# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn.context import reset_session_id, set_session_id
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.team.config_loader import load_team_spec_dict
from jiuwenclaw.agentserver.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    RAIL_WHITELIST,
    build_member_rails,
    filter_inheritable_ability_cards,
    get_default_model_name,
)

logger = logging.getLogger(__name__)


class TeamManager:
    """Manage team instances across sessions."""

    def __init__(self):
        self._team_agents: dict[str, TeamAgent] = {}
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def has_stream_task(self, session_id: str) -> bool:
        return session_id in self._stream_tasks

    def pop_stream_task(self, session_id: str) -> asyncio.Task | None:
        return self._stream_tasks.pop(session_id, None)

    @staticmethod
    def _build_agent_customizer(
        deep_agent: DeepAgent,
        session_id: str,
        request_id: str | None,
        channel_id: str | None,
        request_metadata: dict[str, Any] | None,
    ) -> Callable[[DeepAgent], None]:
        from jiuwenclaw.agentserver.extensions.rail_manager import get_rail_manager
        from jiuwenclaw.agentserver.tools.send_file_to_user import SendFileToolkit
        from jiuwenclaw.utils import get_agent_skills_dir
        from openjiuwen.core.runner import Runner

        global_skills_dir = get_agent_skills_dir()
        resolved_channel = channel_id or "default"
        resolved_model_name = get_default_model_name()

        def customizer(agent: DeepAgent) -> None:
            logger.info("[TeamManager] customizer called for agent: channel=%s, agent_id=%s", resolved_channel, id(agent))
            logger.info("[TeamManager] main agent id: %s", id(deep_agent))

            # 调试：打印 agent 的 workspace 信息
            agent_ws = agent.deep_config.workspace if agent.deep_config else None
            if agent_ws:
                logger.info("[TeamManager] agent workspace.root_path=%s, stable_base=%s",
                           agent_ws.root_path, getattr(agent_ws, 'stable_base', 'N/A'))
            else:
                logger.warning("[TeamManager] agent deep_config.workspace is None")

            # 复用主 agent 的 sys_operation
            main_sys_operation = deep_agent.deep_config.sys_operation if deep_agent.deep_config else None
            if main_sys_operation and agent.deep_config:
                agent.deep_config.sys_operation = main_sys_operation
                logger.info("[TeamManager] Reused main agent's sys_operation for team member")
            elif not main_sys_operation:
                logger.warning("[TeamManager] Main agent's sys_operation is None, member will use its own")

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
            logger.info("[TeamManager] Added %d inheritable abilities (total: %d)", added_count, len(existing_ability_ids))

            member_workspace = agent.deep_config.workspace if agent.deep_config else None
            if member_workspace and member_workspace.root_path:
                member_skills_dir = Path(member_workspace.root_path) / "skills"

                try:
                    member_skills_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("[TeamManager] member_skills_dir: %s, exists: %s", member_skills_dir, member_skills_dir.exists())
                    
                    if global_skills_dir.exists():
                        copied_count = 0
                        for skill_dir in global_skills_dir.iterdir():
                            if skill_dir.is_dir() and not skill_dir.name.startswith("_"):
                                dest = member_skills_dir / skill_dir.name
                                if not dest.exists():
                                    shutil.copytree(skill_dir, dest)
                                    copied_count += 1
                                    logger.info("[TeamManager] Copied skill '%s' to member workspace", skill_dir.name)
                        logger.info("[TeamManager] Total skills copied: %d", copied_count)
                        
                        skills_after_copy = list(member_skills_dir.iterdir())
                        logger.info("[TeamManager] Skills in member workspace: %s", [s.name for s in skills_after_copy])
                    else:
                        logger.warning("[TeamManager] global_skills_dir does not exist: %s", global_skills_dir)
                except Exception as exc:
                    logger.warning("[TeamManager] skill copy failed: %s", exc)

                # 为 member 创建独立的 SkillManager 和 SkillToolkit
                try:
                    from jiuwenclaw.agentserver.skill_manager import SkillManager
                    from jiuwenclaw.agentserver.tools.skill_toolkits import SkillToolkit
                    
                    member_skill_manager = SkillManager(workspace_dir=str(member_workspace.root_path))
                    skill_toolkit = SkillToolkit(manager=member_skill_manager)
                    
                    existing_ability_ids = {card.id for card in agent.ability_manager.list() or []}
                    for tool in skill_toolkit.get_tools():
                        if not Runner.resource_mgr.get_tool(tool.card.id):
                            Runner.resource_mgr.add_tool(tool)
                        if tool.card.id not in existing_ability_ids:
                            agent.ability_manager.add(tool.card)
                            existing_ability_ids.add(tool.card.id)
                            logger.info("[TeamManager] Added SkillToolkit tool: %s", tool.card.name)
                        else:
                            logger.debug("[TeamManager] SkillToolkit tool '%s' already exists, skipped", tool.card.name)
                    logger.info("[TeamManager] SkillToolkit registered for member workspace: %s", member_workspace.root_path)
                except Exception as exc:
                    logger.warning("[TeamManager] SkillToolkit registration failed: %s", exc)

                try:
                    member_rails = build_member_rails(
                        skills_dir=str(member_skills_dir),
                        language="cn",
                        channel=resolved_channel,
                        agent_name=getattr(agent.card, "name", "team_member"),
                        model_name=resolved_model_name,
                    )
                    for rail in member_rails:
                        if type(rail).__name__ in RAIL_WHITELIST:
                            agent.add_rail(rail)
                        else:
                            logger.debug("[TeamManager] Skipping non-whitelisted rail: %s", type(rail).__name__)
                    logger.info("[TeamManager] Added %d rails for team member", len(member_rails))
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

            if request_id and channel_id:
                try:
                    from jiuwenclaw.agentserver.config import get_config
                    config = get_config()
                    send_file_enabled = config.get("channels", {}).get(str(channel_id), {}).get("send_file_allowed", False)
                    if send_file_enabled:
                        sf_toolkit = SendFileToolkit(
                            request_id=request_id,
                            session_id=session_id,
                            channel_id=channel_id,
                            metadata=request_metadata,
                        )
                        existing_ability_ids = {card.id for card in agent.ability_manager.list() or []}
                        for tool in sf_toolkit.get_tools():
                            if not Runner.resource_mgr.get_tool(tool.card.id):
                                Runner.resource_mgr.add_tool(tool)
                            if tool.card.id not in existing_ability_ids:
                                agent.ability_manager.add(tool.card)
                                existing_ability_ids.add(tool.card.id)
                            else:
                                logger.debug("[TeamManager] SendFile tool '%s' already exists, skipped", tool.card.name)
                        logger.info("[TeamManager] SendFileToolkit registered for channel=%s", channel_id)
                    else:
                        logger.info("[TeamManager] SendFileToolkit skipped: send_file_allowed=False for channel=%s", channel_id)
                except Exception as exc:
                    logger.warning("[TeamManager] SendFileToolkit registration failed: %s", exc)
            else:
                logger.info("[TeamManager] SendFileToolkit skipped: missing request_id or channel_id")

            logger.info("[TeamManager] Agent customizer completed")

        return customizer

    async def create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
        request_id: str | None = None,
        channel_id: str | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> TeamAgent:
        logger.info("[TeamManager] building TeamAgentSpec: session_id=%s", session_id)

        spec_dict = load_team_spec_dict(session_id)
        spec = TeamAgentSpec.model_validate(spec_dict)
        spec.agent_customizer = self._build_agent_customizer(
            deep_agent,
            session_id,
            request_id,
            channel_id,
            request_metadata,
        )

        logger.info("[TeamManager] TeamAgentSpec ready: team_name=%s", spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] creating TeamAgent from spec")
            team_agent = spec.build()
            self._team_agents[session_id] = team_agent
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

    async def destroy_team(self, session_id: str) -> bool:
        async with self._lock:
            return await self._destroy_team(session_id)

    async def _destroy_other_sessions(self, current_session_id: str) -> None:
        stale_session_ids = [sid for sid in list(self._team_agents.keys()) if sid != current_session_id]
        for stale_session_id in stale_session_ids:
            await self._destroy_team(stale_session_id)

    async def _destroy_team(self, session_id: str) -> bool:
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

        team_agent = self._team_agents.pop(session_id, None)
        if team_agent is None:
            return False

        try:
            token = set_session_id(session_id)
            try:
                cleaned = await team_agent.destroy_team(force=True)
            finally:
                reset_session_id(token)

            logger.info(
                "[TeamManager] Team cleaned via core API: session_id=%s cleaned=%s",
                session_id,
                cleaned,
            )
            return cleaned
        except Exception as exc:
            logger.error(
                "[TeamManager] destroy team failed: session_id=%s error=%s",
                session_id,
                exc,
            )
            return False

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


_team_manager: TeamManager | None = None


def get_team_manager() -> TeamManager:
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager


def reset_team_manager() -> None:
    global _team_manager
    _team_manager = None
