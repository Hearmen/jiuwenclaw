# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wrap team spawn_member so remote blank claws receive a bootstrap envelope.

LLM still calls ``spawn_member`` only. For member names listed under
``team.metadata.jiuwen_remote_member_names``, after a successful DB insert we
publish a JSON payload via the normal team ``send_message`` channel so a
teammate process that is already running and subscribed can apply runtime
hints (transport topology, leader id, etc.).

Security: payload intentionally avoids DB credentials; it only mirrors
messager-facing fields already shared for pyzmq coordination.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import types
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# team.metadata.jiuwen_remote_member_names: str | list[str]
_METADATA_REMOTE_NAMES_KEY = "jiuwen_remote_member_names"
_WRAPPED_ATTR = "_jiuwen_spawn_member_remote_bootstrap_wrapped"
_LOCAL_SPAWN_GUARD_ATTR = "_jiuwen_distributed_local_spawn_guard_attached"
_SEND_MESSAGE_GUARDED_ATTR = "_jiuwen_distributed_send_message_guarded"
_ACK_LISTENER_ATTR = "_jiuwen_remote_bootstrap_ack_listener_attached"
_TEAMMATE_BOOTSTRAP_LISTENER_ATTR = "_jiuwen_remote_teammate_bootstrap_listener_attached"
_METADATA_REMOTE_ALL_KEY = "jiuwen_remote_all_spawn_members"
_A2X_RESERVATIONS_ATTR = "_jiuwen_a2x_blank_agent_reservations"

# Remote claw → leader: JSON body on a normal team P2P message (DB + MESSAGE topic).
REMOTE_BOOTSTRAP_ACK_TYPE = "jiuwen.remote_bootstrap_ack"
REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE = "jiuwen.remote_teammate_bootstrap.direct"
_TRANSPORT_BOOTSTRAP_DIRECT_ADDR_KEY = "bootstrap_direct_addr"
_TRANSPORT_BOOTSTRAP_KNOWN_PEERS_KEY = "bootstrap_known_peers"


def remote_member_names(config_base: dict[str, Any] | None = None) -> set[str]:
    """Member slugs treated as externally-hosted teammates (post-spawn bootstrap)."""
    if config_base is None:
        from jiuwenclaw.config import get_config as _get_config

        config_base = _get_config()
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    meta = team.get("metadata") if isinstance(team.get("metadata"), dict) else {}
    raw = meta.get(_METADATA_REMOTE_NAMES_KEY)
    if raw is None:
        return set()
    if isinstance(raw, str) and raw.strip():
        return {raw.strip()}
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def remote_all_spawn_members(config_base: dict[str, Any] | None = None) -> bool:
    """Whether distributed leader treats every ``spawn_member`` as remote."""
    if config_base is None:
        from jiuwenclaw.config import get_config as _get_config

        config_base = _get_config()
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    runtime_mode = str(runtime.get("mode", "")).strip().lower()
    if runtime_mode == "distributed":
        # In distributed mode, prefer remote takeover by default.
        raw = team.get("metadata") if isinstance(team.get("metadata"), dict) else {}
        if isinstance(raw, dict) and _METADATA_REMOTE_ALL_KEY in raw:
            return bool(raw.get(_METADATA_REMOTE_ALL_KEY))
        return True
    return False


def _is_distributed_leader_runtime(config_base: dict[str, Any]) -> bool:
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    mode = str(runtime.get("mode", "")).strip().lower()
    role = str(runtime.get("role", "")).strip().lower()
    return mode == "distributed" and role == "leader"


def _spawn_member_tool_id(leader_deep_agent: Any) -> str:
    """Resolve qualified tool id (inprocess mode rewrites card ids)."""
    try:
        for card in leader_deep_agent.ability_manager.list() or []:
            cid = getattr(card, "id", "") or ""
            if cid == "team.spawn_member" or cid.startswith("team.spawn_member."):
                return cid
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] spawn_member tool id resolve failed: %s", exc)
    return "team.spawn_member"


def _team_tool_id(leader_deep_agent: Any, tool_name: str) -> str:
    """Resolve a registered team tool id by public tool name."""
    expected = f"team.{tool_name}"
    try:
        for card in leader_deep_agent.ability_manager.list() or []:
            cid = getattr(card, "id", "") or ""
            name = getattr(card, "name", "") or ""
            if cid == expected or cid.startswith(f"{expected}.") or name == tool_name:
                return cid or expected
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] %s tool id resolve failed: %s", tool_name, exc)
    return expected


def _is_distributed_leader_runtime(config_base: dict[str, Any]) -> bool:
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    mode = str(runtime.get("mode", "")).strip().lower()
    role = str(runtime.get("role", "")).strip().lower()
    return mode == "distributed" and role == "leader"


def _messager_bootstrap_dict(team_agent: Any) -> dict[str, Any]:
    ctx = team_agent.runtime_context
    if ctx is None or ctx.messager_config is None:
        return {}
    try:
        return ctx.messager_config.model_dump(mode="json")
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] messager_config dump failed: %s", exc)
        return {}


def _transport_params_from_config(config_base: dict[str, Any] | None = None) -> dict[str, Any]:
    if config_base is None:
        from jiuwenclaw.config import get_config as _get_config

        config_base = _get_config()
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    transport = team.get("transport") if isinstance(team.get("transport"), dict) else {}
    params = transport.get("params") if isinstance(transport.get("params"), dict) else {}
    return params if isinstance(params, dict) else {}


