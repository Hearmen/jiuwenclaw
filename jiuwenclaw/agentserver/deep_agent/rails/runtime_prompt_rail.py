# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""RuntimePromptRail — Inject dynamic time/channel info per model call.

Replaces the _refresh_runtime_identity_prompt() hack in interface_deep.py.
Dynamic content (time, channel) is decoupled from the static identity prompt
and refreshed on every model call via before_model_call().
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail


class RuntimePromptRail(DeepAgentRail):
    """在 before_model_call 中注入运行时动态 section（时间、频道）。

    替代原来的 _refresh_runtime_identity_prompt() hack，
    将动态内容从静态身份 prompt 中解耦，实现每次 model call 刷新。
    """

    priority = 5  # 高优先级，确保早于其他 rail 执行

    def __init__(self, language: str = "cn", channel: str = "web", timezone_offset: int = 8) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._language = language
        self._channel = channel
        self._tz = timezone(timedelta(hours=timezone_offset))

    def init(self, agent) -> None:
        """从 agent 获取 system_prompt_builder 引用。"""
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        """清理注入的 time section 并释放引用。"""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("time")
        self.system_prompt_builder = None

    def set_language(self, language: str) -> None:
        """per-request 更新语言（由 _register_runtime_tools 调用）。"""
        self._language = language

    def set_channel(self, channel: str) -> None:
        """per-request 更新频道（由 _register_runtime_tools 调用）。"""
        self._channel = channel

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """每次 model call 注入最新的时间和频道信息。"""
        now_str = datetime.now(tz=self._tz).strftime("%Y-%m-%d %H:%M:%S")

        if self._language == "cn":
            content = f"# 当前时间\n\n{now_str}\n\n频道: {self._channel}"
        else:
            content = f"# Current Time\n\n{now_str}\n\nChannel: {self._channel}"

        section = PromptSection(
            name="time",
            content={"cn": content, "en": content},
            priority=15,
        )
        if self.system_prompt_builder:
            self.system_prompt_builder.add_section(section)
