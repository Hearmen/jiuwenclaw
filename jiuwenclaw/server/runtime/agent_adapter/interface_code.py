# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuWenClaw Code Adapter — code 模式配置驱动适配器.

继承 JiuWenClawDeepAdapter，重写 create_instance() 和 rails/tools 注册方法。
从 config.yaml::modes.code.rails/tools 读取配置列表，
通过名字映射查找构建方法来注册。
统一使用 create_deep_agent()，不再使用 create_code_agent()。

Code 模式独占逻辑全部收敛于此：
- LspRail、ProjectMemoryRail、CodingMemoryRail 等 code 专属 rail
- code_agent / explore_agent subagent 配置
- code 模式下 rail 生命周期（保留 SubagentRail、补充 ProjectMemoryRail 等）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails import (
    AgentModeRail,
    ConfirmInterruptRail,
    CodingMemoryRail,
    SysOperationRail,
    LspRail
)
from openjiuwen.harness.rails.context_engineer.context_assemble_rail import ContextAssembleRail
from openjiuwen.harness.lsp import InitializeOptions
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config
from openjiuwen.harness.subagents.code_agent import build_code_agent_config
from openjiuwen.harness.subagents.explore_agent import build_explore_agent_config
from openjiuwen.harness.subagents.plan_agent import build_plan_agent_config
from openjiuwen.harness.tools import WebFetchWebpageTool, WebFreeSearchTool, WebPaidSearchTool
from openjiuwen.harness.workspace.workspace import Workspace

from jiuwenclaw.server.runtime.agent_adapter.interface_deep import (
    JiuWenClawDeepAdapter,
    parse_int,
)
from jiuwenclaw.agents.harness.common.rails.interrupt.interrupt_helpers import build_permission_rail
from jiuwenclaw.agents.harness.common.prompt.prompt_builder import build_identity_prompt
from jiuwenclaw.agents.harness.common.rails import (
    ProjectMemoryRail,
    StructuredAskUserRail,
)
from jiuwenclaw.agents.harness.common.memory.config import get_memory_mode
from jiuwenclaw.agents.harness.common.tools import SkillToolkit
from jiuwenclaw.common.config import get_config
from jiuwenclaw.common.utils import get_agent_workspace_dir

logger = logging.getLogger(__name__)


# 名字 → 构建方法映射（rail/tool 名字与类方法名对照）
_RAIL_BUILD_NAMES: dict[str, str] = {
    "SysOperationRail": "_build_filesystem_rail",
    "FileSystemRail": "_build_filesystem_rail",     # 别名映射
    "SkillUseRail": "_build_skill_rail_via_config",
    "LspRail": "_build_lsp_rail_via_config",
    "HeartbeatRail": "_build_heartbeat_rail",
    "AvatarPromptRail": "_build_avatar_rail",
    "TaskPlanningRail": "_build_task_planning_rail",
    "SubagentRail": "_build_subagent_rail",
    "ContextAssembleRail": "_build_context_assemble_rail",
    "ContextProcessorRail": "_build_context_processor_rail",
    "SkillEvolutionRail": "_build_skill_evolution_rail_via_config",
    "ProjectMemoryRail": "_build_project_memory_rail",
    "CodingMemoryRail": "_build_coding_memory_rail",
}

_TOOL_BUILD_NAMES: dict[str, str] = {
    "web_free_search": "_build_web_free_search_tool",
    "web_fetch_webpage": "_build_web_fetch_webpage_tool",
    "web_paid_search": "_build_paid_search_tool",
    "user_todos": "_build_user_todos_tool",
    "skill_toolkit": "_build_skill_toolkit",
}


@dataclass
class _RailBuildInfo:
    """Rail 构建信息 — 统一固定和动态 Rails 的构建流程."""
    attr_name: str
    build_func: Callable
    params: dict = None

    def __post_init__(self):
        self.params = self.params or {}


