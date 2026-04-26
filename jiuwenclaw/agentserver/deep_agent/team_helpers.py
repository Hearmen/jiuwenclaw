# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team agent streaming helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from openjiuwen.core.runner import Runner
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.team import get_team_manager
from jiuwenclaw.agentserver.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.agentserver.stream_utils import parse_stream_chunk
from jiuwenclaw.schema.agent import AgentResponseChunk

logger = logging.getLogger(__name__)

_pending_waiters: dict[tuple[str, str], list[tuple[str, asyncio.Queue]]] = {}


def _resolve_channel_id(channel_id: str | None) -> str:
    return str(channel_id or "default").strip() or "default"


def _waiter_key(channel_id: str | None, session_id: str) -> tuple[str, str]:
    return _resolve_channel_id(channel_id), session_id


async def _drain_team_skill_approval_events(
    channel_id: str | None,
    session_id: str,
) -> None:
    """Drain buffered TeamSkillRail approval events and broadcast them."""
    try:
        tm = get_team_manager()
        for evt in await tm.drain_team_skill_events(session_id):
            parsed = parse_stream_chunk(evt)
            if parsed is not None:
                _broadcast_event(channel_id, session_id, parsed)
    except Exception as exc:
        logger.warning("[TeamHelpers] drain team skill events failed: %s", exc)


def _broadcast_event(
    channel_id: str | None, session_id: str, event: dict[str, Any]
) -> None:
    """Broadcast an event to all request queues waiting on the same channel/session."""
    waiter_key = _waiter_key(channel_id, session_id)
    waiters = _pending_waiters.get(waiter_key, [])
    for request_id, queue in waiters:
        try:
            queue.put_nowait(dict(event))
        except Exception:
            logger.debug(
                "[TeamHelpers] broadcast failed: channel_id=%s session_id=%s request_id=%s",
                waiter_key[0],
                session_id,
                request_id,
            )


async def process_team_message_stream(
    request: Any,
    inputs: dict[str, Any],
    deep_agent: DeepAgent,
) -> AsyncIterator[AgentResponseChunk]:
    """Process a team-mode streaming request."""
    session_id = request.session_id or "default"
    rid = request.request_id
    channel_id = request.channel_id

    team_manager = get_team_manager(channel_id)

    try:
        if deep_agent is None:
            raise RuntimeError("DeepAgent not initialized")

        team_agent = await team_manager.get_or_create_team(
            session_id=session_id,
            deep_agent=deep_agent,
            request_id=rid,
            channel_id=channel_id,
            request_metadata=request.metadata,
        )
    except Exception as exc:
        logger.exception("[TeamHelpers] TeamAgent create failed: %s", exc)
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload={"event_type": "chat.error", "error": str(exc)},
            is_complete=False,
        )
        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=None,
            is_complete=True,
        )
        return

    query = inputs.get("query", "")
    is_first_request = not team_manager.has_stream_task(session_id)
    request_queue: asyncio.Queue | None = None

    try:
        if is_first_request:
            request_queue = asyncio.Queue()
            waiter_key = _waiter_key(channel_id, session_id)
            if waiter_key not in _pending_waiters:
                _pending_waiters[waiter_key] = []
            _pending_waiters[waiter_key].append((rid, request_queue))
            logger.info(
                "[TeamHelpers] first team request: channel_id=%s session_id=%s",
                waiter_key[0],
                session_id,
            )

            monitor_handler = TeamMonitorHandler(team_agent, session_id)
            try:
                await monitor_handler.start()
                team_manager.register_monitor(session_id, monitor_handler)
                logger.info(
                    "[TeamHelpers] Monitor started: channel_id=%s session_id=%s",
                    waiter_key[0],
                    session_id,
                )
            except Exception as exc:
                logger.warning(
                    "[TeamHelpers] Monitor start failed, continue without it: %s", exc
                )

            stream_task = asyncio.create_task(
                _consume_stream_with_query(
                    channel_id,
                    session_id,
                    team_agent,
                    query,
                )
            )
            team_manager.register_stream_task(session_id, stream_task)

            if monitor_handler.is_running:
                asyncio.create_task(
                    _consume_monitor_events(
                        channel_id,
                        session_id,
                        monitor_handler,
                    )
                )
        else:
            logger.info(
                "[TeamHelpers] follow-up team request: channel_id=%s session_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
            )
            if query:
                success = await team_manager.interact(session_id, query)
                if not success:
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload={
                            "event_type": "chat.error",
                            "error": "interact failed",
                        },
                        is_complete=False,
                    )
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload=None,
                        is_complete=True,
                    )
                    return

            logger.info(
                "[TeamHelpers] follow-up request submitted without waiter: channel_id=%s session_id=%s request_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
                rid,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=channel_id,
                payload=None,
                is_complete=True,
            )
            return

        try:
            while team_manager.has_stream_task(session_id):
                if request_queue is None:
                    break
                try:
                    event = await asyncio.wait_for(request_queue.get(), timeout=0.1)
                    yield AgentResponseChunk(
                        request_id=rid,
                        channel_id=channel_id,
                        payload=event,
                        is_complete=False,
                    )
                    if (
                        isinstance(event, dict)
                        and event.get("event_type") == "team.error"
                    ):
                        break
                except asyncio.TimeoutError:
                    if not team_manager.has_stream_task(session_id):
                        break
                    continue
        except asyncio.CancelledError:
            logger.info(
                "[TeamHelpers] event stream cancelled: channel_id=%s session_id=%s request_id=%s",
                _resolve_channel_id(channel_id),
                session_id,
                rid,
            )
            raise
        except Exception as exc:
            logger.exception(
                "[TeamHelpers] event stream failed: channel_id=%s session_id=%s error=%s",
                _resolve_channel_id(channel_id),
                session_id,
                exc,
            )
            yield AgentResponseChunk(
                request_id=rid,
                channel_id=channel_id,
                payload={"event_type": "chat.error", "error": str(exc)},
                is_complete=False,
            )

        yield AgentResponseChunk(
            request_id=rid,
            channel_id=channel_id,
            payload=None,
            is_complete=True,
        )
    finally:
        if request_queue is not None:
            waiter_key = _waiter_key(channel_id, session_id)
            waiters = _pending_waiters.get(waiter_key, [])
            _pending_waiters[waiter_key] = [
                (req_id, queue) for req_id, queue in waiters if req_id != rid
            ]
            if not _pending_waiters.get(waiter_key, []):
                _pending_waiters.pop(waiter_key, None)
                logger.info(
                    "[TeamHelpers] cleared waiter set: channel_id=%s session_id=%s",
                    waiter_key[0],
                    session_id,
                )