def _resolve_bootstrap_peer_for_member(member_name: str, config_base: dict[str, Any] | None = None) -> tuple[str, str]:
    """Resolve (agent_id, addr) for bootstrap control-plane message."""
    params = _transport_params_from_config(config_base)
    requested = str(member_name or "").strip()

    def _iter_peers(key: str) -> list[dict[str, Any]]:
        raw = params.get(key)
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        return []

    def _pick(peers: list[dict[str, Any]]) -> tuple[str, str]:
        if not peers:
            return "", ""
        for peer in peers:
            alias = str(peer.get("member_name", "")).strip()
            if requested and alias and alias == requested:
                agent_id = str(peer.get("agent_id", "")).strip()
                addrs = peer.get("addrs") if isinstance(peer.get("addrs"), list) else []
                addr = _normalize_leader_direct_addr(addrs[0]) if addrs else ""
                if agent_id and addr:
                    return agent_id, addr
        for peer in peers:
            agent_id = str(peer.get("agent_id", "")).strip()
            addrs = peer.get("addrs") if isinstance(peer.get("addrs"), list) else []
            addr = _normalize_leader_direct_addr(addrs[0]) if addrs else ""
            if requested and requested == agent_id and addr:
                return agent_id, addr
        first = peers[0]
        agent_id = str(first.get("agent_id", "")).strip()
        addrs = first.get("addrs") if isinstance(first.get("addrs"), list) else []
        addr = _normalize_leader_direct_addr(addrs[0]) if addrs else ""
        return (agent_id, addr) if agent_id and addr else ("", "")

    agent_id, addr = _pick(_iter_peers(_TRANSPORT_BOOTSTRAP_KNOWN_PEERS_KEY))
    if agent_id and addr:
        return agent_id, addr
    return _pick(_iter_peers("known_peers"))


def _normalize_leader_agent_id(raw: Any, *, team_name: str, leader_member_name: str) -> str:
    """Return a non-empty, stable leader peer id for teammate route registration."""
    value = str(raw or "").strip()
    if value and value.lower() not in {"none", "null"}:
        return value
    tname = str(team_name or "").strip() or "jiuwen_team"
    lname = str(leader_member_name or "").strip() or "team_leader"
    return f"{tname}_{lname}"


def _normalize_leader_direct_addr(raw: Any) -> str:
    """Normalize leader direct addr to a connectable host for remote teammate."""
    value = str(raw or "").strip()
    if not value:
        return ""
    # direct_addr is often configured as bind addr 0.0.0.0; peers must dial a real host.
    return re.sub(r"^tcp://0\.0\.0\.0(?=[:/]|$)", "tcp://127.0.0.1", value)


def build_bootstrap_ack_envelope(
    *,
    member_name: str,
    team_name: str | None = None,
    leader_agent_id: str | None = None,
    leader_direct_addr: str | None = None,
    handshake_applied: bool | None = None,
    version: int = 1,
) -> dict[str, Any]:
    """Payload for a teammate→leader message after remote bootstrap is applied (optional team_name)."""
    body: dict[str, Any] = {
        "type": REMOTE_BOOTSTRAP_ACK_TYPE,
        "version": version,
        "member_name": member_name.strip(),
    }
    if team_name:
        body["team_name"] = team_name
    if leader_agent_id:
        body["leader_agent_id"] = leader_agent_id
    if leader_direct_addr:
        body["leader_direct_addr"] = leader_direct_addr
    if isinstance(handshake_applied, bool):
        body["handshake_applied"] = handshake_applied
    return body


def parse_remote_bootstrap_ack_json(content: str) -> dict[str, Any] | None:
    """If ``content`` is a valid ACK JSON envelope, return the dict; else None."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("type") != REMOTE_BOOTSTRAP_ACK_TYPE:
        return None
    if int(data.get("version", 1)) != 1:
        return None
    mn = data.get("member_name")
    if not isinstance(mn, str) or not mn.strip():
        return None
    return data


def parse_remote_teammate_bootstrap_json(content: str) -> dict[str, Any] | None:
    """If ``content`` is a valid teammate-bootstrap envelope, return dict; else None."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        data = json.loads(content.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("type") != "jiuwen.remote_teammate_bootstrap":
        return None
    if int(data.get("version", 1)) != 1:
        return None
    member_name = data.get("member_name")
    if not isinstance(member_name, str) or not member_name.strip():
        return None
    return data


def build_bootstrap_envelope(
    team_agent: Any,
    *,
    member_name: str,
    prompt: str | None,
) -> dict[str, Any]:
    spec = team_agent.spec
    ctx = team_agent.runtime_context
    team_spec = ctx.team_spec if ctx else None
    team_name = (team_spec.team_name if team_spec else None) or (spec.team_name if spec else "")
    leader_member_name = (team_spec.leader_member_name if team_spec else None) or (
        spec.leader.member_name if spec and spec.leader else None
    )
    messager = _messager_bootstrap_dict(team_agent)
    leader_agent_id = _normalize_leader_agent_id(
        messager.get("node_id"),
        team_name=team_name,
        leader_member_name=str(leader_member_name or ""),
    )
    leader_direct_addr = _normalize_leader_direct_addr(messager.get("direct_addr"))
    return {
        "type": "jiuwen.remote_teammate_bootstrap",
        "version": 1,
        "bootstrap_id": str(uuid.uuid4()),
        "team_name": team_name,
        "session_id": _session_id_from_team_name(team_name),
        "member_name": member_name,
        "leader_member_name": leader_member_name,
        "leader_agent_id": leader_agent_id,
        "leader_direct_addr": leader_direct_addr,
        "messager": messager,
        "prompt": prompt or "",
    }


