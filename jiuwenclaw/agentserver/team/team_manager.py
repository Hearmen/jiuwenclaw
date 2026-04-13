# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 生命周期管理器.

管理 Team 的创建、恢复、销毁等生命周期操作.
支持 Persistent 模式：团队任务完成后进入待命状态，可继续交互.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.spawn.context import set_session_id, reset_session_id
from openjiuwen.harness import DeepAgent

from jiuwenclaw.agentserver.team.config_loader import TeamConfig, load_team_config
from jiuwenclaw.agentserver.team.monitor_handler import TeamMonitorHandler
from jiuwenclaw.config import get_config

logger = logging.getLogger(__name__)


class TeamManager:
    """Team 生命周期管理器.

    管理多个 session 的 Team 实例，支持：
    - 创建新 Team
    - 恢复待命状态的 Team
    - 从崩溃中恢复 Team
    - 销毁 Team
    """

    def __init__(self):
        self._team_agents: dict[str, TeamAgent] = {}
        self._team_monitors: dict[str, TeamMonitorHandler] = {}
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._event_queues: dict[str, asyncio.Queue] = {}
        self._config: TeamConfig | None = None
        self._lock = asyncio.Lock()

    def has_stream_task(self, session_id: str) -> bool:
        """返回指定 session 是否已有流任务."""
        return session_id in self._stream_tasks

    def set_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        """注册指定 session 的流任务."""
        self._stream_tasks[session_id] = task

    def pop_stream_task(self, session_id: str) -> asyncio.Task | None:
        """移除并返回指定 session 的流任务."""
        return self._stream_tasks.pop(session_id, None)

    async def _get_config(self) -> TeamConfig:
        """获取 Team 配置（懒加载）."""
        if self._config is None:
            self._config = load_team_config()
        return self._config

    @staticmethod
    def _build_agent_customizer(
        deep_agent: DeepAgent,
    ) -> Callable[[DeepAgent], None]:
        """构建 agent_customizer 回调函数.

        用于在 TeamAgent 创建每个成员的 DeepAgent 时，注入 Claw 的标准 rails 和 tools.
        这样 leader 和所有 teammate 都具备与独立 DeepAdapter 相同的完整能力.

        Args:
            deep_agent: 参考的 DeepAgent 实例，用于提取 rails 和 tools

        Returns:
            customizer 函数: (DeepAgent) -> None
        """
        def customizer(agent: DeepAgent) -> None:
            """Customizer 回调：注入 Claw 能力."""
            for rail in deep_agent._registered_rails:
                try:
                    agent.add_rail(rail)
                except Exception as e:
                    logger.warning("[TeamManager] 添加 rail 失败: %s", e)

            for ability in deep_agent.ability_manager.list():
                try:
                    agent.ability_manager.add(ability)
                except Exception as e:
                    logger.warning("[TeamManager] 添加 ability 失败: %s", e)

            logger.debug("[TeamManager] Agent customizer 执行完成: %s", agent)

        return customizer

    def _build_team_agent_spec(
        self,
        team_config: TeamConfig,
        deep_agent: DeepAgent,
        session_id: str,
    ) -> TeamAgentSpec:
        """构建 TeamAgentSpec.

        从 config.yaml 的 team 配置读取基础配置，从 models.default 读取模型配置.
        使用 agent_customizer 回调方式，让每个成员的 DeepAgent 都具备 Claw 的完整能力.
        """
        config_base = get_config()
        team_config_dict = config_base.get("team", {})
        
        if not team_config_dict:
            team_config_dict = self._get_default_team_config()
        
        team_config_dict = self._build_agents_config(team_config_dict, config_base)
        
        team_config_dict["team_name"] = f"{team_config_dict.get('team_name', 'team')}_{session_id}"
        
        spec = TeamAgentSpec.model_validate(team_config_dict)
        spec.agent_customizer = self._build_agent_customizer(deep_agent)
        
        logger.info(
            "[TeamManager] TeamAgentSpec 构建完成: team_name=%s, agents=%s",
            spec.team_name,
            list(team_config_dict.get("agents", {}).keys()),
        )
        return spec
    
    @staticmethod
    def _build_agents_config(
        team_config_dict: dict[str, Any],
        config_base: dict[str, Any],
    ) -> dict[str, Any]:
        """构建 agents 配置.

        从 models.default 读取模型配置，动态构建 leader 和 teammate 的配置.
        """
        model_config = config_base.get("models", {}).get("default", {})
        model_client_config = model_config.get("model_client_config", {})
        model_request_config = model_config.get("model_config_obj", {})
        
        model_name = model_client_config.get("model_name", "")
        if model_name and "model" not in model_request_config:
            model_request_config = dict(model_request_config)
            model_request_config["model"] = model_name
        
        logger.info(
            "[TeamManager] 从 models.default 读取模型配置: model_name=%s, provider=%s",
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
        """获取默认的team配置."""
        return {
            "team_name": "jiuwen_team",
            "lifecycle": "persistent",
            "teammate_mode": "build_mode",
            "spawn_mode": "inprocess",
            "leader": {
                "member_name": "team_leader",
                "name": "TeamLeader",
                "persona": "天才项目管理专家",
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
        """创建新的 Team.

        Args:
            session_id: 会话 ID
            deep_agent: DeepAgent 实例作为 Leader

        Returns:
            TeamAgent: 创建的 TeamAgent 实例
        """
        if session_id in self._team_agents:
            await self._destroy_team(session_id)

        team_config = await self._get_config()
        logger.info("[TeamManager] 开始构建 TeamAgentSpec: session_id=%s", session_id)

        team_spec = self._build_team_agent_spec(team_config, deep_agent, session_id)
        logger.info("[TeamManager] TeamAgentSpec 构建完成: team_name=%s", team_spec.team_name)

        token = set_session_id(session_id)
        try:
            logger.info("[TeamManager] 开始调用 spec.build() 创建 TeamAgent")
            team_agent = team_spec.build()
            logger.info("[TeamManager] TeamAgent 创建成功")

            self._team_agents[session_id] = team_agent

            logger.info(
                "[TeamManager] Team 创建成功: session_id=%s, team_name=%s, lifecycle=%s",
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
        """获取或创建 Team,支持自动恢复.

        自动处理三种场景:
        1. 首次输入: 创建新Team
        2. 后续输入: 复用已存在的Team
        3. 崩溃恢复: 从数据库恢复Team

        Args:
            session_id: 会话 ID
            deep_agent: DeepAgent 实例作为 Leader

        Returns:
            TeamAgent: TeamAgent 实例
        """
        async with self._lock:
            team_agent = self._team_agents.get(session_id)

            if team_agent is not None:
                return team_agent

            team_agent = await self.create_team(session_id, deep_agent)
            return team_agent

    async def interact(self, session_id: str, user_input: str) -> bool:
        """向 Team 发送交互消息.

        Args:
            session_id: 会话 ID
            user_input: 用户输入

        Returns:
            bool: 是否成功
        """
        team_agent = self._team_agents.get(session_id)
        if team_agent is None:
            logger.warning(
                "[TeamManager] 交互失败，Team 不存在: session_id=%s",
                session_id,
            )
            return False

        try:
            await team_agent.interact(user_input)

            logger.debug(
                "[TeamManager] 交互消息已发送: session_id=%s, input=%s",
                session_id,
                user_input[:50],
            )

            return True

        except Exception as e:
            logger.error(
                "[TeamManager] 交互失败: session_id=%s, error=%s",
                session_id,
                e,
            )
            return False

    async def destroy_team(self, session_id: str) -> bool:
        """销毁 Team.

        Args:
            session_id: 会话 ID

        Returns:
            bool: 是否成功
        """
        async with self._lock:
            return await self._destroy_team(session_id)

    async def _destroy_team(self, session_id: str) -> bool:
        """内部方法：销毁 Team."""
        stream_task = self._stream_tasks.pop(session_id, None)
        if stream_task and not stream_task.done():
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass

        self._event_queues.pop(session_id, None)

        monitor_handler = self._team_monitors.pop(session_id, None)
        if monitor_handler is not None:
            try:
                await monitor_handler.stop()
            except Exception as e:
                logger.warning(
                    "[TeamManager] Monitor 停止失败: session_id=%s, error=%s",
                    session_id,
                    e,
                )

        team_agent = self._team_agents.pop(session_id, None)
        if team_agent is None:
            return False

        try:
            await team_agent.stop()

            logger.info(
                "[TeamManager] Team 已销毁: session_id=%s",
                session_id,
            )
            return True

        except Exception as e:
            logger.error(
                "[TeamManager] 销毁 Team 失败: session_id=%s, error=%s",
                session_id,
                e,
            )
            return False

    async def cleanup_all(self) -> None:
        """清理所有 Team."""
        async with self._lock:
            session_ids = list(self._team_agents.keys())
            for session_id in session_ids:
                await self._destroy_team(session_id)
            logger.info("[TeamManager] 所有 Team 已清理")

    def get_team_agent(self, session_id: str) -> TeamAgent | None:
        """获取 TeamAgent.

        Args:
            session_id: 会话 ID

        Returns:
            TeamAgent: TeamAgent 实例，如果不存在返回 None
        """
        return self._team_agents.get(session_id)

    def register_event_queue(self, session_id: str, queue: asyncio.Queue) -> None:
        """注册 session 的事件队列.

        Args:
            session_id: 会话 ID
            queue: 事件队列
        """
        self._event_queues[session_id] = queue

    def register_monitor(self, session_id: str, handler: TeamMonitorHandler) -> None:
        """注册 session 的 Monitor Handler.

        Args:
            session_id: 会话 ID
            handler: TeamMonitorHandler 实例
        """
        self._team_monitors[session_id] = handler

    def register_stream_task(self, session_id: str, task: asyncio.Task) -> None:
        """注册 session 的流任务.

        Args:
            session_id: 会话 ID
            task: asyncio.Task 实例
        """
        self._stream_tasks[session_id] = task

    async def get_events(
        self,
        session_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """获取 Team 的事件流.

        Args:
            session_id: 会话 ID

        Yields:
            dict: 事件数据
        """
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
            except Exception as e:
                logger.error(
                    "[TeamManager] 获取事件失败: session_id=%s, error=%s",
                    session_id,
                    e,
                )
                break

_team_manager: TeamManager | None = None


def get_team_manager() -> TeamManager:
    """获取全局 TeamManager 实例.

    Returns:
        TeamManager: 全局实例
    """
    global _team_manager
    if _team_manager is None:
        _team_manager = TeamManager()
    return _team_manager


def reset_team_manager() -> None:
    """重置全局 TeamManager 实例（用于测试）."""
    global _team_manager
    _team_manager = None
