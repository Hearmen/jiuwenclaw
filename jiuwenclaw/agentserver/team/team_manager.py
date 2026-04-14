# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team lifecycle manager."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn.context import reset_session_id, set_session_id
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.team.config_loader import TeamConfig, load_team_config
from jiuwenclaw.agentserver.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.config import get_config

logger = logging.getLogger(__name__)


class TeamManager:
    """Manage team instances across sessions."""

    def __init__(self):
        self._team_agents: dict[str, TeamAgent] = {}
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._event_queues: dict[str, asyncio.Queue] = {}
        self._config: TeamConfig | None = None
        self._lock = asyncio.Lock()

    def has_stream_task(self, session_id: str) -> bool:
        return session_id in self._stream_tasks

    def set_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        self._stream_tasks[session_id] = task

    def pop_stream_task(self, session_id: str) -> asyncio.Task | None:
        return self._stream_tasks.pop(session_id, None)

    async def _get_config(self) -> TeamConfig:
        if self._config is None:
            self._config = load_team_config()
        return self._config

    @staticmethod
    def _build_agent_customizer(
        deep_agent: DeepAgent,
    ) -> Callable[[DeepAgent], None]:
        from jiuwenclaw.agentserver.extensions.rail_manager import get_rail_manager
        
        def customizer(agent: DeepAgent) -> None:
            """Customizer 回调：注入 Claw 能力."""
            rail_manager = get_rail_manager()
            
            # 遍历已注册的 rail 扩展名称，重新加载实例
            for rail_name in rail_manager.get_registered_rail_names():
                try:
                    # 重新加载 rail 实例，确保每个 agent 有独立的实例
                    rail_instance = rail_manager.load_rail_instance_without_enabled_check(rail_name)
                    agent.add_rail(rail_instance)
                    logger.debug("[TeamManager] Added rail instance for %s: %s", rail_name, rail_instance)
                except Exception as exc:
                    logger.warning("[TeamManager] add rail %s failed: %s", rail_name, exc)

            for ability in deep_agent.ability_manager.list():
                try:
                    agent.ability_manager.add(ability)
                except Exception as exc:
                    logger.warning("[TeamManager] add ability failed: %s", exc)

            logger.debug("[TeamManager] Agent customizer completed: %s", agent)

        return customizer

    def _build_team_agent_spec(
        self,
        team_config: TeamConfig,
        deep_agent: DeepAgent,
        session_id: str,
    ) -> TeamAgentSpec:
        config_base = get_config()
        team_config_dict = config_base.get("team", {})

        if not team_config_dict:
            team_config_dict = self._get_default_team_config()

        team_config_dict = self._build_agents_config(team_config_dict, config_base)
        team_config_dict["team_name"] = f"{team_config_dict.get('team_name', 'team')}_{session_id}"

        spec = TeamAgentSpec.model_validate(team_config_dict)
        spec.agent_customizer = self._build_agent_customizer(deep_agent)

        logger.info(
            "[TeamManager] TeamAgentSpec built: team_name=%s, agents=%s",
            spec.team_name,
            list(team_config_dict.get("agents", {}).keys()),
        )
        return spec

    @staticmethod
    def _build_agents_config(
        team_config_dict: dict[str, Any],
        config_base: dict[str, Any],
    ) -> dict[str, Any]:
        model_config = config_base.get("models", {}).get("default", {})
        model_client_config = model_config.get("model_client_config", {})
        model_request_config = model_config.get("model_config_obj", {})

        model_name = model_client_config.get("model_name", "")
        if model_name and "model" not in model_request_config:
            model_request_config = dict(model_request_config)
            model_request_config["model"] = model_name

        logger.info(
            "[TeamManager] model config loaded: model_name=%s, provider=%s",
            model_name,
            model_client_config.get("client_provider", "unknown"),
        )

        agents = {}

        leader_config = team_config_dict.get("leader", {})
        agents["leader"] = {
            "model": {
                "model_client_config": model_client_config,
                "model_request_config": model_request_config,
            },
            "workspace": {
                "root_path": "./workspace",
            },
            "max_iterations": 100,
            "completion_timeout": 600.0,
        }
        if leader_config.get("member_name"):
            agents["leader"]["member_name"] = leader_config["member_name"]
        if leader_config.get("name"):
            agents["leader"]["name"] = leader_config["name"]

        agents["teammate"] = {
            "model": {
                "model_client_config": model_client_config,
                "model_request_config": model_request_config,
            },
            "workspace": {
                "root_path": "./workspace",
            },
            "max_iterations": 100,
            "completion_timeout": 600.0,
        }

        team_config_dict["agents"] = agents
        return team_config_dict

    @staticmethod
    def _get_default_team_config() -> dict[str, Any]:
        return {
            "team_name": "jiuwen_team",
            "lifecycle": "persistent",
            "teammate_mode": "build_mode",
            "spawn_mode": "inprocess",
            "leader": {
                "member_name": "team_leader",
                "name": "TeamLeader",
                "persona": "project management expert",
                "domain": "project_management",
            },
            "transport": {
                "type": "inprocess",
            },
            "storage": {
                "type": "sqlite",
                "params": {
                    "connection_string": "./team_data/team.db",
                },
            },
        }

    async def create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
    ) -> TeamAgent:
        team_config = await self._get_config()
        logger.info("[TeamManager] building TeamAgentSpec: session_id=%s", session_id)

        team_spec = self._build_team_agent_spec(team_config, deep_agent, session_id)
        logger.info("[TeamManager] TeamAgentSpec ready: team_name=%s", team_spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] creating TeamAgent from spec")
            team_agent = team_spec.build()
            self._team_agents[session_id] = team_agent
            logger.info(
                "[TeamManager] Team created: session_id=%s, team_name=%s, lifecycle=%s",
                session_id,
                team_config.team_name,
                team_config.lifecycle,
            )
            return team_agent
        finally:
            reset_session_id(token)

    async def get_or_create_team(
        self,
        session_id: str,
        deep_agent: DeepAgent,
    ) -> TeamAgent:
        async with self._lock:
            team_agent = self._team_agents.get(session_id)
            if team_agent is not None:
                return team_agent

            await self._destroy_other_sessions(session_id)
            return await self.create_team(session_id, deep_agent)

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

        self._event_queues.pop(session_id, None)

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

    def register_event_queue(self, session_id: str, queue: asyncio.Queue) -> None:
        self._event_queues[session_id] = queue

    def register_monitor(self, session_id: str, handler: TeamMonitorHandler) -> None:
        self._team_monitors[session_id] = handler

    def register_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        self._stream_tasks[session_id] = task

    async def get_events(
        self,
        session_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        event_queue = self._event_queues.get(session_id)
        if not event_queue:
            return

        while True:
            if session_id not in self._stream_tasks:
                break

            try:
                event = await asyncio.wait_for(
                    event_queue.get(),
                    timeout=0.1,
                )
                yield event

                if isinstance(event, dict) and event.get("event_type") == "team.error":
                    break
            except asyncio.TimeoutError:
                if session_id not in self._stream_tasks:
                    break
                continue
            except Exception as exc:
                logger.error(
                    "[TeamManager] get events failed: session_id=%s error=%s",
                    session_id,
                    exc,
                )
                break


_team_manager: TeamManager | None = None


def get_team_manager() -> TeamManager:
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager


def reset_team_manager() -> None:
    global _team_manager
    _team_manager = None