def _apply_leader_route_from_envelope(team_agent: Any, envelope: dict[str, Any]) -> bool:
    """Best-effort dynamic route registration so blank teammate can reply to leader."""
    leader_agent_id = str(envelope.get("leader_agent_id", "")).strip()
    leader_direct_addr = _normalize_leader_direct_addr(envelope.get("leader_direct_addr"))
    if (
        not leader_agent_id
        or leader_agent_id.lower() in {"none", "null"}
        or not leader_direct_addr
    ):
        return False
    messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
    register = getattr(messager, "register_peer", None)
    if not callable(register):
        return False
    try:
        from openjiuwen.agent_teams.messager.base import MessagerPeerConfig

        register(MessagerPeerConfig(agent_id=leader_agent_id, addrs=[leader_direct_addr]))
        logger.info(
            "[RemoteMemberBootstrap] teammate applied leader route agent_id=%s addr=%s",
            leader_agent_id,
            leader_direct_addr,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate failed to apply leader route agent_id=%s addr=%s: %s",
            leader_agent_id,
            leader_direct_addr,
            exc,
        )
        return False


async def _send_bootstrap_message(team_agent: Any, member_name: str, prompt: str | None) -> bool:
    mm = team_agent.message_manager
    messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
    if mm is None:
        logger.warning(
            "[RemoteMemberBootstrap] no message_manager; cannot notify member=%s",
            member_name,
        )
        return False
    envelope = build_bootstrap_envelope(team_agent, member_name=member_name, prompt=prompt)
    registry_reservation = None
    peer_agent_id = ""
    peer_addr = ""
    try:
        from jiuwenclaw.config import get_config as _get_config
        from jiuwenclaw.agentserver.a2x_registry_runtime import reserve_blank_teammate_agent

        registry_reservation = await reserve_blank_teammate_agent(
            _get_config(),
            source="leader-spawn-member",
        )
        if registry_reservation is not None:
            peer_agent_id = registry_reservation.service_id
            peer_addr = _normalize_leader_direct_addr(registry_reservation.endpoint)
    except Exception as exc:
        logger.warning("[RemoteMemberBootstrap] A2X blank teammate reservation failed: %s", exc)

    if not peer_agent_id or not peer_addr:
        peer_agent_id, peer_addr = _resolve_bootstrap_peer_for_member(member_name)

    direct_sent = False
    if messager is not None and peer_agent_id and peer_addr:
        try:
            from openjiuwen.agent_teams.messager.base import MessagerPeerConfig
            from openjiuwen.agent_teams.schema.events import EventMessage

            register = getattr(messager, "register_peer", None)
            send = getattr(messager, "send", None)
            if callable(register) and callable(send):
                register(MessagerPeerConfig(agent_id=peer_agent_id, addrs=[peer_addr]))
                control_event = EventMessage(
                    event_type=REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE,
                    payload={"envelope": envelope},
                    sender_id=str(envelope.get("leader_member_name") or ""),
                )
                await send(peer_agent_id, control_event)
                direct_sent = True
                logger.info(
                    "[RemoteMemberBootstrap] pushed bootstrap via direct control-plane member=%s "
                    "peer_agent_id=%s peer_addr=%s bootstrap_id=%s",
                    member_name,
                    peer_agent_id,
                    peer_addr,
                    envelope.get("bootstrap_id"),
                )
        except Exception as exc:
            logger.warning(
                "[RemoteMemberBootstrap] direct bootstrap send failed member=%s peer_agent_id=%s peer_addr=%s: %s",
                member_name,
                peer_agent_id,
                peer_addr,
                exc,
            )
    if direct_sent:
        if registry_reservation is not None:
            logger.info(
                "[RemoteMemberBootstrap] keeping A2X reservation after bootstrap "
                "member=%s service_id=%s endpoint=%s",
                member_name,
                registry_reservation.service_id,
                registry_reservation.endpoint,
            )
            _remember_a2x_reservation(team_agent, member_name, registry_reservation)
        return True

    if registry_reservation is not None:
        logger.warning(
            "[RemoteMemberBootstrap] releasing A2X reservation after bootstrap delivery failure "
            "member=%s service_id=%s endpoint=%s",
            member_name,
            registry_reservation.service_id,
            registry_reservation.endpoint,
        )
        await registry_reservation.release()
        await registry_reservation.close()

    logger.warning(
        "[RemoteMemberBootstrap] direct bootstrap not delivered; DB fallback disabled "
        "member=%s peer_agent_id=%s peer_addr=%s has_messager=%s",
        member_name,
        peer_agent_id,
        peer_addr,
        bool(messager is not None),
    )
    return False


def _remember_a2x_reservation(team_agent: Any, member_name: str, reservation: Any) -> None:
    reservations = getattr(team_agent, _A2X_RESERVATIONS_ATTR, None)
    if not isinstance(reservations, list):
        reservations = []
        setattr(team_agent, _A2X_RESERVATIONS_ATTR, reservations)
    reservations.append((member_name, reservation))


async def release_a2x_reservations_for_team(team_agent: Any) -> None:
    """Release leader-held blank-agent reservations when the team is dissolved."""
    reservations = getattr(team_agent, _A2X_RESERVATIONS_ATTR, None)
    if not isinstance(reservations, list) or not reservations:
        return
    setattr(team_agent, _A2X_RESERVATIONS_ATTR, [])
    for member_name, reservation in reservations:
        try:
            await reservation.release()
            logger.info(
                "[RemoteMemberBootstrap] released A2X reservation on team destroy "
                "member=%s service_id=%s endpoint=%s",
                member_name,
                getattr(reservation, "service_id", ""),
                getattr(reservation, "endpoint", ""),
            )
        except Exception as exc:
            logger.warning(
                "[RemoteMemberBootstrap] A2X reservation release on team destroy failed "
                "member=%s: %s",
                member_name,
                exc,
            )
        finally:
            close = getattr(reservation, "close", None)
            if callable(close):
                await close()