_DRAIN_INTERVAL_SEC = 5


async def _periodic_drain(
    channel_id: str | None,
    session_id: str,
) -> None:
    """Periodically drain buffered approval events in the background."""
    while True:
        await asyncio.sleep(_DRAIN_INTERVAL_SEC)
        try:
            await _drain_team_skill_approval_events(channel_id, session_id)
        except Exception as exc:
            logger.debug("[TeamHelpers] periodic drain failed: %s", exc)


async def _consume_stream_with_query(
    channel_id: str | None,
    session_id: str,
    team_agent: Any,
    initial_query: str,
) -> None:
    """Consume the team stream in the background and broadcast parsed events."""
    drain_task = asyncio.create_task(
        _periodic_drain(channel_id, session_id),
        name=f"periodic_drain_{session_id}",
    )
    try:
        logger.info(
            "[TeamHelpers] stream started: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        async for chunk in Runner.run_agent_team_streaming(
            agent_team=team_agent,
            inputs={"query": initial_query},
            session=session_id,
        ):
            parsed = parse_stream_chunk(chunk)
            if parsed is not None:
                _broadcast_event(channel_id, session_id, parsed)
            await _drain_team_skill_approval_events(channel_id, session_id)

        logger.warning(
            "[TeamHelpers] stream ended unexpectedly: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
    except asyncio.CancelledError:
        logger.info(
            "[TeamHelpers] stream cancelled: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "[TeamHelpers] stream failed: channel_id=%s session_id=%s error=%s",
            _resolve_channel_id(channel_id),
            session_id,
            exc,
        )
        _broadcast_event(
            channel_id,
            session_id,
            {
                "event_type": "team.error",
                "error": str(exc),
                "session_id": session_id,
            },
        )
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass

        # Final drain for any events buffered right before stream exit.
        try:
            await _drain_team_skill_approval_events(channel_id, session_id)
        except Exception as exc:
            logger.warning("[TeamHelpers] final drain failed: %s", exc)

        # Launch watcher task to await evolution completion and push approval.
        tm = get_team_manager(channel_id)
        rail = tm.get_team_skill_rail(session_id)
        if rail is not None:
            logger.info(
                "[TeamHelpers] launching evolution watcher: channel_id=%s session_id=%s",
                channel_id,
                session_id,
            )
            task = asyncio.create_task(
                _watch_team_evolution_and_push(channel_id, session_id, rail)
            )
            task.add_done_callback(_on_team_watcher_done)
        else:
            logger.warning(
                "[TeamHelpers] no TeamSkillRail found, evolution watcher not launched: session_id=%s",
                session_id,
            )

        tm.pop_stream_task(session_id)


async def _consume_monitor_events(
    channel_id: str | None,
    session_id: str,
    monitor_handler: TeamMonitorHandler,
) -> None:
    """Consume monitor events in the background and broadcast them."""
    try:
        logger.info(
            "[TeamHelpers] monitor event loop started: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        async for event in monitor_handler.events():
            _broadcast_event(channel_id, session_id, event)

        logger.info(
            "[TeamHelpers] monitor event loop ended: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
    except asyncio.CancelledError:
        logger.info(
            "[TeamHelpers] monitor event loop cancelled: channel_id=%s session_id=%s",
            _resolve_channel_id(channel_id),
            session_id,
        )
        raise
    except Exception as exc:
        logger.error(
            "[TeamHelpers] monitor event loop failed: channel_id=%s session_id=%s error=%s",
            _resolve_channel_id(channel_id),
            session_id,
            exc,
        )


def _on_team_watcher_done(task: asyncio.Task) -> None:
    """Callback when a team evolution watcher task completes."""
    exc = task.exception()
    if exc is not None:
        logger.warning("[TeamHelpers] evolution watcher task exception: %s", exc)


async def _watch_team_evolution_and_push(
    channel_id: str | None,
    session_id: str,
    rail: Any,
) -> None:
    """Wait for TeamSkillRail background evolution to complete, push approval via send_push."""
    from jiuwenclaw.agentserver.gateway_push import WebSocketGatewayPushTransport

    transport = WebSocketGatewayPushTransport()
    rid = f"team_evolve_{session_id}"

    async def _push_status(status: str, stage: str, message: str = "") -> None:
        await transport.send_push(
            {
                "request_id": rid,
                "channel_id": channel_id or "default",
                "session_id": session_id,
                "payload": {
                    "event_type": "chat.evolution_status",
                    "status": status,
                    "stage": stage,
                    "message": message,
                },
            }
        )

    async def _push_approval(evt) -> None:
        raw_payload = (
            evt.payload
            if hasattr(evt, "payload") and isinstance(evt.payload, dict)
            else evt
        )
        # Inject event_type from OutputSchema.type so Gateway routes it correctly.
        payload = dict(raw_payload)
        evt_type = getattr(evt, "type", None)
        if evt_type and "event_type" not in payload:
            payload["event_type"] = evt_type
        await transport.send_push(
            {
                "request_id": rid,
                "channel_id": channel_id or "default",
                "session_id": session_id,
                "payload": payload,
            }
        )

    def _is_approval(evt) -> bool:
        evt_type = getattr(evt, "type", "")
        if evt_type == "chat.ask_user_question":
            return True
        if hasattr(evt, "payload") and isinstance(evt.payload, dict):
            return evt.payload.get("event_type") == "chat.ask_user_question"
        return False

    try:
        # 1. Push evolution start status.
        await _push_status(
            "start", "collecting", "Running team skill evolution analysis..."
        )

        # 2. Wait for SDK background evolution task to complete and retrieve approval events.
        events = await rail.drain_pending_approval_events(wait=True, timeout=300)

        # 3. Push approval events first (filter out llm_reasoning progress events).
        for evt in events:
            if _is_approval(evt):
                try:
                    await _push_approval(evt)
                except Exception as exc:
                    logger.warning(
                        "[TeamHelpers] push approval failed for event type=%s: %s",
                        getattr(evt, "type", "unknown"),
                        exc,
                    )

        # 4. Push evolution end status last.
        await _push_status(
            "end", "completed", "Team skill evolution analysis completed"
        )
    except Exception as exc:
        logger.warning("[TeamHelpers] evolution watcher failed: %s", exc)
        try:
            await _push_status("end", "failed", f"团队技能演进分析失败: {exc}")
        except Exception as push_exc:
            logger.warning("[TeamHelpers] push status notification failed: %s", push_exc)
