# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""RuntimePromptRail — Inject dynamic time/runtime info per model call.

Time is injected fresh on every model call. Static runtime properties
(model, mode, language, platform, etc.) are maintained in runtime_state.yaml
and the model is instructed to read that file when the user asks about them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail
from jiuwenclaw.utils import get_config_dir

_CN_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]


class RuntimePromptRail(DeepAgentRail):
    """在 before_model_call 中注入时间及运行时状态文件路径。"""

    priority = 5  # 高优先级，确保早于其他 rail 执行

    def __init__(
        self,
        language: str = "cn",
        channel: str = "web",
        timezone_offset: int = 8,
    ) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._language = language
        self._channel = channel
        self._tz = timezone(timedelta(hours=timezone_offset))
        self._trusted_dirs: list[str] | None = None

    def init(self, agent) -> None:
        """从 agent 获取 system_prompt_builder 引用。"""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """清理注入的 section 并释放引用。"""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("time")
            self.system_prompt_builder.remove_section("runtime")
            self.system_prompt_builder.remove_section("browser_tool_policy")
            self.system_prompt_builder.remove_section("trusted_dirs_policy")
        self.system_prompt_builder = None

    def set_language(self, language: str) -> None:
        """per-request 更新语言。"""
        self._language = language

    def set_channel(self, channel: str) -> None:
        """per-request 更新频道。"""
        self._channel = channel

    def set_trusted_dirs(self, trusted_dirs: list[str] | None) -> None:
        """per-request 更新可信目录。"""
        self._trusted_dirs = trusted_dirs

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.system_prompt_builder:
            return

        now = datetime.now(tz=self._tz)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        current_year = now.strftime("%Y")
        weekday_cn = _CN_WEEKDAYS[now.weekday()]

        if self._language == "cn":
            time_content = (
                f"# 当前日期与时间\n\n"
                f"- 当前时间：{now_str}（{weekday_cn}）\n"
                f"- 当前年份：{current_year}\n"
                "- 当用户询问“最新、当前、今年、本年、实时、近期”等信息并需要搜索时，"
                "搜索 query 必须优先使用当前年份或日期"
            )
        else:
            time_content = (
                f"# Current Date & Time\n\n"
                f"- Current time: {now_str} ({now.strftime('%A')})\n"
                f"- Current year: {current_year}\n"
                "- When the user asks for latest/current/this-year/recent information and search is needed, "
                "search queries must prefer the current year or date."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="time",
            content={"cn": time_content, "en": time_content},
            priority=92,
        ))

        runtime_state_path = str(get_config_dir() / "runtime_state.yaml")
        if self._language == "cn":
            runtime_content = (
                "# 运行时\n\n"
                f"- 运行时状态文件：{runtime_state_path}\n"
                "- 每次用户询问当前模型、模式、语言等运行时属性时，"
                "必须重新调用 read_file 工具读取该文件获取最新值，"
                "只用一句话回答用户问的那个字段，不要列出其他内容"
            )
        else:
            runtime_content = (
                "# Runtime\n\n"
                f"- Runtime state file: {runtime_state_path}\n"
                "- Every time the user asks about the current model, mode, language, "
                "or other runtime properties, you MUST re-invoke the read_file tool to get the latest value. "
                "Only answer the field the user asked about in one sentence, do not list all file contents."
            )

        self.system_prompt_builder.add_section(PromptSection(
            name="runtime",
            content={"cn": runtime_content, "en": runtime_content},
            priority=95,
        ))

        self.system_prompt_builder.remove_section("browser_tool_policy")
        if self._channel == "web":
            browser_tool_policy = (
                "# Browser Tool Policy\n\n"
                "- For browser tasks such as opening pages, navigation, clicking, typing, login, screenshots, "
                "page inspection, or extracting data from a live website, use `task_tool` with "
                '`subagent_type` set to `"browser_agent"` and put the full browser objective in '
                "`task_description`.\n"
                "- Do not use bash, execute_code, subprocess, shell commands, or direct Chrome/Edge launches "
                "for browser automation.\n"
                "- If `task_tool` or `browser_agent` is unavailable, say that the browser subagent is unavailable "
                "before trying to start a browser through commands."
            )
            self.system_prompt_builder.add_section(PromptSection(
                name="browser_tool_policy",
                content={"cn": browser_tool_policy, "en": browser_tool_policy},
                priority=98,
            ))

        if self._channel == "tui":
            # Trusted directories policy for TUI mode
            if self._trusted_dirs and len(self._trusted_dirs) > 0:
                workspace_dir = "~/.jiuwenclaw/agent/jiuwenclaw_workspace"
                dirs_display = ", ".join(self._trusted_dirs)
                if self._language == "cn":
                    trusted_dirs_content = (
                        "# 可信目录策略\n\n"
                        f"- 默认工作空间：{workspace_dir}\n"
                        f"- 用户可信目录：{dirs_display}\n"
                        "- 文件操作（读取、编辑、执行）必须限制在上述目录范围内\n"
                        "- 若用户请求的操作涉及超出可信目录范围的路径，必须先向用户确认是否允许此次操作\n"
                        "- 确认时需明确告知：操作的完整路径、操作类型（读取/编辑/执行）、潜在风险\n"
                    )
                else:
                    trusted_dirs_content = (
                        "# Trusted Directories Policy\n\n"
                        f"- Default workspace: {workspace_dir}\n"
                        f"- User trusted directories: {dirs_display}\n"
                        "- File operations (read, edit, execute) must be limited to the above directories\n"
                        "- If the user requests an operation involving paths outside trusted directories, "
                        "you must first ask the user to confirm whether to allow this operation\n"
                        "- When confirming, clearly state: the full path, operation type (read/edit/execute), "
                        "potential risks\n"
                    )
                self.system_prompt_builder.add_section(PromptSection(
                    name="trusted_dirs_policy",
                    content={"cn": trusted_dirs_content, "en": trusted_dirs_content},
                    priority=90,
                ))
            else:
                self.system_prompt_builder.remove_section("trusted_dirs_policy")