def attach_spawn_member_remote_bootstrap_wrapper(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None,
) -> None:
    """Monkey-patch SpawnMemberTool.invoke on the leader's registered tool instance."""
    from jiuwenclaw.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip spawn_member wrapper")
        return

    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    leader = team_agent.deep_agent
    if leader is None:
        return

    tool_id = _spawn_member_tool_id(leader)
    tag = getattr(getattr(leader, "card", None), "id", None)
    tool = Runner.resource_mgr.get_tool(tool_id, tag=tag) if tag else None
    if tool is None:
        tool = Runner.resource_mgr.get_tool(tool_id)
    if tool is None:
        logger.debug(
            "[RemoteMemberBootstrap] tool %s not in Runner.resource_mgr (session_id=%s channel=%s)",
            tool_id,
            session_id,
            channel_id,
        )
        return
    if getattr(tool, _WRAPPED_ATTR, False):
        return
    remote_names = remote_member_names(config_base)
    remote_all = remote_all_spawn_members(config_base)
    if not remote_names and not remote_all:
        logger.debug("[RemoteMemberBootstrap] no jiuwen_remote_member_names; skip wrapper")
        return

    orig_invoke = tool.invoke

    async def wrapped_invoke(self: Any, inputs: dict[str, Any], **kwargs: Any) -> Any:
        result = await orig_invoke(inputs, **kwargs)
        try:
            ok = bool(getattr(result, "success", False))
            if not ok:
                logger.info(
                    "[RemoteMemberBootstrap] spawn_member result not success; skip remote bootstrap: inputs=%s",
                    inputs,
                )
                return result
            mname = (inputs or {}).get("member_name")
            if not isinstance(mname, str):
                logger.info(
                    "[RemoteMemberBootstrap] spawn_member missing member_name(str); skip remote bootstrap: inputs=%s",
                    inputs,
                )
                return result
            key = mname.strip()
            if (not remote_all) and key not in remote_names:
                logger.info(
                    "[RemoteMemberBootstrap] spawn_member member=%s not remote target; no remote claw bootstrap "
                    "(remote_all=%s remote_names=%s)",
                    key,
                    remote_all,
                    sorted(remote_names),
                )
                return result
            logger.info(
                "[RemoteMemberBootstrap] spawn_member member=%s entering remote claw bootstrap path "
                "(remote_all=%s remote_names=%s)",
                key,
                remote_all,
                sorted(remote_names),
            )
            # openjiuwen native spawn path may mark member as READY immediately.
            # For remote teammates, force it back to UNSTARTED and wait for ACK to set READY.
            try:
                from openjiuwen.agent_teams.schema.status import MemberStatus

                tb = getattr(team_agent, "team_backend", None)
                db = getattr(tb, "db", None) if tb is not None else None
                _tn = getattr(team_agent, "_team_name", None)
                team_name = _tn() if callable(_tn) else None
                if db is not None and isinstance(team_name, str) and team_name.strip():
                    await db.update_member_status(key, team_name, MemberStatus.UNSTARTED.value)
                    logger.info(
                        "[RemoteMemberBootstrap] spawn_member member=%s status forced to UNSTARTED "
                        "until remote ACK team=%s",
                        key,
                        team_name,
                    )
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] failed to force UNSTARTED before bootstrap member=%s: %s",
                    key,
                    exc,
                )
            delivered = await _send_bootstrap_message(team_agent, key, (inputs or {}).get("prompt"))
            if delivered:
                try:
                    from openjiuwen.agent_teams.schema.status import MemberStatus

                    tb = getattr(team_agent, "team_backend", None)
                    db = getattr(tb, "db", None) if tb is not None else None
                    _tn = getattr(team_agent, "_team_name", None)
                    team_name = _tn() if callable(_tn) else None
                    if db is not None and isinstance(team_name, str) and team_name.strip():
                        ok = await db.update_member_status(key, team_name, MemberStatus.READY.value)
                        if ok:
                            logger.info(
                                "[RemoteMemberBootstrap] direct ACK applied member=%s team=%s -> status=ready",
                                key,
                                team_name,
                            )
                        else:
                            logger.warning(
                                "[RemoteMemberBootstrap] direct ACK update_member_status failed member=%s team=%s",
                                key,
                                team_name,
                            )
                except Exception as exc:
                    logger.warning(
                        "[RemoteMemberBootstrap] direct ACK status update failed member=%s: %s",
                        key,
                        exc,
                    )
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] post-spawn hook failed: %s", exc)
        return result

    tool.invoke = types.MethodType(wrapped_invoke, tool)
    setattr(tool, _WRAPPED_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] attached spawn_member wrapper tool_id=%s remote=%s remote_all=%s",
        tool_id,
        sorted(remote_names),
        remote_all,
    )


