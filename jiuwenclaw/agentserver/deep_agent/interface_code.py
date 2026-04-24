# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuWenClaw Code Adapter — code 模式配置驱动适配器.

继承 JiuWenClawDeepAdapter，重写 create_instance() 和 rails/tools 注册方法。
从 config.yaml::modes.code.rails/tools 读取配置列表，
通过名字映射查找构建方法来注册。
统一使用 create_deep_agent()，不再使用 create_code_agent()。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness import DeepAgent, VisionModelConfig, AudioModelConfig
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.rails import (
    AgentModeRail,
    AskUserRail,
    ConfirmInterruptRail,
    SkillUseRail,
    SkillEvolutionRail,
    SecurityRail,
    TaskPlanningRail,
)
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.rails.context_engineering_rail import ContextEngineeringRail
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.heartbeat_rail import HeartbeatRail
from openjiuwen.harness.rails.memory_rail import MemoryRail
from openjiuwen.harness.rails.subagent_rail import SubagentRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config
from openjiuwen.harness.subagents.plan_agent import build_plan_agent_config
from openjiuwen.harness.tools import WebFetchWebpageTool, WebFreeSearchTool, WebPaidSearchTool
from openjiuwen.harness.workspace.workspace import Workspace

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers import build_permission_rail
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_identity_prompt
from jiuwenclaw.agentserver.deep_agent.rails import (
    JiuClawContextEngineeringRail,
    JiuClawStreamEventRail,
    ResponsePromptRail,
    RuntimePromptRail,
)
from jiuwenclaw.agentserver.permissions.core import init_permission_engine
from jiuwenclaw.agentserver.tools import SkillToolkit
from jiuwenclaw.config import get_config

logger = logging.getLogger(__name__)

# 名字 → 构建方法映射（rail/tool 名字与类方法名对照）
_RAIL_BUILD_NAMES: dict[str, str] = {
    "FileSystemRail": "_build_filesystem_rail",
    "SkillUseRail": "_build_skill_rail_via_config",
    "LspRail": "_build_lsp_rail_via_config",
    "HeartbeatRail": "_build_heartbeat_rail",
    "AvatarPromptRail": "_build_avatar_rail",
    "TaskPlanningRail": "_build_task_planning_rail",
    "SubagentRail": "_build_subagent_rail",
    "MemoryRail": "_build_memory_rail_via_config",
    "ContextEngineeringRail": "_build_context_engineering_rail",
    "SkillEvolutionRail": "_build_skill_evolution_rail_via_config",
}

_TOOL_BUILD_NAMES: dict[str, str] = {
    "web_free_search": "_build_web_free_search_tool",
    "web_fetch_webpage": "_build_web_fetch_webpage_tool",
    "web_paid_search": "_build_paid_search_tool",
    "user_todos": "_build_user_todos_tool",
    "skill_toolkit": "_build_skill_toolkit",
}


def _subagent_list_has_name(
    subagents: list[SubAgentConfig | DeepAgent], name: str
) -> bool:
    for spec in subagents:
        if isinstance(spec, SubAgentConfig):
            if spec.agent_card.name == name:
                return True
        else:
            card = getattr(spec, "card", None)
            if getattr(card, "name", None) == name:
                return True
    return False


def _append_explore_and_plan_subagents(
    subagents: list[SubAgentConfig | DeepAgent],
    resolved_language: str,
    model: Model,
) -> list[SubAgentConfig | DeepAgent]:
    effective = list(subagents)
    if not _subagent_list_has_name(effective, "explore_agent"):
        effective.append(
            build_explore_agent_config(
                model=model,
                language=resolved_language,
                max_iterations=25,
            )
        )
    if not _subagent_list_has_name(effective, "plan_agent"):
        effective.append(
            build_plan_agent_config(
                model=model,
                language=resolved_language,
                max_iterations=25,
            )
        )
    return effective


