import os
from types import SimpleNamespace

from unittest.mock import patch

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection, SystemPromptBuilder

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_identity_prompt
from jiuwenclaw.agentserver.deep_agent.rails.response_prompt_rail import ResponsePromptRail
from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import RuntimePromptRail


def test_build_identity_prompt_only_contains_identity_section():
    prompt = build_identity_prompt(mode="agent", language="zh", channel="web")

    assert "# 你的家" in prompt
    assert "# 消息说明" not in prompt


@pytest.mark.asyncio
async def test_response_and_runtime_sections_participate_in_priority_order():
    builder = SystemPromptBuilder(language="cn")
    builder.add_section(PromptSection(name="identity", content={"cn": "identity"}, priority=10))
    builder.add_section(PromptSection(name="safety", content={"cn": "# 安全原则"}, priority=20))
    builder.add_section(PromptSection(name="tools", content={"cn": "# 可用工具"}, priority=30))
    builder.add_section(PromptSection(name="skills", content={"cn": "# 技能"}, priority=40))
    builder.add_section(PromptSection(name="memory", content={"cn": "# 持久化存储体系"}, priority=50))
    builder.add_section(PromptSection(name="workspace", content={"cn": "# 工作空间"}, priority=70))
    builder.add_section(PromptSection(name="context", content={"cn": "# 项目上下文"}, priority=80))
    builder.add_section(PromptSection(name="offload", content={"cn": "# 上下文压缩"}, priority=90))

    response_rail = ResponsePromptRail()
    response_rail.init(SimpleNamespace(system_prompt_builder=builder))

    runtime_rail = RuntimePromptRail(
        language="cn",
        channel="web",
        agent_name="main_agent",
        model_name="test-model",
    )
    runtime_rail.init(SimpleNamespace(system_prompt_builder=builder))

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await response_rail.before_model_call(ctx)
    await runtime_rail.before_model_call(ctx)

    prompt = builder.build()
    ordered_markers = [
        "identity",
        "# 安全原则",
        "# 可用工具",
        "# 技能",
        "# 持久化存储体系",
        "# 消息说明",
        "# 工作空间",
        "# 项目上下文",
        "# 上下文压缩",
        "# 当前日期与时间",
        "# 运行时",
    ]
    positions = [prompt.index(marker) for marker in ordered_markers]
    assert positions == sorted(positions)
    assert "运行时：agent=main_agent | model=test-model | channel=web | language=cn" in prompt


def test_resolve_skill_mode_accepts_all_and_auto_list():
    assert JiuWenClawDeepAdapter._resolve_skill_mode({"skill_mode": "all"}) == "all"
    assert JiuWenClawDeepAdapter._resolve_skill_mode({"skill_mode": "auto_list"}) == "auto_list"
    assert JiuWenClawDeepAdapter._resolve_skill_mode({"skill_mode": "invalid"}) == "all"


def test_build_preset_subagents_includes_code_research_and_optional_browser():
    adapter = JiuWenClawDeepAdapter()
    adapter._workspace_dir = "/tmp/jiuwenclaw-workspace"
    model = object()
    config = {"max_iterations": 9}

    with (
        patch.dict("os.environ", {"BROWSER_AGENT_MAX_ITERATIONS": ""}),
        patch.object(adapter, "_resolve_runtime_language", return_value="cn"),
        patch.object(adapter, "_browser_runtime_enabled", return_value=True),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_agent_config",
            return_value="code_spec",
        ) as mock_code,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_research_agent_config",
            return_value="research_spec",
        ) as mock_research,
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_browser_agent_config",
            return_value="browser_spec",
        ) as mock_browser,
    ):
        subagents = adapter._build_preset_subagents(model, config)

    assert subagents == ["code_spec", "research_spec", "browser_spec"]
    mock_code.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenclaw-workspace",
        language="cn",
        max_iterations=9,
    )
    mock_research.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenclaw-workspace",
        language="cn",
        max_iterations=9,
    )
    mock_browser.assert_called_once_with(
        model,
        workspace="/tmp/jiuwenclaw-workspace",
        language="cn",
        max_iterations=9,
    )