def attach_distributed_local_spawn_guard(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None,
) -> None:
    """Disable leader-side teammate startup when teammates are remote-managed.

    Some agent-core versions accept ``spawn_mode=distributed`` in config but still
    wire ``send_message`` auto-start to local ``spawn_teammate``. In distributed
    leader mode, jiuwenclaw owns remote bootstrap, so local teammate creation must
    be suppressed at the adapter layer.
    """
    from jiuwenclaw.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip local spawn guard")
        return

    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    if getattr(team_agent, _LOCAL_SPAWN_GUARD_ATTR, False):
        return

    leader = team_agent.deep_agent
    if leader is None:
        logger.debug("[RemoteMemberBootstrap] skip local spawn guard: missing leader deep_agent")
        return

    tool_id = _team_tool_id(leader, "send_message")
    tag = getattr(getattr(leader, "card", None), "id", None)
    tool = Runner.resource_mgr.get_tool(tool_id, tag=tag) if tag else None
    if tool is None:
        tool = Runner.resource_mgr.get_tool(tool_id)
    if tool is None:
        logger.warning(
            "[RemoteMemberBootstrap] distributed local spawn guard could not find send_message tool "
            "tool_id=%s session_id=%s channel=%s",
            tool_id,
            session_id,
            channel_id,
        )
    elif not getattr(tool, _SEND_MESSAGE_GUARDED_ATTR, False):
        if hasattr(tool, "_on_teammate_created"):
            setattr(tool, "_on_teammate_created", None)
            setattr(tool, _SEND_MESSAGE_GUARDED_ATTR, True)
            logger.info(
                "[RemoteMemberBootstrap] distributed local spawn guard disabled send_message auto-start "
                "tool_id=%s session_id=%s channel=%s",
                tool_id,
                session_id,
                channel_id,
            )
        else:
            logger.warning(
                "[RemoteMemberBootstrap] send_message tool has no _on_teammate_created field "
                "tool_id=%s type=%s",
                tool_id,
                type(tool).__name__,
            )

    original_spawn_teammate = getattr(team_agent, "spawn_teammate", None)
    if callable(original_spawn_teammate):

        async def _skip_local_spawn_teammate(self: Any, ctx: Any, *args: Any, **kwargs: Any) -> None:
            member_name = getattr(ctx, "member_name", None)
            logger.info(
                "[RemoteMemberBootstrap] distributed local spawn guard skipped local spawn_teammate "
                "member=%s session_id=%s channel=%s",
                member_name,
                session_id,
                channel_id,
            )
            return None

        setattr(team_agent, "_jiuwen_original_spawn_teammate", original_spawn_teammate)
        team_agent.spawn_teammate = types.MethodType(_skip_local_spawn_teammate, team_agent)
    else:
        logger.warning("[RemoteMemberBootstrap] team_agent has no callable spawn_teammate to guard")

    setattr(team_agent, _LOCAL_SPAWN_GUARD_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] distributed local spawn guard attached session_id=%s channel=%s",
        session_id,
        channel_id,
    )


def attach_remote_bootstrap_ack_listener(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None = None,
) -> None:
    """Leader: on MESSAGE transport events, detect ACK JSON and set member UNSTARTED→READY in DB.

    The published :class:`MessageEvent` has no body; we load content via ``db.get_message``,
    then ``mark_message_read`` so the leader LLM is not fed the control payload.
    """
    from jiuwenclaw.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_leader_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed leader runtime; skip ACK listener")
        return

    from openjiuwen.agent_teams.schema.events import TeamEvent
    from openjiuwen.agent_teams.schema.status import MemberStatus
    from openjiuwen.agent_teams.schema.team import TeamRole

    _ = session_id
    _ = channel_id

    if getattr(team_agent, "role", None) != TeamRole.LEADER:
        return
    if getattr(team_agent, _ACK_LISTENER_ATTR, False):
        return
    tb = getattr(team_agent, "team_backend", None)
    mm = getattr(team_agent, "message_manager", None)
    if tb is None or mm is None or getattr(tb, "db", None) is None:
        logger.debug(
            "[RemoteMemberBootstrap] skip ACK listener: missing team_backend.db or message_manager",
        )
        return
    remote_names = remote_member_names(config_base)
    remote_all = remote_all_spawn_members(config_base)
    if not remote_names and not remote_all:
        logger.debug("[RemoteMemberBootstrap] no jiuwen_remote_member_names; skip ACK listener")
        return

    async def on_event(event: Any) -> None:
        if getattr(event, "event_type", None) != TeamEvent.MESSAGE:
            return
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            return
        to_name = payload.get("to_member_name")
        from_name = payload.get("from_member_name")
        message_id = payload.get("message_id")
        _mn = getattr(team_agent, "_member_name", None)
        leader_name = _mn() if callable(_mn) else None
        if not leader_name or to_name != leader_name:
            return
        if not isinstance(from_name, str):
            return
        sender = from_name.strip()
        if not sender:
            return
        if (not remote_all) and sender not in remote_names:
            return
        if not isinstance(message_id, str) or not message_id:
            return

        row = await tb.db.get_message(message_id)
        if row is None:
            logger.debug("[RemoteMemberBootstrap] ACK: no row for message_id=%s", message_id)
            return
        if getattr(row, "from_member_name", None) != from_name or getattr(row, "to_member_name", None) != leader_name:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: DB row sender/recipient mismatch id=%s",
                message_id,
            )
            return

        ack = parse_remote_bootstrap_ack_json(getattr(row, "content", "") or "")
        if ack is None:
            return
        ack_member = str(ack.get("member_name", "")).strip()
        if ack_member != sender:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: member_name != sender for id=%s",
                message_id,
            )
            return
        _tn = getattr(team_agent, "_team_name", None)
        team_name = _tn() if callable(_tn) else None
        if not team_name:
            logger.warning("[RemoteMemberBootstrap] ACK: leader has no team_name")
            return
        ack_team = ack.get("team_name")
        if ack_team and str(ack_team) != str(team_name):
            logger.warning(
                "[RemoteMemberBootstrap] ACK: team_name mismatch db=%s ack=%s",
                team_name,
                ack_team,
            )
            return
        ack_applied = ack.get("handshake_applied")
        if isinstance(ack_applied, bool) and not ack_applied:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: teammate reports handshake route not applied member=%s id=%s",
                ack_member,
                message_id,
            )

        ok = await tb.db.update_member_status(ack_member, team_name, MemberStatus.READY.value)
        if not ok:
            logger.warning(
                "[RemoteMemberBootstrap] ACK: update_member_status failed member=%s team=%s",
                ack_member,
                team_name,
            )
            return
        try:
            await mm.mark_message_read(message_id, leader_name)
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] ACK: mark_message_read failed: %s", exc)
        logger.info(
            "[RemoteMemberBootstrap] ACK applied member=%s team=%s message_id=%s -> status=ready "
            "(ready is set by ACK listener, not directly by spawn_member)",
            ack_member,
            team_name,
            message_id,
        )

    team_agent.add_event_listener(on_event)
    setattr(team_agent, _ACK_LISTENER_ATTR, True)
    logger.info(
        "[RemoteMemberBootstrap] attached remote bootstrap ACK listener remote=%s remote_all=%s",
        sorted(remote_names),
        remote_all,
    )


