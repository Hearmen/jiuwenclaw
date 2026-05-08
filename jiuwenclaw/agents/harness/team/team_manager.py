# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.paths import team_home
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agents.harness.team.bootstrap import configure_agent_teams_home

configure_agent_teams_home()

from jiuwenclaw.agents.harness.team.config_loader import (
    load_team_spec_dict,
)
from jiuwenclaw.agents.harness.team.distributed_runtime import (
    ensure_postgresql_for_leader,
    extract_pg_endpoint,
    fallback_distributed_to_local,
    is_distributed_mode,
    missing_distributed_dependencies,
    is_pg_available,
    is_postgresql_storage,
    normalize_distributed_transport_fields,
    parse_port,
    run_command,
    runtime_member_name,
    runtime_role,
    try_start_pg_cluster,
)
from jiuwenclaw.agents.harness.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.agents.harness.team.remote_member_bootstrap import release_a2x_reservations_for_team
from jiuwenclaw.common.config import get_config, get_default_models
from jiuwenclaw.agents.harness.team.team_runtime_inheritance import (
    MemberInfo,
    RAIL_WHITELIST,
    RuntimeInfo,
    TeamWorkspaceInfo,
    build_member_rails,
    filter_inheritable_ability_cards,
    get_default_model_name,
)
from jiuwenclaw.common.utils import get_agent_skills_dir

logger = logging.getLogger(__name__)

# Wall-clock cap for a single external command (pg_isready, systemctl, etc.).
_SUBPROCESS_TIMEOUT_SEC = 120.0
# After pg_ctlcluster/systemd reports start, the server may still be initializing.
_PG_POST_START_READY_MAX_SEC = 30.0
_PG_POST_START_READY_INIT_SLEEP = 0.4
_PG_POST_START_READY_MAX_SLEEP = 2.0
_PG_POST_START_READY_BACKOFF = 1.45
_PG_POST_START_LOG_EVERY_SEC = 5.0


def _sync_skills_dir(source: Path, target: Path) -> None:
    """Copy every valid skill directory from *source* into *target*.

    A valid skill is a sub-directory containing a ``SKILL.md`` file.
    Existing skills in *target* are overwritten so the latest version
    always wins.
    """
    if not source.is_dir():
        return
    target.mkdir(parents=True, exist_ok=True)
    synced = 0
    for skill_dir in source.iterdir():
        if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
            continue
        dest = target / skill_dir.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(skill_dir, dest)
        synced += 1
    if synced:
        logger.info("[TeamManager] synced %d skills: %s -> %s", synced, source, target)


async def _stop_team_messager(team_agent: Any, *, session_id: str) -> None:
    """Stop a team's mailbox transport so per-team ZMQ sockets release their ports."""
    messager = getattr(team_agent, "_messager", None) or getattr(team_agent, "mailbox_transport", None)
    stop = getattr(messager, "stop", None)
    if not callable(stop):
        return
    try:
        await stop()
        logger.info("[TeamManager] team messager stopped: session_id=%s", session_id)
    except Exception as exc:
        logger.warning("[TeamManager] team messager stop failed: session_id=%s error=%s", session_id, exc)


async def cleanup_team_runtime_state_once() -> tuple[list[str], list[str]]:
    """Clear leftover shared team runtime state once during AgentServer startup."""
    from openjiuwen.agent_teams.paths import get_agent_teams_home
    from openjiuwen.agent_teams.spawn.shared_resources import get_shared_db
    from openjiuwen.agent_teams.tools.database import DatabaseConfig

    db_config = DatabaseConfig()
    if db_config.db_type == "sqlite" and not db_config.connection_string:
        db_config.connection_string = str(get_agent_teams_home() / "team.db")
    try:
        shared_db = get_shared_db(db_config)
        return await shared_db.cleanup_all_runtime_state()
    except Exception as exc:
        logger.warning("[TeamManager] startup runtime cleanup failed: %s", exc)
        return [], []