class JiuwenClawCodeAdapter(JiuWenClawDeepAdapter):
    """Code 模式适配器 — 配置驱动注册 rails/tools.

    继承 JiuWenClawDeepAdapter，只重写：
    - create_instance(): 统一使用 create_deep_agent()，不传多模态/上下文引擎参数
    - _build_agent_rails(): 固定 Rails (含 LspRail/ProjectMemoryRail/CodingMemoryRail) + 从 config.yaml 读取动态 Rails
    - _get_tool_cards(): 从 config.yaml 读取动态 Tools
    - _build_configured_subagents(): 固定 explore_agent/plan_agent + 按配置启用 code_agent/browser_agent
    - _update_rails_for_mode(): code 模式 rail 生命周期
    - _update_runtime_config(): 保留 ProjectMemoryRail 语言同步
    """

    # 固定 Rails 名字集合 — 用于动态 Rails 去重
    _FIXED_RAIL_NAMES = frozenset({
        "RuntimePromptRail", "ResponsePromptRail",
        "JiuClawStreamEventRail", "SecurityRail",
        "LspRail", "ProjectMemoryRail", "PermissionInterruptRail",
        "ContextProcessorRail",
        "SysOperationRail", "CodingMemoryRail",
        "AgentModeRail", "StructuredAskUserRail", "ConfirmInterruptRail",
        "FileSystemRail",  # 别名
    })

    def __init__(self) -> None:
        super().__init__()
        # Code 模式专属 rails — 父类不定义这些属性
        self._lsp_rail: LspRail | None = None
        self._project_memory_rail: ProjectMemoryRail | None = None
        self._coding_memory_rail: CodingMemoryRail | None = None

    # ─── 初始化 ──────────────────────────────

    async def create_instance(self, config: dict[str, Any] | None = None, *,
                              mode: str = "code", sub_mode: str = None) -> None:
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
        self._project_dir = self._instance_overrides.get(
            "project_dir", config.get("project_dir")
        )
        self._workspace_dir = config.get("workspace_dir", str(get_agent_workspace_dir()))

        model = self._create_model(config_base)
        agent_card = AgentCard(name=self._agent_name, id='jiuwenclaw')

        tool_cards = await self._get_tool_cards(agent_card.id)
        self._tool_cards = tool_cards

        # 权限护栏由 openjiuwen PermissionInterruptRail + ToolPermissionHost 接管；
        # 无需初始化 jiuwenclaw 内置 PermissionEngine（已弃用）。

        rails_list = self._build_agent_rails(config, config_base, mode="code")

        sys_operation = self._create_sys_operation()
        if sys_operation is None:
            raise RuntimeError("sys_operation is not available, maybe task is not running")
        self._sys_operation = sys_operation

        configured_subagents = self._build_configured_subagents(model, config, config_base) or []

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
                root_path=self._workspace_dir or "./",
                language=self._resolve_runtime_language(),
            ),
            sys_operation=sys_operation,
            language=self._resolve_runtime_language(),
            enable_task_planning=True,
            auto_create_workspace=False
        )

        # code 模式不传: vision_model_config, audio_model_config,
        # context_engine_config, completion_timeout

        self._registered_mcp_server_ids.clear()
        self._registered_mcp_servers.clear()
        await self._register_mcp_servers_from_config(config_base, tag="code")
        logger.info("[JiuwenClawCodeAdapter] 初始化完成: agent_name=%s", self._agent_name)

        await self.load_user_rails()

    # ─── Rails 构建 ──────────────────────────

    def _build_agent_rails(
            self,
            config: dict[str, Any],
            config_base: dict[str, Any],
            *,
            mode: str = "code",
    ) -> list[Any]:
        """Build rails for code mode: fixed rails + dynamic rails from config.

        Code 模式固定包含 LspRail、ProjectMemoryRail、CodingMemoryRail。
        """
        # 固定 Rails — code 模式特有
        rail_infos = [
            _RailBuildInfo("_runtime_prompt_rail", self._build_runtime_prompt_rail),
            _RailBuildInfo("_response_prompt_rail", self._build_response_prompt_rail),
            _RailBuildInfo("_stream_event_rail", self._build_stream_event_rail),
            _RailBuildInfo("_security_rail", self._build_security_rail),
            _RailBuildInfo("_lsp_rail", self._build_lsp_rail_via_config),
            _RailBuildInfo("_project_memory_rail", self._build_project_memory_rail),
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
            _RailBuildInfo("_code_filesystem_rail", self._build_filesystem_rail),
            _RailBuildInfo("_coding_memory_rail", self._build_coding_memory_rail),
            _RailBuildInfo("_code_agent_mode_rail", self._build_agent_mode_rail),
            _RailBuildInfo("_code_ask_user_rail", self._build_structured_ask_user_rail),
            _RailBuildInfo(
                "_code_confirm_interrupt_rail",
                self._build_confirm_interrupt_rail,
                {"tool_names": ["switch_mode"]},
            ),
            _RailBuildInfo("_context_processor_rail", self._build_context_processor_rail),
        ]

        # 动态 Rails — 从 config.yaml::modes.code.rails 读取
        # 跳过已在固定列表中的 rail，避免重复注册
        mode_config = config_base.get("modes", {}).get("code", {})
        configured_rails = mode_config.get("rails") or []

        for rail_name in configured_rails:
            if rail_name in self._FIXED_RAIL_NAMES:
                logger.info(
                    "[JiuwenClawCodeAdapter] Rail %s already in fixed set, skipping dynamic registration",
                    rail_name,
                )
                continue
            method_name = _RAIL_BUILD_NAMES.get(rail_name)
            if method_name is None:
                if rail_name == "MemoryRail":
                    logger.warning(
                        "[JiuwenClawCodeAdapter] MemoryRail is not supported in code mode; "
                        "use CodingMemoryRail instead. Skipping",
                    )
                else:
                    logger.warning(
                        "[JiuwenClawCodeAdapter] Unknown rail name in config: %s, skipping",
                        rail_name,
                    )
                continue
            method = getattr(self, method_name, None)
            if method is None:
                logger.warning(
                    "[JiuwenClawCodeAdapter] Build method %s not found, skipping",
                    method_name,
                )
                continue
            attr_name = f"_dynamic_{rail_name}"
            rail_infos.append(_RailBuildInfo(attr_name, method))
            logger.info(
                "[JiuwenClawCodeAdapter] Dynamic rail %s queued from config",
                rail_name,
            )

        # 统一构建并注册
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

    # ─── Code 专属 Rail 构建 ────────────────

    def _build_filesystem_rail(self) -> SysOperationRail | None:
        """构建 SysOperationRail（FileSystemRail）."""
        try:
            fs_rail = SysOperationRail()
            logger.info("[JiuwenClawCodeAdapter] SysOperationRail create success")
            return fs_rail
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] SysOperationRail create failed: %s", exc)
            return None

    def _build_agent_mode_rail(self) -> AgentModeRail | None:
        """构建 AgentModeRail."""
        try:
            return AgentModeRail()
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] AgentModeRail create failed: %s", exc)
            return None

    def _build_structured_ask_user_rail(self) -> StructuredAskUserRail | None:
        """构建 StructuredAskUserRail."""
        try:
            return StructuredAskUserRail()
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] StructuredAskUserRail create failed: %s", exc)
            return None

    def _build_confirm_interrupt_rail(self, tool_names: list[str] | None = None) -> ConfirmInterruptRail | None:
        """构建 ConfirmInterruptRail."""
        try:
            return ConfirmInterruptRail(tool_names=tool_names or ["switch_mode"])
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] ConfirmInterruptRail create failed: %s", exc)
            return None

    def _build_lsp_rail_via_config(self) -> Any:
        """构建 LspRail（带 project_dir 参数）."""
        logger.info(
            "[JiuwenClawCodeAdapter] Building LspRail with project_dir=%s",
            self._project_dir,
        )
        return self._build_lsp_rail(workspace_dir=self._project_dir)

    def _build_lsp_rail(self, workspace_dir: str | None = None) -> LspRail | None:
        """Build LspRail（code 模式专属）."""
        try:
            lsp_rail = LspRail(InitializeOptions(cwd=workspace_dir))
            logger.info("[JiuwenClawCodeAdapter] LspRail create success")
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] LspRail create failed: %s", exc)
            lsp_rail = None
        return lsp_rail

    def _build_coding_memory_rail(self) -> CodingMemoryRail | None:
        """构建 CodingMemoryRail（主 Agent 和 code_agent subagent 共用）.

        通过 self._coding_memory_rail 缓存避免重复构建。
        """
        # 单例保护：如果已构建，直接返回缓存实例
        if self._coding_memory_rail is not None:
            logger.info("[JiuwenClawCodeAdapter] CodingMemoryRail reuse cached instance")
            return self._coding_memory_rail

        try:
            config = get_config()
            embed_config = config.get("embed") if isinstance(config, dict) else None

            has_api_key = embed_config.get("embed_api_key") if isinstance(embed_config, dict) else None
            has_base_url = embed_config.get("embed_base_url") if isinstance(embed_config, dict) else None
            has_model = embed_config.get("embed_model") if isinstance(embed_config, dict) else None
            if not all([has_api_key, has_base_url, has_model]):
                logger.warning("[JiuwenClawCodeAdapter] CodingMemoryRail: no embedding config, skipping")
                return None

            language = config.get("preferred_language", "zh")
            coding_memory_dir = os.path.join(self._workspace_dir, "coding_memory")
            os.makedirs(coding_memory_dir, exist_ok=True)

            coding_memory_rail = CodingMemoryRail(
                coding_memory_dir=coding_memory_dir,
                embedding_config=EmbeddingConfig(
                    model_name=embed_config.get("embed_model"),
                    base_url=embed_config.get("embed_base_url"),
                    api_key=embed_config.get("embed_api_key"),
                ),
                language="cn" if language == "zh" else "en",
            )
            # 缓存实例，供 code_agent 复用
            self._coding_memory_rail = coding_memory_rail
            logger.info("[JiuwenClawCodeAdapter] CodingMemoryRail create success")
            return coding_memory_rail

        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] CodingMemoryRail create failed: %s", exc)
            return None

    def _build_project_memory_rail(self) -> ProjectMemoryRail | None:
        """Build ProjectMemoryRail to auto-load JIUWENCLAW.md / CLAUDE.md etc.

        Code 模式专属 — 始终挂载。
        确保能检索到 /init 命令创建 JIUWENCLAW.md 的目录（当前工作目录）。
        """
        try:
            workspace = self._project_dir or self._workspace_dir or "./"
            language = self._resolve_runtime_language()
            raw_additional_dirs = self._instance_overrides.get(
                "project_memory_additional_directories",
                self._config_cache.get("project_memory", {}).get("additional_directories"),
            )
            if raw_additional_dirs is None:
                raw_additional_dirs = os.getenv("JIUWENCLAW_ADDITIONAL_DIRECTORIES", "")

            if isinstance(raw_additional_dirs, str):
                additional_dirs = [
                    item.strip()
                    for item in raw_additional_dirs.split(os.pathsep)
                    if item.strip()
                ]
            elif isinstance(raw_additional_dirs, (list, tuple, set)):
                additional_dirs = [
                    str(item).strip()
                    for item in raw_additional_dirs
                    if str(item).strip()
                ]
            else:
                additional_dirs = []

            rail = ProjectMemoryRail(
                workspace=workspace,
                language=language,
                additional_directories=tuple(additional_dirs),
            )
            logger.info(
                "[JiuwenClawCodeAdapter] ProjectMemoryRail create success "
                "(workspace=%s, language=%s, additional_dirs=%d)",
                workspace,
                language,
                len(additional_dirs),
            )
            return rail
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[JiuwenClawCodeAdapter] ProjectMemoryRail create failed: %s", exc,
            )
            return None

    # ─── 配置驱动的 Rail/Tool 构建代理 ──────────

    def _build_skill_rail_via_config(self) -> Any:
        """构建 SkillUseRail（从 config 读取参数）."""
        include_tools = not self._is_acp_tool_profile(self._instance_overrides)
        return self._build_skill_rail(
            self._config_cache,
            include_tools=include_tools,
        )

    def _build_context_assemble_rail(self) -> Any:
        """构建 ContextEngineeringRail."""
        return ContextAssembleRail()

    def _build_context_processor_rail(self) -> Any:
        """构建 ContextProcessorRail — 复用父类逻辑."""
        from jiuwenclaw.server.runtime.agent_adapter.interface_deep import _build_context_processor_rail
        return _build_context_processor_rail(self._config_cache)

    def _build_skill_evolution_rail_via_config(self) -> Any:
        """构建 SkillEvolutionRail."""
        return self._build_skill_evolution_rail(get_config())

    # ─── Subagent 配置 ──────────────────────────

    @staticmethod
    def _subagent_list_has_name(subagents: list, name: str) -> bool:
        """检查 subagents 列表中是否已包含指定名字的 subagent."""
        for spec in subagents:
            if isinstance(spec, SubAgentConfig):
                if spec.agent_card.name == name:
                    return True
            else:
                card = getattr(spec, "card", None)
                if getattr(card, "name", None) == name:
                    return True
        return False

    def _build_configured_subagents(
            self,
            model: Model,
            config: dict[str, Any],
            config_base: dict[str, Any] | None = None,
    ) -> list[Any] | None:
        """Build subagents for code mode: explore_agent + plan_agent + code_agent + browser_agent.

        explore_agent / plan_agent 固定挂载（Code 模式核心子代理）。
        code_agent / browser_agent 按配置启用。
        """
        react_cfg = config if isinstance(config, dict) else {}
        subagents_cfg = react_cfg.get("subagents")

        resolved_language = self._resolve_runtime_language()
        workspace = self._workspace_dir or "./"
        subagents: list[Any] = []

        # ── 固定挂载：explore_agent（Code 模式核心子代理，始终启用）──
        if not self._subagent_list_has_name(subagents, "explore_agent"):
            explore_agent_cfg = subagents_cfg.get("explore_agent") if isinstance(subagents_cfg, dict) else None
            subagents.append(
                build_explore_agent_config(
                    model=model,
                    workspace=workspace,
                    language=resolved_language,
                    max_iterations=parse_int(
                        explore_agent_cfg.get("max_iterations") if isinstance(explore_agent_cfg, dict) else None,
                        react_cfg.get("max_iterations", 15),
                    ),
                )
            )

        # ── 固定挂载：plan_agent（Code 模式核心子代理，始终启用）──
        if not self._subagent_list_has_name(subagents, "plan_agent"):
            plan_agent_cfg = subagents_cfg.get("plan_agent") if isinstance(subagents_cfg, dict) else None
            subagents.append(
                build_plan_agent_config(
                    model=model,
                    workspace=workspace,
                    language=resolved_language,
                    max_iterations=parse_int(
                        plan_agent_cfg.get("max_iterations") if isinstance(plan_agent_cfg, dict) else None,
                        react_cfg.get("max_iterations", 15),
                    ),
                )
            )

        if isinstance(subagents_cfg, dict):
            # code_agent subagent — 按配置启用
            code_agent_cfg = subagents_cfg.get("code_agent")
            if self._is_subagent_enabled(code_agent_cfg):
                code_agent_rails = None
                # 复用主 Agent 已构建的 CodingMemoryRail
                coding_memory_rail = self._coding_memory_rail
                if coding_memory_rail is not None:
                    # SysOperationRail is default rail for code_agent;
                    # passing rails overrides defaults, must include it explicitly
                    code_agent_rails = [SysOperationRail(), coding_memory_rail]
                subagents.append(
                    build_code_agent_config(
                        model,
                        workspace=workspace,
                        language=resolved_language,
                        rails=code_agent_rails,
                        max_iterations=parse_int(
                            code_agent_cfg.get("max_iterations"),
                            react_cfg.get("max_iterations", 15),
                        ),
                    )
                )

            # browser_agent
            browser_agent_cfg = subagents_cfg.get("browser_agent")
            browser_enabled = self._browser_runtime_enabled()
            if browser_enabled:
                if not str(os.getenv("BROWSER_DRIVER") or "").strip():
                    os.environ["BROWSER_DRIVER"] = "managed"
                    logger.info(
                        "[JiuwenClawCodeAdapter] browser subagent enabled without BROWSER_DRIVER; "
                        "defaulting to managed mode"
                    )
                if not str(os.getenv("BROWSER_MANAGED_BINARY") or "").strip():
                    chrome_path = self._resolve_managed_browser_binary_from_config()
                    if chrome_path:
                        os.environ["BROWSER_MANAGED_BINARY"] = chrome_path
                        logger.info(
                            "[JiuwenClawCodeAdapter] using browser.chrome_path for managed browser: %s",
                            chrome_path,
                        )
                subagents.append(
                    build_browser_agent_config(
                        model,
                        workspace=workspace,
                        language=resolved_language,
                        max_iterations=parse_int(
                            browser_agent_cfg.get("max_iterations") if isinstance(browser_agent_cfg, dict) else None,
                            react_cfg.get("max_iterations", 15),
                        )
                    )
                )

        return subagents or None

    # ─── Rail 生命周期(mode切换) ───────────────────

    async def _update_rails_for_mode(self, mode: str) -> None:
        """Code 模式下的 rail 生命周期管理.

        code.normal / code.plan 等模式：
        - 保留 SubagentRail（主 Agent 通过 task_tool 派发 explore/plan 子代理）
        - 保留 ProjectMemoryRail（code 模式始终挂载）
        - 保留 CodingMemoryRail（code 模式始终挂载）
        - 卸载 TaskPlanningRail、SkillEvolutionRail
        """
        # 卸载非 code 专属 rails
        rail_specs = (
            ("_task_planning_rail", "TaskPlanningRail"),
            ("_skill_evolution_rail", "SkillEvolutionRail"),
        )

        for attr, label in rail_specs:
            rail = getattr(self, attr, None)
            if rail is not None:
                await self._instance.unregister_rail(rail)
                setattr(self, attr, None)
                logger.info(
                    "[JiuwenClawCodeAdapter] %s unregistered for %s mode",
                    label, mode,
                )

        # code 模式保留 SubagentRail；若缺失则补充注册
        if self._subagent_rail is None:
            self._subagent_rail = self._build_subagent_rail()
            if self._subagent_rail is not None:
                await self._instance.register_rail(self._subagent_rail)
                logger.info(
                    "[JiuwenClawCodeAdapter] SubagentRail (re)registered for %s",
                    mode,
                )

        # code 模式保留 ProjectMemoryRail；若缺失则补充注册
        if self._project_memory_rail is None:
            self._project_memory_rail = self._build_project_memory_rail()
            if self._project_memory_rail is not None:
                await self._instance.register_rail(self._project_memory_rail)
                logger.info(
                    "[JiuwenClawCodeAdapter] ProjectMemoryRail (re)registered for %s",
                    mode,
                )

        # code 模式保留 CodingMemoryRail；若缺失则补充注册
        if self._coding_memory_rail is None:
            coding_memory_rail = self._build_coding_memory_rail()
            if coding_memory_rail is not None:
                # _build_coding_memory_rail 已缓存到 self._coding_memory_rail
                await self._instance.register_rail(coding_memory_rail)
                logger.info(
                    "[JiuwenClawCodeAdapter] CodingMemoryRail (re)registered for %s",
                    mode,
                )

    # ─── Runtime config ──────────────────────────

    async def _update_runtime_config(self, runtime_config: "JiuWenClawDeepAdapter._RuntimeConfig") -> None:
        """Code 模式 runtime config: ProjectMemoryRail 语言同步 + rail 模式切换."""
        if self._instance is None:
            raise RuntimeError("JiuwenClawCodeAdapter 未初始化，请先调用 create_instance()")

        resolved_language = self._resolve_runtime_language()
        resolved_channel = str(runtime_config.channel_id or
                               self._resolve_prompt_channel(runtime_config.session_id) or "web").strip() or "web"
        if self._runtime_prompt_rail:
            self._runtime_prompt_rail.set_language(resolved_language)
            self._runtime_prompt_rail.set_channel(resolved_channel)
            self._runtime_prompt_rail.set_trusted_dirs(runtime_config.trusted_dirs)
        self._write_runtime_state(mode=runtime_config.mode, language=resolved_language, channel=resolved_channel)

        # ProjectMemoryRail 语言同步 + trusted_dirs 注入
        if self._project_memory_rail is not None:
            self._project_memory_rail.set_language(resolved_language)
            # trusted_dirs 来自 CLI 端的 trusted_dirs / workspace-dir，
            # 包含用户项目目录（即 /init 写 JIUWENCLAW.md 的目录）
            if runtime_config.trusted_dirs:
                self._project_memory_rail.set_additional_directories(
                    runtime_config.trusted_dirs,
                )

        # code 模式始终走 _update_rails_for_mode 的 code 逻辑
        await self._update_rails_for_mode(runtime_config.mode)
        await self._update_tools_for_mode(runtime_config.mode, runtime_config.session_id, runtime_config.request_id)
        await self._update_session_tools(
            runtime_config.session_id,
            runtime_config.request_id,
            channel_id=runtime_config.channel_id,
        )
        self._refresh_acp_runtime_tools(
            runtime_config.session_id,
            runtime_config.request_id,
            runtime_config.channel_id,
            runtime_config.request_metadata,
        )
        self._update_prompt_for_mode(runtime_config.mode, resolved_language)

    # ─── Tools 构建 ──────────────────────────

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
                if not Runner.resource_mgr.get_tool(result.card.id):
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
            from jiuwenclaw.agents.harness.common.tools.user_todo_tool import (
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
        """构建 SkillToolkit 工具（不注册到 Runner，由 _get_tool_cards 统一注册）."""
        try:
            skill_toolkit = SkillToolkit(manager=self._skill_manager)
            logger.info(
                "[JiuwenClawCodeAdapter] SkillToolkit built: tools=%s",
                [t.card.name for t in skill_toolkit.get_tools()],
            )
            return skill_toolkit.get_tools()
        except Exception as exc:
            logger.warning("[JiuwenClawCodeAdapter] skill_toolkit build failed: %s", exc)
            return None