def _set_obj_member_name(obj: Any, member_name: str) -> None:
    """Best-effort mutation helper for member-name fields on runtime objects."""
    for attr in ("member_name", "_member_name"):
        if hasattr(obj, attr):
            try:
                setattr(obj, attr, member_name)
            except Exception as exc:
                logger.debug(
                    "[RemoteMemberBootstrap] failed to set %s on %s: %s",
                    attr,
                    type(obj).__name__,
                    exc,
                )


def _adopt_teammate_member_name(team_agent: Any, member_name: str) -> None:
    """Switch teammate runtime identity to the spawned remote member name."""
    target = member_name.strip()
    if not target:
        return
    ctx = getattr(team_agent, "_ctx", None)
    if ctx is not None and hasattr(ctx, "member_name"):
        try:
            ctx.member_name = target
        except Exception as exc:
            logger.debug("[RemoteMemberBootstrap] failed to update runtime member_name: %s", exc)
        mc = getattr(ctx, "messager_config", None)
        if mc is not None and hasattr(mc, "node_id"):
            try:
                mc.node_id = target
            except Exception as exc:
                logger.debug("[RemoteMemberBootstrap] failed to update messager node_id: %s", exc)
    for obj_name in ("_team_backend", "_team_member"):
        obj = getattr(team_agent, obj_name, None)
        if obj is not None:
            _set_obj_member_name(obj, target)


def _is_distributed_teammate_runtime(config_base: dict[str, Any]) -> bool:
    team = config_base.get("team") if isinstance(config_base.get("team"), dict) else {}
    runtime = team.get("runtime") if isinstance(team.get("runtime"), dict) else {}
    mode = str(runtime.get("mode", "")).strip().lower()
    role = str(runtime.get("role", "")).strip().lower()
    return mode == "distributed" and role == "teammate"


def _session_id_from_team_name(team_name: str) -> str:
    name = str(team_name or "").strip()
    if "_sess_" not in name:
        return ""
    return "sess_" + name.split("_sess_", 1)[1]


async def _ensure_dynamic_member_execution_loop(
    *,
    session_id: str,
    target_member: str,
    channel_id: str = "default",
    leader_agent_id: str = "",
    leader_direct_addr: str = "",
) -> tuple[bool, bool]:
    """Best-effort bootstrap for teammate runtime loop after dynamic member takeover."""
    sid = str(session_id or "").strip()
    member = str(target_member or "").strip()
    if not sid or not member:
        return False, False
    try:
        from openjiuwen.agent_teams.agent.team_agent import TeamAgent
        from openjiuwen.agent_teams.spawn.context import reset_session_id, set_session_id

        from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
        from jiuwenclaw.agentserver.team.team_manager import get_team_manager

        server = AgentWebSocketServer.get_instance()
        agent_manager = server.get_agent_manager()
        agent = agent_manager.get_agent_nowait(channel_id) or await agent_manager.get_agent(channel_id, "agent")
        if agent is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: agent unavailable channel=%s session_id=%s",
                channel_id,
                sid,
            )
            return False, False
        deep_agent = agent.get_instance()
        if deep_agent is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: deep_agent unavailable session_id=%s",
                sid,
            )
            return False, False

        team_manager = get_team_manager(channel_id)
        leader_team_agent = await team_manager.get_or_create_team(
            sid,
            deep_agent,
            channel_id=channel_id,
        )
        # Build a real TEAMMATE runtime context for the adopted member, instead
        # of using TeamManager.interact() (which drives the leader context).
        build_ctx = getattr(leader_team_agent, "_build_context_from_db", None)
        if not callable(build_ctx):
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: _build_context_from_db unavailable "
                "session_id=%s member=%s",
                sid,
                member,
            )
            return False, False
        teammate_ctx = await build_ctx(member)
        if teammate_ctx is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: teammate context missing "
                "session_id=%s member=%s",
                sid,
                member,
            )
            return False, False
        if getattr(leader_team_agent, "spec", None) is None:
            logger.warning(
                "[RemoteMemberBootstrap] teammate loop start skipped: team spec unavailable session_id=%s",
                sid,
            )
            return False
        payload = {
            "spec": leader_team_agent.spec.model_dump(mode="json"),
            "context": teammate_ctx.model_dump(mode="json"),
        }
        teammate_agent = await TeamAgent.from_spawn_payload(payload)
        route_applied = False
        if leader_agent_id and leader_direct_addr:
            route_applied = _apply_leader_route_from_envelope(
                teammate_agent,
                {
                    "leader_agent_id": leader_agent_id,
                    "leader_direct_addr": leader_direct_addr,
                },
            )
        kickoff = (
            f"[remote bootstrap] teammate adopted member={member}. "
            "Start/continue execution loop for assigned team tasks."
        )
        token = set_session_id(sid)
        try:
            await teammate_agent.invoke({"query": kickoff}, session=None)
        finally:
            reset_session_id(token)
        logger.info(
            "[RemoteMemberBootstrap] teammate execution loop kicked: session_id=%s member=%s channel_id=%s",
            sid,
            member,
            channel_id,
        )
        return True, route_applied
    except Exception as exc:
        logger.warning(
            "[RemoteMemberBootstrap] teammate execution loop kickoff failed: session_id=%s member=%s error=%s",
            sid,
            member,
            exc,
        )
        return False, False