class TeamManager:
    """Manage team instances across sessions."""

    def __init__(self):
        self._team_agents: dict[str, TeamAgent] = {}
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        # session_id → TeamSkillRail instance (set by customizer, used for drain/approval)
        self._team_skill_rails: dict[str, Any] = {}
        # session_id → (workspace_skills_dir, global_team_skills_dir)
        self._team_skill_sync_targets: dict[str, tuple[Path, Path]] = {}
        # session_id → evolution watcher task
        self._team_evolution_watchers: dict[str, asyncio.Task] = {}

    def has_stream_task(self, session_id: str) -> bool:
        return session_id in self._stream_tasks

    def pop_stream_task(self, session_id: str) -> asyncio.Task | None:
        return self._stream_tasks.pop(session_id, None)

    def get_team_evolution_watcher(self, session_id: str) -> asyncio.Task | None:
        return self._team_evolution_watchers.get(session_id)

    def register_team_evolution_watcher(self, session_id: str, task: asyncio.Task) -> None:
        self._team_evolution_watchers[session_id] = task

    def pop_team_evolution_watcher(self, session_id: str) -> asyncio.Task | None:
        return self._team_evolution_watchers.pop(session_id, None)

    @staticmethod
    def _is_distributed_mode(config_base: dict[str, Any]) -> bool:
        return is_distributed_mode(config_base)

    @staticmethod
    def _runtime_role(config_base: dict[str, Any]) -> str:
        return runtime_role(config_base)

    @staticmethod
    def _runtime_member_name(config_base: dict[str, Any], team_cfg: dict[str, Any]) -> str | None:
        return runtime_member_name(config_base, team_cfg)

    @staticmethod
    def _normalize_distributed_transport_fields(
        config_base: dict[str, Any],
        team_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        return normalize_distributed_transport_fields(config_base, team_cfg)

    @staticmethod
    def normalize_distributed_transport_fields(
        config_base: dict[str, Any],
        team_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Public wrapper for distributed transport normalization."""
        return TeamManager._normalize_distributed_transport_fields(config_base, team_cfg)

    @staticmethod
    def _parse_port(value: Any, default: int, field_name: str) -> int:
        return parse_port(value, default, field_name)

    @staticmethod
    def parse_port(value: Any, default: int, field_name: str) -> int:
        """Public wrapper for validated port parsing."""
        return TeamManager._parse_port(value, default, field_name)

    @staticmethod
    def _normalize_team_identity_fields(team_cfg: dict[str, Any]) -> dict[str, Any]:
        normalized_cfg = copy.deepcopy(team_cfg)
        leader_cfg = normalized_cfg.get("leader", {})
        if isinstance(leader_cfg, dict):
            display_name = str(leader_cfg.get("display_name", "")).strip()
            name = str(leader_cfg.get("name", "")).strip()
            if display_name and not name:
                leader_cfg["name"] = display_name
            elif name and not display_name:
                leader_cfg["display_name"] = name

        members = normalized_cfg.get("predefined_members", [])
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                display_name = str(member.get("display_name", "")).strip()
                name = str(member.get("name", "")).strip()
                if display_name and not name:
                    member["name"] = display_name
                elif name and not display_name:
                    member["display_name"] = name
        return normalized_cfg

    @staticmethod
    def _load_team_spec(session_id: str) -> TeamAgentSpec:
        config_base = get_config()
        # Keep dependency checks scoped to distributed mode to make the
        # control flow explicit at the call site (local mode bypasses checks).
        if TeamManager._is_distributed_mode(config_base):
            missing = missing_distributed_dependencies(config_base)
            if missing:
                missing_list = ", ".join(missing)
                logger.warning(
                    "[TeamManager][MISSING_DISTRIBUTE_DEPS] missing=%s",
                    missing_list,
                )
                logger.error(
                    "[TeamManager][FALLBACK_TO_LOCAL] "
                    "distributed runtime is not available; downgraded to local mode "
                    "for current process"
                )
                logger.warning(
                    "[TeamManager][ACTION] install via: "
                    "pip install -e \".[distribute]\" or uv sync --extra distribute"
                )
                config_base = fallback_distributed_to_local(config_base)

        spec_dict = load_team_spec_dict(session_id, config_base=config_base)
        spec_dict = TeamManager._normalize_team_identity_fields(spec_dict)
        if TeamManager._is_distributed_mode(config_base):
            spec_dict = TeamManager._normalize_distributed_transport_fields(config_base, spec_dict)

        # When models.defaults has more than one entry, populate model_pool
        # and set model_pool_strategy to by_model_name so team members
        # can be assigned different model endpoints from the pool.
        default_models = get_default_models(config_base)
        if len(default_models) > 1:
            from openjiuwen.agent_teams.schema.team import ModelPoolEntry

            pool_entries: list[dict] = []
            for entry in default_models:
                mcc = entry.get("model_client_config") or {}
                mco = entry.get("model_config_obj") or {}
                if not mcc.get("model_name"):
                    continue
                pool_entry = ModelPoolEntry(
                    model_name=mcc["model_name"],
                    api_key=mcc.get("api_key", ""),
                    api_base_url=mcc.get("api_base", ""),
                    api_provider=mcc.get("client_provider", ""),
                    metadata={
                        "client": {
                            k: v for k, v in mcc.items()
                            if k not in ("model_name", "api_key", "api_base", "client_provider") and v is not None
                        },
                        "request": dict(mco),
                    },
                )
                pool_entries.append(pool_entry.model_dump())

            if pool_entries:
                spec_dict["model_pool"] = pool_entries
                spec_dict["model_pool_strategy"] = "by_model_name"

        return TeamAgentSpec.model_validate(spec_dict)

    @staticmethod
    def register_member_runtime_tools(
        agent: DeepAgent,
        *,
        session_id: str,
        request_id: str | None,
        channel_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> None:
        from jiuwenclaw.agents.harness.common.tools.cron.cron_runtime import CronRuntimeBridge
        from jiuwenclaw.agents.harness.common.tools.send_file_to_user import SendFileToolkit
        from openjiuwen.core.runner import Runner

        agent_id = getattr(getattr(agent, "card", None), "id", None)
        cron_runtime = CronRuntimeBridge()
        cron_context = SimpleNamespace(
            tool_scope=f"team_member_{agent_id or 'unknown'}",
            channel_id=channel_id or "web",
            session_id=session_id,
            metadata=request_metadata,
            mode="team",
        )

        try:
            cron_tools = cron_runtime.build_tools(
                context=cron_context,
                agent_id=agent_id,
                language=getattr(agent.deep_config, "language", "cn")
            )
            for cron_tool in cron_tools:
                if not Runner.resource_mgr.get_tool(cron_tool.card.id):
                    Runner.resource_mgr.add_tool(cron_tool)
                agent.ability_manager.add(cron_tool.card)
            logger.info("[TeamManager] Registered %d cron tools for member agent=%s", len(cron_tools), agent_id)
        except Exception as exc:
            logger.warning("[TeamManager] cron tool registration failed for member agent=%s: %s", agent_id, exc)

        if not request_id or not channel_id:
            logger.info("[TeamManager] SendFileToolkit skipped: missing request_id or channel_id")
            return

        try:
            config = get_config()
            send_file_enabled = (
                config.get("channels", {})
                .get(str(channel_id), {})
                .get("send_file_allowed", False)
            )
            if not send_file_enabled:
                logger.info(
                    "[TeamManager] SendFileToolkit skipped: send_file_allowed=False for channel=%s",
                    channel_id,
                )
                return

            for existing in list(agent.ability_manager.list() or []):
                if getattr(existing, "name", "").startswith("send_file_to_user"):
                    agent.ability_manager.remove(existing.name)

            send_file_toolkit = SendFileToolkit(
                request_id=request_id,
                session_id=session_id,
                channel_id=channel_id,
                metadata=request_metadata,
            )
            for sf_tool in send_file_toolkit.get_tools():
                Runner.resource_mgr.add_tool(sf_tool)
                agent.ability_manager.add(sf_tool.card)
            logger.info("[TeamManager] SendFileToolkit registered for channel=%s", channel_id)
        except Exception as exc:
            logger.warning("[TeamManager] SendFileToolkit registration failed: %s", exc)

    @staticmethod
    def build_agent_customizer(
        spec: TeamAgentSpec,
        deep_agent: DeepAgent,
        session_id: str,
        *,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> Callable[..., None]:
        from jiuwenclaw.agents.harness.team.rails.team_member_skill_toolkit_rail import (
            MemberSkillToolkitRail,
        )
        from jiuwenclaw.server.runtime.skill.skill_manager import SkillManager
        from jiuwenclaw.agents.harness.common.plugins.rail_manager import get_rail_manager

        global_skills_dir = get_agent_skills_dir()
        global_skills_state_path = global_skills_dir / "skills_state.json"
        resolved_channel = channel_id or "default"
        resolved_model_name = get_default_model_name()

        # Resolve team shared workspace skills directory for TeamSkillRail.
        ws_config = spec.workspace
        team_ws_root = (
            ws_config.root_path if ws_config and ws_config.root_path
            else str(team_home(spec.team_name) / "team-workspace")
        )
        team_ws_skills_dir = Path(team_ws_root) / "skills"
        team_ws_trajectories_dir = Path(team_ws_root) / "trajectories"

        def resolve_member_spec(
            member_name: str | None,
            role: str | None,
        ) -> Any:
            if member_name and member_name in spec.agents:
                return spec.agents[member_name]
            if role and role in spec.agents:
                return spec.agents[role]
            return spec.agents.get("leader")

        def resolve_member_skills(
            member_name: str | None,
            role: str | None,
        ) -> tuple[bool, list[str]]:
            member_spec = resolve_member_spec(member_name, role)
            if member_spec is None or not hasattr(member_spec, "skills"):
                return False, []

            skills = getattr(member_spec, "skills", None)
            if skills is None:
                return False, []

            return True, [str(skill).strip() for skill in skills if str(skill).strip()]

        def copy_member_configured_skills(
            member_skills_dir: Path,
            selected_skills: list[str],
        ) -> None:
            """Copy member-configured skills to member's own skills directory."""
            if not global_skills_dir.exists():
                logger.warning("[TeamManager] global_skills_dir does not exist: %s", global_skills_dir)
                return

            selected_skill_set = set(selected_skills)
            member_skills_dir.mkdir(parents=True, exist_ok=True)
            copied_count = 0
            for skill_dir in global_skills_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                if not (skill_dir / "SKILL.md").is_file():
                    continue
                if skill_dir.name not in selected_skill_set:
                    continue
                dest = member_skills_dir / skill_dir.name
                if dest.exists():
                    continue
                shutil.copytree(skill_dir, dest)
                copied_count += 1
                logger.info("[TeamManager] Copied skill '%s' to member workspace", skill_dir.name)

            existing_skill_names = {
                path.name for path in member_skills_dir.iterdir() if path.is_dir()
            }
            missing = sorted(selected_skill_set - existing_skill_names)
            if missing:
                logger.warning("[TeamManager] configured skills not found in global dir: %s", missing)

            logger.info("[TeamManager] Total configured skills copied to member: %d", copied_count)

        def build_member_skill_state(member_skills_dir: Path) -> dict[str, Any]:
            state: dict[str, Any] = {
                "marketplaces": [],
                "installed_plugins": [],
                "local_skills": [],
            }
            if global_skills_state_path.is_file():
                try:
                    loaded_state = json.loads(global_skills_state_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_state, dict):
                        state.update(loaded_state)
                except Exception as exc:
                    logger.warning("[TeamManager] failed to load global skills_state.json: %s", exc)

            state["marketplaces"] = SkillManager.normalize_marketplaces(
                state.get("marketplaces")
            )

            actual_skill_names = sorted(
                path.name
                for path in member_skills_dir.iterdir()
                if path.is_dir() and (path / "SKILL.md").is_file()
            )
            actual_skill_set = set(actual_skill_names)

            installed_plugins = []
            for plugin in state.get("installed_plugins", []):
                if not isinstance(plugin, dict):
                    continue
                plugin_name = str(plugin.get("name", "")).strip()
                if not plugin_name or plugin_name not in actual_skill_set:
                    continue
                installed_plugins.append(plugin)

            local_skills = []
            for local_skill in state.get("local_skills", []):
                if not isinstance(local_skill, dict):
                    continue
                skill_name = str(local_skill.get("name", "")).strip()
                if not skill_name or skill_name not in actual_skill_set:
                    continue
                local_skills.append(local_skill)

            existing_plugin_names = {
                str(plugin.get("name", "")).strip()
                for plugin in installed_plugins
                if isinstance(plugin, dict)
            }
            existing_local_names = {
                str(local_skill.get("name", "")).strip()
                for local_skill in local_skills
                if isinstance(local_skill, dict)
            }
            for skill_name in actual_skill_names:
                if skill_name not in existing_plugin_names:
                    installed_plugins.append(
                        {
                            "name": skill_name,
                            "marketplace": "",
                            "version": "",
                            "commit": "",
                            "source": "project",
                            "installed_at": "",
                        }
                    )
                if skill_name not in existing_local_names:
                    local_skills.append(
                        {
                            "name": skill_name,
                            "origin": str(member_skills_dir / skill_name),
                            "source": "project",
                        }
                    )

            state["installed_plugins"] = installed_plugins
            state["local_skills"] = local_skills
            return state

        def write_member_skill_state(member_skills_dir: Path) -> None:
            state_file = member_skills_dir / "skills_state.json"
            state = build_member_skill_state(member_skills_dir)
            state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[TeamManager] Wrote member skills_state.json: %s", state_file)

        def customizer(
            agent: DeepAgent,
            member_name: str | None = None,
            role: str | None = None,
        ) -> None:
            logger.info(
                "[TeamManager] customizer called: channel=%s member_name=%s role=%s",
                resolved_channel,
                member_name,
                role,
            )
            agent_ws = agent.deep_config.workspace if agent.deep_config else None
            if agent_ws:
                logger.debug("[TeamManager] member workspace.root_path=%s", agent_ws.root_path)
            else:
                logger.warning("[TeamManager] agent deep_config.workspace is None")

            inheritable_cards = filter_inheritable_ability_cards(deep_agent)
            existing_ability_ids = {card.id for card in agent.ability_manager.list() or []}
            added_count = 0
            for card in inheritable_cards:
                if card.id not in existing_ability_ids:
                    agent.ability_manager.add(card)
                    existing_ability_ids.add(card.id)
                    added_count += 1
                else:
                    logger.debug("[TeamManager] Ability '%s' already exists, skipped", card.name)
            logger.info(
                "[TeamManager] Added %d inheritable abilities (total: %d)",
                added_count,
                len(existing_ability_ids),
            )

            member_workspace = agent.deep_config.workspace if agent.deep_config else None
            member_skills_dir_resolved: Path | None = None
            if member_workspace and member_workspace.root_path:
                member_skills_dir = Path(member_workspace.root_path) / "skills"
                member_skills_dir_resolved = member_skills_dir
                skills_configured, selected_skills = resolve_member_skills(member_name, role)

                # Copy member-configured skills to member's own skills directory
                # Note: global skills are already copied to team shared directory in create_team
                try:
                    # Ensure member skills directory exists
                    member_skills_dir.mkdir(parents=True, exist_ok=True)
                    if skills_configured and selected_skills:
                        copy_member_configured_skills(member_skills_dir, selected_skills)
                    # Member directory always needs skills_state.json
                    write_member_skill_state(member_skills_dir)
                except Exception as exc:
                    logger.warning("[TeamManager] skill copy failed: %s", exc)

                # Create independent SkillManager and SkillToolkit for member
                try:
                    agent.add_rail(
                        MemberSkillToolkitRail(
                            workspace_dir=str(member_workspace.root_path),
                        )
                    )
                    logger.info(
                        "[TeamManager] MemberSkillToolkitRail queued for member workspace: %s",
                        member_workspace.root_path,
                    )
                except Exception as exc:
                    logger.warning("[TeamManager] MemberSkillToolkitRail setup failed: %s", exc)

            # Build all member rails (common + skill rails via role).
            try:
                member_rails = build_member_rails(
                    member_info=MemberInfo(
                        agent_name=getattr(agent.card, "name", "team_member"),
                        model_name=resolved_model_name,
                        role=role
                    ),
                    runtime=RuntimeInfo(channel=resolved_channel),
                    team_workspace=TeamWorkspaceInfo(
                        root_dir=str(Path(team_ws_root)),
                        skills_dir=str(team_ws_skills_dir),
                        trajectories_dir=str(team_ws_trajectories_dir),
                        team_id=spec.team_name,
                        config=get_config(),
                    ),
                )
                from openjiuwen.harness.rails import TeamSkillRail
                team_skill_rail: Any | None = None
                for rail in member_rails:
                    if type(rail).__name__ in RAIL_WHITELIST:
                        agent.add_rail(rail)
                    else:
                        logger.debug("[TeamManager] Skipping non-whitelisted rail: %s", type(rail).__name__)
                    if isinstance(rail, TeamSkillRail):
                        team_skill_rail = rail
                logger.info("[TeamManager] Added %d rails for team member", len(member_rails))
                # Register TeamSkillRail with TeamManager for approval/sync.
                if team_skill_rail is not None:
                    tm = get_team_manager(resolved_channel)
                    tm.register_team_skill_rail(session_id, team_skill_rail)
                    tm.register_team_skill_sync_target(
                        session_id,
                        team_ws_skills_dir,
                        get_agent_skills_dir(),
                    )
                    logger.info(
                        "[TeamManager] TeamSkillRail mounted on leader "
                        "(skills_dir=%s, sync_target=%s)",
                        team_ws_skills_dir, get_agent_skills_dir(),
                    )
            except Exception as exc:
                logger.warning("[TeamManager] build_member_rails failed: %s", exc)

            rail_manager = get_rail_manager()
            for rail_name in rail_manager.get_registered_rail_names():
                try:
                    rail_instance = rail_manager.load_rail_instance_without_enabled_check(rail_name)
                    if rail_instance is not None:
                        agent.add_rail(rail_instance)
                        logger.info("[TeamManager] Added extension rail: %s", rail_name)
                except Exception as exc:
                    logger.warning("[TeamManager] add rail %s failed: %s", rail_name, exc)

            TeamManager.register_member_runtime_tools(
                agent,
                session_id=session_id,
                request_id=request_id,
                channel_id=channel_id,
                request_metadata=request_metadata,
            )

        return customizer

    @staticmethod
    def _is_postgresql_storage(team_cfg: dict[str, Any]) -> bool:
        return is_postgresql_storage(team_cfg)

    @staticmethod
    def _extract_pg_endpoint(team_cfg: dict[str, Any]) -> tuple[str, int]:
        return extract_pg_endpoint(team_cfg)

    @staticmethod
    async def _run_command(*args: str) -> tuple[int, str]:
        return await run_command(*args, subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _is_pg_available(self, host: str, port: int) -> bool:
        return await is_pg_available(host, port, subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _try_start_pg_cluster(self) -> bool:
        return await try_start_pg_cluster(subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC)

    async def _ensure_postgresql_for_leader(self, config_base: dict[str, Any]) -> None:
        await ensure_postgresql_for_leader(
            config_base,
            subprocess_timeout_sec=_SUBPROCESS_TIMEOUT_SEC,
            post_start_ready_max_sec=_PG_POST_START_READY_MAX_SEC,
            post_start_ready_init_sleep=_PG_POST_START_READY_INIT_SLEEP,
            post_start_ready_max_sleep=_PG_POST_START_READY_MAX_SLEEP,
            post_start_ready_backoff=_PG_POST_START_READY_BACKOFF,
            post_start_log_every_sec=_PG_POST_START_LOG_EVERY_SEC,
        )

    @staticmethod
    def _copy_global_skills_to_team_shared_dir(spec: TeamAgentSpec) -> None:
        """Copy global skills to team shared directory (executed once after team build)."""
        global_skills_dir = get_agent_skills_dir()
        if not global_skills_dir.exists():
            logger.warning("[TeamManager] global_skills_dir does not exist: %s", global_skills_dir)
            return

        # Resolve team workspace path
        ws_config = spec.workspace
        ws_path = ws_config.root_path if ws_config and ws_config.root_path else None
        if not ws_path:
            ws_path = str(team_home(spec.team_name) / "team-workspace")

        team_shared_skills_dir = Path(ws_path) / "skills"

        # Check if already copied (via marker file)
        copied_marker = team_shared_skills_dir / ".team_skills_copied"
        if copied_marker.exists():
            logger.info("[TeamManager] Team shared skills already copied, skipping")
            return

        # Copy entire skills directory (including skills_state.json)
        team_shared_skills_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(global_skills_dir, team_shared_skills_dir, dirs_exist_ok=True)

        # Write marker file to indicate copy completed
        copied_marker.write_text("", encoding="utf-8")
        logger.info("[TeamManager] Copied global skills dir to team shared: %s", team_shared_skills_dir)

    async def create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        config_base = get_config()
        await self._ensure_postgresql_for_leader(config_base)
        logger.info("[TeamManager] building TeamAgentSpec: session_id=%s", session_id)
        spec = self._load_team_spec(session_id)

        spec.agent_customizer = self.build_agent_customizer(
            spec,
            deep_agent,
            session_id,
            request_id=request_id,
            channel_id=channel_id,
            request_metadata=request_metadata,
        )

        logger.info("[TeamManager] TeamAgentSpec ready: team_name=%s", spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] creating TeamAgent from spec")
            team_agent = spec.build()
            self._team_agents[session_id] = team_agent
            # After build, copy global skills to team shared directory (only once)
            self._copy_global_skills_to_team_shared_dir(spec)

            if self._is_distributed_mode(config_base):
                try:
                    from jiuwenclaw.agents.harness.team.remote_member_bootstrap import (
                        attach_distributed_local_spawn_guard,
                        attach_remote_bootstrap_ack_listener,
                        attach_remote_teammate_bootstrap_listener,
                        attach_spawn_member_remote_bootstrap_wrapper,
                    )

                    attach_distributed_local_spawn_guard(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_spawn_member_remote_bootstrap_wrapper(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_remote_bootstrap_ack_listener(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                    attach_remote_teammate_bootstrap_listener(
                        team_agent,
                        session_id=session_id,
                        channel_id=channel_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[TeamManager] remote_member_bootstrap wrapper attach failed: %s",
                        exc,
                    )
            logger.info(
                "[TeamManager] Team created: session_id=%s, team_name=%s",
                session_id,
                spec.team_name,
            )
            return team_agent
        finally:
            reset_session_id(token)

    async def get_or_create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        async with self._lock:
            team_agent = self._team_agents.get(session_id)
            if team_agent is not None:
                return team_agent

            await self._destroy_other_sessions(session_id)
            return await self.create_team(
                session_id,
                deep_agent,
                request_id,
                channel_id,
                request_metadata,
            )

    async def interact(self, session_id: str, user_input: str) -> bool:
        team_agent = self._team_agents.get(session_id)
        if team_agent is None:
            logger.warning("[TeamManager] interact failed, missing team: session_id=%s", session_id)
            return False

        try:
            await team_agent.interact(user_input)
            logger.debug("[TeamManager] interact sent: session_id=%s", session_id)
            return True
        except Exception as exc:
            logger.error("[TeamManager] interact failed: session_id=%s, error=%s", session_id, exc)
            return False

    # ── TeamSkillRail accessor ──────────────────────────────────

    def get_team_skill_rail(self, session_id: str) -> Any | None:
        return self._team_skill_rails.get(session_id)

    def find_team_skill_rail_for_request(self, request_id: str) -> Any | None:
        """Find the TeamSkillRail that owns a pending patch with this request_id."""
        for rail in self._team_skill_rails.values():
            if request_id in getattr(rail, "_pending_patch_snapshots", {}):
                return rail
        return None

    async def drain_team_skill_events(self, session_id: str) -> list[dict]:
        """Drain buffered approval events from this session's TeamSkillRail."""
        rail = self._team_skill_rails.get(session_id)
        if rail is None:
            return []
        return await rail.drain_pending_approval_events()

    def register_team_skill_rail(self, session_id: str, rail: Any) -> None:
        """Register a TeamSkillRail instance for the given session."""
        self._team_skill_rails[session_id] = rail

    def register_team_skill_sync_target(
        self, session_id: str, source: Path, target: Path,
    ) -> None:
        """Register skill sync directories for the given session."""
        self._team_skill_sync_targets[session_id] = (source, target)

    def has_team_skill_sync_target(self, session_id: str) -> bool:
        """Return whether the session has a registered team skill sync target."""
        return session_id in self._team_skill_sync_targets

    # ── Skill sync helpers ──────────────────────────────────────

    def sync_team_skills(self, session_id: str) -> None:
        """Sync team skills from workspace dir to global team_skills dir after approval."""
        sync_info = self._team_skill_sync_targets.get(session_id)
        if sync_info is None:
            logger.debug("[TeamManager] no sync target for session_id=%s", session_id)
            return
        source, target = sync_info
        _sync_skills_dir(source, target)

    async def destroy_team(self, session_id: str) -> bool:
        async with self._lock:
            return await self._destroy_team(session_id)

    async def _destroy_other_sessions(self, current_session_id: str) -> None:
        stale_session_ids = [sid for sid in list(self._team_agents.keys()) if sid != current_session_id]
        for stale_session_id in stale_session_ids:
            await self._destroy_team(stale_session_id)

    async def _destroy_team(self, session_id: str) -> bool:
        watcher_task = self._team_evolution_watchers.pop(session_id, None)
        if watcher_task and not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] evolution watcher stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        stream_task = self._stream_tasks.pop(session_id, None)
        if stream_task and not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        monitor_handler = self._team_monitors.pop(session_id, None)
        if monitor_handler is not None:
            try:
                await monitor_handler.stop()
            except Exception as exc:
                logger.warning(
                    "[TeamManager] monitor stop failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )

        # Clean up sync state for the team skill rail.
        self._team_skill_rails.pop(session_id, None)
        self._team_skill_sync_targets.pop(session_id, None)

        team_agent = self._team_agents.pop(session_id, None)
        cleaned = False
        try:
            if team_agent is None:
                logger.info("[TeamManager] no in-memory team for session_id=%s", session_id)
                return False

            token = set_session_id(session_id)
            try:
                try:
                    cleaned = await team_agent.destroy_team(force=True)
                finally:
                    await release_a2x_reservations_for_team(team_agent)
                    await _stop_team_messager(team_agent, session_id=session_id)
            finally:
                reset_session_id(token)

            logger.info(
                "[TeamManager] Team cleaned via core API: session_id=%s cleaned=%s",
                session_id,
                cleaned,
            )
        except Exception as exc:
            logger.error(
                "[TeamManager] destroy team failed: session_id=%s error=%s",
                session_id,
                exc,
            )

        return cleaned

    async def cleanup_all(self) -> None:
        async with self._lock:
            session_ids = list(self._team_agents.keys())
            for session_id in session_ids:
                await self._destroy_team(session_id)
            logger.info("[TeamManager] all teams cleaned")

    def get_team_agent(self, session_id: str) -> TeamAgent | None:
        return self._team_agents.get(session_id)

    def register_monitor(self, session_id: str, handler: TeamMonitorHandler) -> None:
        self._team_monitors[session_id] = handler

    def register_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        self._stream_tasks[session_id] = task

    async def terminate_session_runtime(self, session_id: str, reason: str = "") -> bool:
        """终止指定 session 的 Team 运行时（stream/monitor/team agent/runtime cleanup）。"""
        async with self._lock:
            has_stream_task = session_id in self._stream_tasks
            has_team_runtime = session_id in self._team_agents or session_id in self._team_monitors
            if not has_stream_task and not has_team_runtime:
                return False
            logger.info(
                "[TeamManager] %s terminate team session runtime: session_id=%s",
                reason,
                session_id,
            )
            cleaned = await self._destroy_team(session_id)
        logger.info(
            "[TeamManager] %steam session terminated: session_id=%s cleaned=%s",
            reason,
            session_id,
            cleaned,
        )
        return True

    async def cancel_session_stream_task(self, session_id: str, reason: str = "") -> bool:
        """兼容旧命名；实际语义为终止该 session 的 Team runtime。"""
        return await self.terminate_session_runtime(session_id, reason=reason)

    async def cancel_all_stream_tasks(self, reason: str = "") -> None:
        """Gateway 与 AgentServer 断开时取消 Team 后台 stream 协程（含 create_task 绕开 SessionManager 的任务）。"""
        async with self._lock:
            pending = list(self._stream_tasks.items())
        for session_id, task in pending:
            if task.done():
                continue
            logger.info(
                "[TeamManager] %s cancel stream task session_id=%s",
                reason,
                session_id,
            )
            task.cancel()
        for session_id, task in pending:
            if task.done():
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "[TeamManager] stream task await after cancel failed session_id=%s: %s",
                    session_id,
                    exc,
                )
        async with self._lock:
            self._stream_tasks.clear()


_team_managers: dict[str, TeamManager] = {}


def get_team_manager(channel_id: str | None = None) -> TeamManager:
    resolved_channel_id = str(channel_id or "default").strip() or "default"
    manager = _team_managers.get(resolved_channel_id)
    if manager is None:
        manager = TeamManager()
        _team_managers[resolved_channel_id] = manager
    return manager


def find_team_skill_rail_across_managers(request_id: str) -> Any | None:
    """Find the TeamSkillRail that owns a pending request across all channel managers."""
    for manager in _team_managers.values():
        rail = manager.find_team_skill_rail_for_request(request_id)
        if rail is not None:
            return rail
    return None


def sync_team_skills_across_managers(session_id: str) -> bool:
    """Sync team skills for the given session across all channel managers."""
    for manager in _team_managers.values():
        if manager.has_team_skill_sync_target(session_id):
            manager.sync_team_skills(session_id)
            return True
    return False


async def cancel_all_team_stream_tasks_across_managers(reason: str = "") -> None:
    """Cancel team stream tasks for all channel managers."""
    for manager in list(_team_managers.values()):
        await manager.cancel_all_stream_tasks(reason=reason)


def reset_team_manager(channel_id: str | None = None) -> None:
    if channel_id is None:
        _team_managers.clear()
        return

    resolved_channel_id = str(channel_id).strip() or "default"
    _team_managers.pop(resolved_channel_id, None)