class JiuwenClawCodeAdapter(JiuWenClawDeepAdapter):
    """Code 模式适配器 — 配置驱动注册 rails/tools.

    继承 JiuWenClawDeepAdapter，只重写：
    - create_instance(): 统一使用 create_deep_agent()，不传多模态/上下文引擎参数
    - _build_agent_rails(): 固定 Rails + 从 config.yaml 读取动态 Rails
    - _get_tool_cards(): 从 config.yaml 读取动态 Tools
    """

    async def create_instance(self, config: dict[str, Any] | None = None, *, mode: str = "code") -> None:
        """初始化 DeepAgent 实例（code 模式）.

        统一使用 create_deep_agent()，不传 vision_model_config /
        audio_model_config / context_engine_config / completion_timeout。
        """
        await self.set_checkpoint()

        self._instance_overrides = dict(config or {}) if isinstance(config, dict) else {}
        config_base = get_config()
        self._refresh_multimodal_configs(config_base)
        config = config_base.get('react', {}).copy()
        self._config_cache = config.copy()
        self._agent_name = self._instance_overrides.get(
            "agent_name", config.get("agent_name", "main_agent")
        )
        self._project_dir = config.get("workspace_dir")

        model = self._create_model(config_base)
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')

        tool_cards = await self._get_tool_cards(agent_card.id)
        self._tool_cards = tool_cards

        permissions_cfg = config_base.get("permissions", {})
        init_permission_engine(permissions_cfg)
        logger.info(
            "[JiuwenClawCodeAdapter] Permission engine initialized: enabled=%s",
            permissions_cfg.get("enabled", True),
        )

        rails_list = self._build_agent_rails(config, config_base, mode="code")

        sys_operation = self._create_sys_operation()
        if sys_operation is None:
            raise RuntimeError("sys_operation is not available, maybe task is not running")
        self._sys_operation = sys_operation

        raw_subagents = self._build_configured_subagents(model, config, config_base) or []
        configured_subagents = _append_explore_and_plan_subagents(
            raw_subagents,
            resolved_language=self._resolve_runtime_language(),
            model=model,
        )

        self._instance = create_deep_agent(
            model=model,
            card=agent_card,
            system_prompt=build_identity_prompt(
                mode="agent.fast",
                language=self._resolve_prompt_language(),
                channel=(
                    "acp" if self._is_acp_tool_profile(self._instance_overrides)
                    else self._resolve_prompt_channel()
                ),
            ),
            tools=tool_cards if tool_cards else [],
            subagents=configured_subagents,
            rails=rails_list if rails_list else [],
            enable_task_loop=config.get("enable_task_loop", True),
            max_iterations=config.get("max_iterations", 15),
            workspace=Workspace(
                root_path=self._project_dir or "./",
                language=self._resolve_runtime_language(),
            ),
            sys_operation=sys_operation,
            language=self._resolve_runtime_language(),
            enable_task_planning=True
        )
        # code 模式不传: vision_model_config, audio_model_config,
        # context_engine_config, completion_timeout

        self._registered_mcp_server_ids.clear()
        self._registered_mcp_servers.clear()
        await self._register_mcp_servers_from_config(config_base, tag="code")
        logger.info("[JiuwenClawCodeAdapter] 初始化完成: agent_name=%s", self._agent_name)

        await self.load_user_rails()

    def _build_agent_rails(
            self,
            config: dict[str, Any],
            config_base: dict[str, Any],
            *,
            mode: str = "code",
    ) -> list[Any]:
        """Build rails for code mode: fixed rails + dynamic rails from config."""

        @dataclass
        class _RailBuildInfo:
            attr_name: str
            build_func: Any
            params: dict = None

            def __post_init__(self):
                self.params = self.params or {}

        # 固定 Rails — 写死在代码中
        rail_infos = [
            _RailBuildInfo("_runtime_prompt_rail", self._build_runtime_prompt_rail),
            _RailBuildInfo("_response_prompt_rail", self._build_response_prompt_rail),
            _RailBuildInfo("_stream_event_rail", self._build_stream_event_rail),
            _RailBuildInfo("_security_rail", self._build_security_rail),
            _RailBuildInfo(
                "_permission_rail",
                build_permission_rail,
                {
                    "config": config_base,
                    "llm": self._model,
                    "model_name": config_base.get("models", {}).get(
                        "default", {}
                    ).get("model_client_config", {}).get("model_name", "gpt-4"),
                },
            ),
            _RailBuildInfo("_code_filesystem_rail", FileSystemRail, {}),
            _RailBuildInfo("_code_agent_mode_rail", AgentModeRail, {}),
            _RailBuildInfo("_code_ask_user_rail", AskUserRail, {}),
            _RailBuildInfo(
                "_code_confirm_interrupt_rail",
                ConfirmInterruptRail,
                {"tool_names": ["switch_mode"]},
            ),
        ]

        # 动态 Rails — 从 config.yaml::modes.code.rails 读取
        mode_config = config_base.get("modes", {}).get("code", {})
        configured_rails = mode_config.get("rails") or []

        for rail_name in configured_rails:
            rail_instance = self._get_rail_build_func(rail_name)
            if rail_instance is None:
                logger.warning(
                    "[JiuwenClawCodeAdapter] Unknown rail name in config: %s, skipping",
                    rail_name,
                )
                continue
            attr_name = f"_dynamic_{rail_name}"
            setattr(self, attr_name, rail_instance)

            def _make_passthrough(inst):
                return lambda **kw: inst

            rail_infos.append(
                _RailBuildInfo(attr_name, _make_passthrough(rail_instance))
            )
            logger.info(
                "[JiuwenClawCodeAdapter] Dynamic rail %s registered from config",
                rail_name,
            )

        # 构建并注册
        rails_list = []
        for info in rail_infos:
            logger.info(
                "[JiuwenClawCodeAdapter] Building rail: %s with params: %s",
                info.attr_name, info.params,
            )
            rail_instance = info.build_func(**info.params)
            if rail_instance is not None:
                setattr(self, info.attr_name, rail_instance)
                rails_list.append(rail_instance)
                logger.info(
                    "[JiuwenClawCodeAdapter] Rail %s built successfully",
                    info.attr_name,
                )
            else:
                logger.warning(
                    "[JiuwenClawCodeAdapter] Rail %s build returned None",
                    info.attr_name,
                )
        logger.info(
            "[JiuwenClawCodeAdapter] Total rails built: %d, rail names: %s",
            len(rails_list),
            [type(r).__name__ for r in rails_list],
        )
        return rails_list

    def _get_rail_build_func(self, rail_name: str) -> Any | None:
        """根据 rail 名字调用对应构建方法."""
        method_name = _RAIL_BUILD_NAMES.get(rail_name)
        if method_name is None:
            return None
        method = getattr(self, method_name, None)
        if method is None:
            return None
        return method()

    def _build_lsp_rail_via_config(self) -> Any:
        """构建 LspRail（带 project_dir 参数）."""
        logger.info(
            "[JiuwenClawCodeAdapter] Building LspRail with project_dir=%s",
            self._project_dir,
        )
        return self._build_lsp_rail(workspace_dir=self._project_dir)

    def _build_skill_rail_via_config(self) -> Any:
        """构建 SkillUseRail（从 config 读取参数）."""
        return self._build_skill_rail(
            self._config_cache,
            include_tools=self._skill_include_tools_for_profile(),
        )

    def _build_memory_rail_via_config(self) -> Any:
        """构建 MemoryRail."""
        return self._build_memory_rail("code")

    def _build_context_engineering_rail(self) -> Any:
        """构建 ContextEngineeringRail."""
        return JiuClawContextEngineeringRail(preset=False)

    def _build_skill_evolution_rail_via_config(self) -> Any:
        """构建 SkillEvolutionRail."""
        return self._build_skill_evolution_rail(get_config())

    async def _get_tool_cards(self, agent_id: str) -> list[Any]:
        """Get tool cards for code mode — from config.yaml::modes.code.tools."""

        tool_cards = []

        config_base = get_config()
        mode_config = config_base.get("modes", {}).get("code", {})
        configured_tools = mode_config.get("tools") or []

        for tool_name in configured_tools:
            result = self._get_tool_build_func(tool_name, agent_id)
            if result is None:
                logger.warning(
                    "[JiuwenClawCodeAdapter] Unknown or failed tool: %s, skipped",
                    tool_name,
                )
                continue
            if isinstance(result, list):
                for tool_instance in result:
                    if not Runner.resource_mgr.get_tool(tool_instance.card.id):
                        Runner.resource_mgr.add_tool(tool_instance)
                    tool_cards.append(tool_instance.card)
            else:
                Runner.resource_mgr.add_tool(result)
                tool_cards.append(result.card)
            logger.info(
                "[JiuwenClawCodeAdapter] Tool %s registered from config",
                tool_name,
            )

        return tool_cards

    def _get_tool_build_func(self, tool_name: str, agent_id: str) -> Any | None:
        """根据 tool 名字调用对应构建方法."""
        method_name = _TOOL_BUILD_NAMES.get(tool_name)
        if method_name is None:
            logger.warning(
                "[JiuwenClawCodeAdapter] Unknown tool name in config: %s, skipping",
                tool_name,
            )
            return None
        method = getattr(self, method_name, None)
        if method is None:
            return None
        return method(agent_id)

    def _build_web_free_search_tool(self, agent_id: str) -> Any:
        """构建 web_free_search 工具."""
        return WebFreeSearchTool(
            language=self._resolve_runtime_language(), agent_id=agent_id
        )

    def _build_web_fetch_webpage_tool(self, agent_id: str) -> Any:
        """构建 web_fetch_webpage 工具."""
        return WebFetchWebpageTool(
            language=self._resolve_runtime_language(), agent_id=agent_id
        )

    def _build_paid_search_tool(self, agent_id: str) -> WebPaidSearchTool | None:
        """条件注册付费搜索工具：有任意一个付费 API Key 才注册."""
        if not any(
            os.environ.get(key)
            for key in ("BOCHA_API_KEY", "PERPLEXITY_API_KEY", "SERPER_API_KEY", "JINA_API_KEY")
        ):
            logger.info("[JiuwenClawCodeAdapter] web_paid_search skipped: no paid search API key")
            return None
        tool = WebPaidSearchTool(
            language=self._resolve_runtime_language(), agent_id=agent_id
        )
        self._paid_search_tool = tool
        self._paid_search_registered = True
        return tool

    def _build_user_todos_tool(self, agent_id: str) -> list[Any] | None:
        """注册 user_todos 工具."""
        try:
            from jiuwenclaw.agentserver.tools.user_todo_tool import (
                get_decorated_tools as _get_user_todo_tools,
                set_global_workspace_dir as _set_user_todo_workspace,
                set_global_channel_id as _set_user_todo_channel_id,
            )
            _set_user_todo_workspace(self._workspace_dir)
            _set_user_todo_channel_id(self._runtime_cron_tool_context.channel_id)
            tools = _get_user_todo_tools()
            return tools
        except ImportError:
            logger.info("[JiuwenClawCodeAdapter] user_todos skipped: module not importable")
            return None

    def _build_skill_toolkit(self, agent_id: str) -> list[Any] | None:
        """注册 SkillToolkit 工具."""
        try:
            skill_toolkit = SkillToolkit(manager=self._skill_manager)
            skill_tool_names: list[str] = []
            for tool in skill_toolkit.get_tools():
                if not Runner.resource_mgr.get_tool(tool.card.id):
                    Runner.resource_mgr.add_tool(tool)
                skill_tool_names.append(tool.card.name)
            logger.info(
                "[JiuwenClawCodeAdapter] SkillToolkit registered: tools=%s",
                skill_tool_names,
            )
            return skill_toolkit.get_tools()
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] skill_toolkit build failed: %s", exc)
            return None