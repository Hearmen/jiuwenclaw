# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentManager - 管理 Agent 实例."""

from __future__ import annotations

import logging
import uuid
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.interface import JiuWenClaw


logger = logging.getLogger(__name__)


ACP_DEFAULT_CAPABILITIES: dict[str, Any] = {
    "protocolVersion": "0.1.0",
    "serverInfo": {
        "name": "JiuWenClaw",
        "version": "1.0.0",
    },
    "capabilities": {
        "tools": True,
        "resources": True,
        "streaming": True,
    },
}


class AgentManager:
    """管理多个 Agent 实例.

    支持多种通道:
    - "acp": ACP 协议通道
    - "default": 默认通道
    """

    def __init__(self) -> None:
        self.agents: dict[str, "JiuWenClaw"] = {}

    async def _create_agent(
        self, agent_key: str, config: dict[str, Any] | None = None
    ) -> "JiuWenClaw":
        """创建 Agent 实例.

        Args:
            agent_key: Agent 键（如 "acp" 或 "default"）
            config: 可选配置

        Returns:
            JiuWenClaw 实例
        """
        from jiuwenclaw.agentserver.interface import JiuWenClaw

        logger.info("[AgentManager] Creating %s agent", agent_key)
        agent = JiuWenClaw()
        await agent.create_instance(config)
        self.agents[agent_key] = agent
        logger.info("[AgentManager] %s agent created", agent_key)
        return agent

    async def initialize(
        self, channel_id: str = "", extra_config: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """初始化 AgentManager.

        对于 ACP 通道，创建 agent 并返回 capabilities。

        Args:
            channel_id: 通道 ID
            extra_config: 额外配置（如 protocol_version, client_capabilities）

        Returns:
            对于 ACP 通道，返回 capabilities；对于其他通道，返回 None
        """
        if channel_id == "acp":
            logger.info("[AgentManager] ACP initialize")

            if "acp" in self.agents:
                logger.info("[AgentManager] Resetting ACP agent")
                try:
                    await self.agents["acp"].cleanup()
                except Exception as e:
                    logger.warning("[AgentManager] ACP agent cleanup failed: %s", e)
                del self.agents["acp"]

            config: dict[str, Any] = {"agent_name": "acp_agent"}
            if extra_config:
                config.update(extra_config)
            await self._create_agent("acp", config)

            return ACP_DEFAULT_CAPABILITIES.copy()
        return None

    async def create_session(self, channel_id: str = "") -> str:
        """创建会话.

        Args:
            channel_id: 通道 ID

        Returns:
            会话 ID
        """
        if channel_id == "acp":
            session_id = f"acp_{uuid.uuid4().hex[:8]}"
            logger.info("[AgentManager] ACP session created: session_id=%s", session_id)
            return session_id
        return "default"

    async def get_agent(self, channel_id: str = "") -> "JiuWenClaw | None":
        """获取 Agent 实例（自动创建）.

        如果 agent 不存在，会自动创建（仅用于非 ACP 场景）。

        Args:
            channel_id: 通道 ID

        Returns:
            JiuWenClaw | None: Agent 实例
        """
        agent_key = channel_id if channel_id == "acp" else "default"
        if agent_key not in self.agents:
            config: dict[str, Any] | None = None
            if channel_id == "acp":
                config = {"agent_name": "acp_agent"}
            await self._create_agent(agent_key, config)
        return self.agents.get(agent_key)

    def get_agent_nowait(self, channel_id: str = "") -> "JiuWenClaw | None":
        """获取 Agent 实例（同步，不自动创建）.

        Args:
            channel_id: 通道 ID

        Returns:
            JiuWenClaw | None: Agent 实例，如果不存在则返回 None
        """
        agent_key = channel_id if channel_id == "acp" else "default"
        return self.agents.get(agent_key)

    async def cleanup(self) -> None:
        """清理所有 agent 实例."""
        for key in list(self.agents.keys()):
            if hasattr(self.agents[key], "cleanup"):
                try:
                    await self.agents[key].cleanup()
                except Exception as e:
                    logger.warning("[AgentManager] Agent cleanup failed: %s", e)
            del self.agents[key]
        logger.info("[AgentManager] All agents cleaned up")