async def _apply_bootstrap_envelope_from_control_plane(
    *,
    processed_ids: set[str],
    loop_kicked_members: set[tuple[str, str]],
    kickoff_tasks: set[asyncio.Task[Any]],
    adopted_member: str,
    envelope: dict[str, Any],
    source_id: str,
) -> str:
    """Process one bootstrap envelope (from DB poll or direct control-plane)."""
    if not isinstance(envelope, dict):
        return adopted_member
    bootstrap_id = str(envelope.get("bootstrap_id", "")).strip() or source_id
    if bootstrap_id in processed_ids:
        return adopted_member
    envelope_team_name = str(envelope.get("team_name", "")).strip()
    envelope_session_id = str(envelope.get("session_id", "")).strip()
    if not envelope_team_name or not envelope_session_id:
        return adopted_member
    target_member = str(envelope.get("member_name", "")).strip()
    if not target_member:
        return adopted_member
    leader_agent_id = str(envelope.get("leader_agent_id", "")).strip()
    leader_direct_addr = str(envelope.get("leader_direct_addr", "")).strip()

    effective_sid = envelope_session_id or _session_id_from_team_name(envelope_team_name)
    loop_key = (effective_sid, target_member)

    logger.info(
        "[RemoteMemberBootstrap] teammate applied bootstrap from control-plane "
        "(ready to send direct ACK to leader transport) team=%s session_id=%s "
        "old_member=%s adopted_member=%s source_id=%s",
        envelope_team_name,
        envelope_session_id,
        adopted_member,
        target_member,
        source_id,
    )
    if effective_sid and loop_key not in loop_kicked_members:
        loop_kicked_members.add(loop_key)

        async def _kickoff_loop() -> None:
            kicked, route_applied = await _ensure_dynamic_member_execution_loop(
                session_id=effective_sid,
                target_member=target_member,
                channel_id="default",
                leader_agent_id=leader_agent_id,
                leader_direct_addr=leader_direct_addr,
            )
            if kicked:
                logger.info(
                    "[RemoteMemberBootstrap] teammate execution kickoff scheduled from bootstrap "
                    "team=%s session_id=%s member=%s handshake_applied=%s",
                    envelope_team_name,
                    effective_sid,
                    target_member,
                    route_applied,
                )
            else:
                logger.warning(
                    "[RemoteMemberBootstrap] teammate execution kickoff failed after direct ACK "
                    "team=%s session_id=%s member=%s",
                    envelope_team_name,
                    effective_sid,
                    target_member,
                )

        kickoff_task = asyncio.create_task(
            _kickoff_loop(),
            name=f"remote-bootstrap-kickoff:{effective_sid}:{target_member}",
        )
        kickoff_tasks.add(kickoff_task)

        def _on_kickoff_done(task: asyncio.Task[Any]) -> None:
            kickoff_tasks.discard(task)
            try:
                task.result()
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] teammate execution kickoff task crashed "
                    "team=%s session_id=%s member=%s error=%s",
                    envelope_team_name,
                    effective_sid,
                    target_member,
                    exc,
                )

        kickoff_task.add_done_callback(_on_kickoff_done)
    processed_ids.add(bootstrap_id)
    return target_member


async def run_teammate_bootstrap_daemon(*, stop_event: asyncio.Event, poll_interval: float = 1.0) -> None:
    """Startup daemon for distributed teammate: consume bootstrap even before team runtime exists."""
    from jiuwenclaw.config import get_config as _get_config

    config = _get_config()
    if not _is_distributed_teammate_runtime(config):
        return
    team_cfg = config.get("team") if isinstance(config.get("team"), dict) else {}
    transport_cfg = team_cfg.get("transport") if isinstance(team_cfg.get("transport"), dict) else {}
    transport_params = transport_cfg.get("params") if isinstance(transport_cfg.get("params"), dict) else {}

    runtime = team_cfg.get("runtime") if isinstance(team_cfg.get("runtime"), dict) else {}
    local_member = str(runtime.get("member_name", "teammate_1")).strip() or "teammate_1"
    adopted_member = local_member
    processed: set[str] = set()
    loop_kicked_members: set[tuple[str, str]] = set()
    kickoff_tasks: set[asyncio.Task[Any]] = set()
    bootstrap_router = None
    zmq_mod = None
    direct_bootstrap_addr = _normalize_leader_direct_addr(
        transport_params.get(_TRANSPORT_BOOTSTRAP_DIRECT_ADDR_KEY)
    )

    if direct_bootstrap_addr:
        try:
            import zmq
            import zmq.asyncio

            zmq_mod = zmq
            ctx = zmq.asyncio.Context.instance()
            bootstrap_router = ctx.socket(zmq.ROUTER)
            bootstrap_router.bind(direct_bootstrap_addr)
            logger.info(
                "[RemoteMemberBootstrap] teammate direct bootstrap listener started addr=%s local_member=%s",
                direct_bootstrap_addr,
                local_member,
            )
            try:
                from jiuwenclaw.agentserver.a2x_registry_runtime import (
                    register_teammate_blank_agent_at_startup,
                )

                await register_teammate_blank_agent_at_startup(
                    config,
                    source="teammate-bootstrap-daemon",
                )
            except Exception as exc:
                logger.warning(
                    "[RemoteMemberBootstrap] teammate startup A2X registration failed: %s",
                    exc,
                )
        except Exception as exc:
            bootstrap_router = None
            logger.warning(
                "[RemoteMemberBootstrap] teammate direct bootstrap listener disabled addr=%s error=%s",
                direct_bootstrap_addr,
                exc,
            )

    logger.info(
        "[RemoteMemberBootstrap] teammate bootstrap daemon started local_member=%s",
        local_member,
    )
    while not stop_event.is_set():
        try:
            if bootstrap_router is not None and zmq_mod is not None:
                for _ in range(64):
                    try:
                        frames = await bootstrap_router.recv_multipart(flags=zmq_mod.NOBLOCK)
                    except zmq_mod.Again:
                        break
                    except Exception as exc:
                        logger.warning("[RemoteMemberBootstrap] direct bootstrap recv failed: %s", exc)
                        break
                    if len(frames) < 2:
                        continue
                    identity, payload = frames[0], frames[-1]
                    try:
                        raw = json.loads(payload.decode("utf-8"))
                    except Exception:
                        await bootstrap_router.send_multipart([identity, b"ok"])
                        continue
                    event_type = str(raw.get("event_type", "")).strip()
                    env = None
                    if event_type == REMOTE_BOOTSTRAP_DIRECT_EVENT_TYPE:
                        payload_obj = raw.get("payload")
                        if isinstance(payload_obj, dict):
                            env = payload_obj.get("envelope")
                    if isinstance(env, dict):
                        source_id = str(env.get("bootstrap_id", "")).strip() or str(uuid.uuid4())
                        adopted_member = await _apply_bootstrap_envelope_from_control_plane(
                            processed_ids=processed,
                            loop_kicked_members=loop_kicked_members,
                            kickoff_tasks=kickoff_tasks,
                            adopted_member=adopted_member,
                            envelope=env,
                            source_id=source_id,
                        )
                    await bootstrap_router.send_multipart([identity, b"ok"])

        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] teammate bootstrap daemon loop error: %s", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(0.2, poll_interval))
        except asyncio.TimeoutError:
            pass

    if bootstrap_router is not None:
        try:
            bootstrap_router.close(linger=0)
        except Exception as exc:
            logger.debug("[RemoteMemberBootstrap] bootstrap router close failed: %s", exc)
    for task in list(kickoff_tasks):
        task.cancel()
    for task in list(kickoff_tasks):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    logger.info("[RemoteMemberBootstrap] teammate bootstrap daemon stopped")


