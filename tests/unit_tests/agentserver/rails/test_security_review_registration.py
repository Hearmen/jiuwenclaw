# coding: utf-8
from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest

warnings.filterwarnings(
    "ignore",
    message="Pandas requires version .*",
    category=UserWarning,
)

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.deep_agent.rails import SecurityReviewAndSkillRail
from jiuwenclaw.schema.agent import AgentRequest


def test_security_review_rail_disabled_by_default():
    adapter = JiuWenClawDeepAdapter()

    assert adapter._build_security_review_rail({}) is None


def test_security_review_rail_uses_react_config_when_enabled():
    adapter = JiuWenClawDeepAdapter()

    rail = adapter._build_security_review_rail(
        {
            "security_review": {
                "enabled": True,
                "repeated_tool_failure_threshold": 3,
                "runtime_advice": False,
            }
        }
    )

    assert isinstance(rail, SecurityReviewAndSkillRail)
    assert rail.config.repeated_tool_failure_threshold == 3
    assert rail.config.runtime_advice is False


def test_build_agent_rails_registers_security_review_when_enabled():
    adapter = JiuWenClawDeepAdapter()
    security_review_rail = object()

    with (
        patch.object(adapter, "_filesystem_rail_enabled_for_profile", return_value=False),
        patch.object(adapter, "_skill_include_tools_for_profile", return_value=False),
        patch.object(adapter, "_build_runtime_prompt_rail", return_value=None),
        patch.object(adapter, "_build_response_prompt_rail", return_value=None),
        patch.object(adapter, "_build_skill_rail", return_value=None),
        patch.object(adapter, "_build_stream_event_rail", return_value=None),
        patch.object(adapter, "_build_task_planning_rail", return_value=None),
        patch.object(adapter, "_build_security_rail", return_value=None),
        patch.object(
            adapter, "_build_security_review_rail", return_value=security_review_rail
        ) as build_security_review,
        patch.object(adapter, "_build_heartbeat_rail", return_value=None),
        patch.object(adapter, "_build_avatar_rail", return_value=None),
        patch.object(adapter, "_build_subagent_rail", return_value=None),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_permission_rail",
            return_value=None,
        ),
    ):
        rails = adapter._build_agent_rails(
            {"security_review": {"enabled": True}},
            {"models": {"default": {"model_client_config": {"model_name": "test-model"}}}},
        )

    assert rails == [security_review_rail]
    assert adapter._security_review_rail is security_review_rail
    build_security_review.assert_called_once_with(config={"security_review": {"enabled": True}})


def test_security_review_candidate_chunks_are_approval_events():
    adapter = JiuWenClawDeepAdapter()
    candidate = {"type": "security_rule", "requires_approval": True}

    chunks = adapter._security_review_candidates_to_chunks([candidate])

    assert chunks[0]["event_type"] == "chat.ask_user_question"
    assert chunks[0]["request_id"].startswith("security_review_")
    assert "安全演进审批" in chunks[0]["questions"][0]["header"]


@pytest.mark.asyncio
async def test_security_review_candidate_answer_is_resolved_and_recorded():
    adapter = JiuWenClawDeepAdapter()
    candidate = {"type": "security_rule", "requires_approval": True}
    chunks = adapter._security_review_candidates_to_chunks([candidate])

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-1",
            params={
                "request_id": chunks[0]["request_id"],
                "answers": [{"selected_options": ["接收"]}],
            },
        )
    )

    assert response.payload["resolved"] is True
    assert adapter._security_review_approved_candidates == [candidate]
    assert adapter._security_review_pending_candidates == {}


def test_get_current_agent_rails_rebuilds_security_review_rail_on_reload():
    adapter = JiuWenClawDeepAdapter()

    with (
        patch.object(adapter, "_build_skill_rail", return_value=None),
        patch.object(adapter, "_update_permission_rail", return_value=None),
    ):
        rails = adapter._get_current_agent_rails({"security_review": {"enabled": True}})

    assert len(rails) == 1
    assert isinstance(rails[0], SecurityReviewAndSkillRail)
    assert adapter._security_review_rail is rails[0]
