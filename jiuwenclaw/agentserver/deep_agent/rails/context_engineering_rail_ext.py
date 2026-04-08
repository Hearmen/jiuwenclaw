# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawContextEngineeringRail — Extend CE Rail with offload/context hints.

Subclasses the SDK ContextEngineeringRail to append offload/context-compression
instructions into the 'context' section, replacing the deleted _context_prompt().
"""
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.context_engineering_rail import ContextEngineeringRail


class JiuClawContextEngineeringRail(ContextEngineeringRail):
    """扩展 CE Rail，在 context section 中追加 offload/上下文压缩说明。"""

    OFFLOAD_HINT_CN = (
        "\n\n## 隐藏消息\n\n"
        "你的上下文在过长时会被自动压缩，"
        "并标记为[OFFLOAD: handle=<id>, type=<type>]。\n\n"
        "如果你认为需要读取隐藏的内容，"
        "可随时调用reload_original_context_messages工具。\n\n"
        "请勿猜测或编造缺失的内容\n\n"
        '存储类型："in_memory"（会话缓存）'
    )

    OFFLOAD_HINT_EN = (
        "\n\n## Context Reloading\n\n"
        "Your context will be automatically compressed when it becomes too long "
        "and marked with [OFFLOAD: handle=<id>, type=<type>].\n\n"
        'Call reload_original_context_messages(offload_handle="<id>", '
        'offload_type="<type>"), using the exact values from the marker.\n\n'
        "Do not guess or fabricate missing content.\n\n"
        'Storage types: "in_memory" (session cache)'
    )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """先执行父类注入 workspace + context，再追加 offload 说明。"""
        await super().before_model_call(ctx)

        if not self.system_prompt_builder:
            return

        sections = getattr(self.system_prompt_builder, "_sections", {})
        existing = sections.get("context")
        if not existing:
            return

        lang = self.system_prompt_builder.language or "cn"
        hint = self.OFFLOAD_HINT_CN if lang == "cn" else self.OFFLOAD_HINT_EN
        original = existing.render(lang) or ""
        merged = original + hint

        self.system_prompt_builder.add_section(
            PromptSection(
                name="context",
                content={lang: merged},
                priority=existing.priority,
            )
        )