def attach_remote_teammate_bootstrap_listener(
    team_agent: Any,
    *,
    session_id: str,
    channel_id: str | None = None,
) -> None:
    """Teammate: consume remote bootstrap message, adopt member identity, send ACK."""
    from jiuwenclaw.config import get_config as _get_config

    config_base = _get_config()
    if not _is_distributed_teammate_runtime(config_base):
        logger.debug("[RemoteMemberBootstrap] non-distributed teammate runtime; skip teammate listener")
        return

    from openjiuwen.agent_teams.schema.events import TeamEvent
    from openjiuwen.agent_teams.schema.team import TeamRole

    _ = session_id
    _ = channel_id

    if getattr(team_agent, "role", None) != TeamRole.TEAMMATE:
        return
    if getattr(team_agent, _TEAMMATE_BOOTSTRAP_LISTENER_ATTR, False):
        return
    tb = getattr(team_agent, "team_backend", None)
    mm = getattr(team_agent, "message_manager", None)
    if tb is None or mm is None or getattr(tb, "db", None) is None:
        logger.debug(
            "[RemoteMemberBootstrap] skip teammate bootstrap listener: missing team_backend.db or message_manager",
        )
        return

    async def on_event(event: Any) -> None:
        if getattr(event, "event_type", None) != TeamEvent.MESSAGE:
            return
        payload = getattr(event, "payload", None) or {}
        if not isinstance(payload, dict):
            return
        message_id = payload.get("message_id")
        from_name = payload.get("from_member_name")
        to_name = payload.get("to_member_name")
        if not isinstance(message_id, str) or not message_id:
            return
        if not isinstance(from_name, str) or not from_name.strip():
            return
        if not isinstance(to_name, str) or not to_name.strip():
            return

        row = await tb.db.get_message(message_id)
        if row is None:
            return
        envelope = parse_remote_teammate_bootstrap_json(getattr(row, "content", "") or "")
        if envelope is None:
            return

        target_member = str(envelope.get("member_name", "")).strip()
        leader_member = str(envelope.get("leader_member_name", "")).strip() or from_name.strip()
        if not target_member:
            return
        if to_name.strip() != target_member:
            return

        old_member = getattr(team_agent, "_member_name", None)
        old_member_name = old_member() if callable(old_member) else None
        _adopt_teammate_member_name(team_agent, target_member)
        route_applied = _apply_leader_route_from_envelope(team_agent, envelope)

        try:
            if old_member_name:
                await mm.mark_message_read(message_id, old_member_name)
            await mm.mark_message_read(message_id, target_member)
        except Exception as exc:
            logger.warning("[RemoteMemberBootstrap] teammate mark_message_read failed: %s", exc)

        _tn = getattr(team_agent, "_team_name", None)
        team_name = _tn() if callable(_tn) else None
        ack = build_bootstrap_ack_envelope(
            member_name=target_member,
            team_name=team_name,
            leader_agent_id=str(envelope.get("leader_agent_id", "")).strip(),
            leader_direct_addr=str(envelope.get("leader_direct_addr", "")).strip(),
            handshake_applied=route_applied,
        )
        ack_id = await mm.send_message(
            content=json.dumps(ack, ensure_ascii=False),
            to_member_name=leader_member,
        )
        logger.info(
            "[RemoteMemberBootstrap] teammate bootstrap consumed message_id=%s old_member=%s adopted_member=%s "
            "leader=%s ack_message_id=%s",
            message_id,
            old_member_name,
            target_member,
            leader_member,
            ack_id,
        )

    team_agent.add_event_listener(on_event)
    setattr(team_agent, _TEAMMATE_BOOTSTRAP_LISTENER_ATTR, True)
    logger.info("[RemoteMemberBootstrap] attached teammate bootstrap listener")
