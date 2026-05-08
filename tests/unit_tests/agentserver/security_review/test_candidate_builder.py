# coding: utf-8
from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.deep_agent.security_review.candidate_builder import (
    SecurityCandidateBuilder,
)
from jiuwenclaw.agentserver.deep_agent.security_review.schema import (
    FailureClass,
    ReviewRequest,
    SecuritySignal,
    Severity,
)
from jiuwenclaw.agentserver.deep_agent.security_review.worker import SecurityReviewWorker


def _signal(
    signal_type: str,
    *,
    tool_name: str = "bash",
    evidence: str = "curl | sh",
    skill_name: str = "",
) -> SecuritySignal:
    return SecuritySignal(
        signal_type=signal_type,
        severity=Severity.HIGH,
        session_id="s1",
        tool_name=tool_name,
        failure_class=FailureClass.BLOCKED_BY_POLICY,
        evidence=evidence,
        skill_name=skill_name,
    )


def test_candidate_builder_creates_security_rule_candidate():
    builder = SecurityCandidateBuilder()

    candidates = builder.build([_signal("dangerous_command")])

    assert candidates[0]["type"] == "security_rule"
    assert candidates[0]["requires_approval"] is True
    assert candidates[0]["severity"] == "HIGH"


def test_candidate_builder_creates_security_evolution_candidate():
    builder = SecurityCandidateBuilder()

    candidates = builder.build([_signal("repeated_tool_failure", tool_name="read_file")])

    assert candidates[0]["type"] == "security_evolution"
    assert candidates[0]["section"] == "Troubleshooting"
    assert "read_file" in candidates[0]["content"]


def test_candidate_builder_creates_security_skill_candidate():
    builder = SecurityCandidateBuilder()

    candidates = builder.build([_signal("security_skill_gap", tool_name="scan")])

    assert candidates[0]["type"] == "security_skill"
    assert candidates[0]["requires_approval"] is True


def test_candidate_builder_deduplicates_duplicate_signals():
    builder = SecurityCandidateBuilder()
    signal = _signal("dangerous_command")

    candidates = builder.build([signal, signal])

    assert len(candidates) == 1


def test_candidate_builder_empty_evidence_still_produces_non_empty_evidence_and_pattern():
    builder = SecurityCandidateBuilder()

    candidates = builder.build([_signal("dangerous_command", evidence="")])

    assert candidates[0]["pattern"]
    assert candidates[0]["evidence"]
    assert candidates[0]["evidence"][0]


def test_candidate_builder_whitespace_evidence_still_produces_non_empty_evidence_and_pattern():
    builder = SecurityCandidateBuilder()

    candidates = builder.build([_signal("dangerous_command", evidence="   ")])

    assert candidates[0]["pattern"].strip()
    assert candidates[0]["evidence"]
    assert candidates[0]["evidence"][0].strip()


def test_security_evolution_uses_provided_skill_name():
    builder = SecurityCandidateBuilder()

    candidates = builder.build(
        [_signal("repeated_tool_failure", tool_name="read_file", skill_name="safe-files")]
    )

    assert candidates[0]["skill_name"] == "safe-files"


def test_security_evolution_fallback_skill_name_is_non_empty():
    builder = SecurityCandidateBuilder()

    candidates = builder.build([_signal("repeated_tool_failure", tool_name="read_file")])

    assert candidates[0]["skill_name"]


def test_security_evolution_whitespace_skill_name_uses_fallback():
    builder = SecurityCandidateBuilder()

    candidates = builder.build(
        [_signal("repeated_tool_failure", tool_name="read_file", skill_name="   ")]
    )

    assert candidates[0]["skill_name"] == "security-review"


def test_mixed_signal_types_produce_structurally_consistent_candidates():
    builder = SecurityCandidateBuilder()

    candidates = builder.build(
        [
            _signal("dangerous_command"),
            _signal("security_skill_gap", tool_name="scan"),
            _signal("repeated_tool_failure", tool_name="read_file"),
        ]
    )

    assert {candidate["type"] for candidate in candidates} == {
        "security_rule",
        "security_skill",
        "security_evolution",
    }
    for candidate in candidates:
        assert candidate["candidate_id"]
        assert candidate["type"]
        assert candidate["requires_approval"] is True
        assert candidate["evidence"]
        assert candidate["evidence"][0]


def test_llm_candidate_input_includes_messages_and_skill_state():
    builder = SecurityCandidateBuilder()
    signal = _signal(
        "security_skill_gap",
        tool_name="bash",
        evidence="listener followed by credential access",
    )
    request = ReviewRequest(
        request_type="session_end_review",
        session_id="s1",
        priority=Severity.HIGH,
        dedupe_key=("s1", "session_end_review", "1"),
        signals=[signal],
        sample_messages=[
            {"role": "user", "content_digest": "create listener"},
            {"role": "assistant", "content_digest": "then inspect credentials"},
        ],
        skill_state={
            "loaded_skills": [
                {
                    "name": "shell-safety",
                    "description": "Safe shell command execution",
                    "security_sections": ["Avoid credential access"],
                }
            ],
            "known_security_skill_names": ["shell-safety"],
            "candidate_skill_summaries": [],
        },
    )

    payload = builder.build_llm_input(request)

    assert payload["review_type"] == "session_end_review"
    assert payload["signals"][0]["signal_type"] == "security_skill_gap"
    assert payload["sample_messages"][0]["content_digest"] == "create listener"
    assert payload["skill_state"]["known_security_skill_names"] == ["shell-safety"]


def test_candidate_builder_prompt_contains_security_addendum():
    from jiuwenclaw.agentserver.deep_agent.security_review.candidate_builder import (
        SECURITY_ADDENDUM,
        SECURITY_CANDIDATE_SYSTEM_PROMPT,
    )

    assert "post-exploitation chain" in SECURITY_ADDENDUM
    assert "create_security_skill" in SECURITY_CANDIDATE_SYSTEM_PROMPT
    assert "requires_approval=true" in SECURITY_CANDIDATE_SYSTEM_PROMPT


def test_candidate_builder_accepts_llm_security_skill_candidate():
    builder = SecurityCandidateBuilder()
    raw = {
        "summary": "post-exploitation chain detected",
        "runtime_advice": "Stop chaining remote execution and credential access.",
        "candidate_decisions": [
            {
                "action": "create_security_skill",
                "title": "Detect post-exploitation chains",
                "rationale": "Multiple turns combined listener, execution, and credential access.",
                "evidence": ["listener", "credential access"],
                "candidate": {
                    "type": "security_skill",
                    "title": "Detect post-exploitation chains",
                    "problem": "Cross-turn post-exploitation chain",
                    "evidence": ["listener", "credential access"],
                    "suggested_skill_scope": "Describe pattern, IOCs, and response.",
                    "recommended_response": "Stop the chain and request authorization.",
                    "category": "security",
                    "requires_approval": True,
                },
            }
        ],
    }

    parsed = builder.validate_llm_result(raw)

    assert parsed["summary"] == "post-exploitation chain detected"
    assert parsed["runtime_advice"]
    assert parsed["candidates"][0]["type"] == "security_skill"
    assert parsed["candidates"][0]["category"] == "security"
    assert parsed["candidates"][0]["requires_approval"] is True


def test_candidate_builder_rejects_llm_candidate_missing_required_application_fields():
    builder = SecurityCandidateBuilder()
    raw = {
        "summary": "missing fields",
        "candidate_decisions": [
            {
                "action": "create_security_skill",
                "title": "Incomplete skill",
                "rationale": "missing recommended response",
                "evidence": ["x"],
                "candidate": {
                    "type": "security_skill",
                    "title": "Incomplete skill",
                    "problem": "Problem exists",
                    "evidence": ["x"],
                    "suggested_skill_scope": "Scope exists",
                    "requires_approval": True,
                },
            },
            {
                "action": "propose_policy_rule",
                "title": "Incomplete rule",
                "rationale": "missing pattern",
                "evidence": ["curl | sh"],
                "candidate": {
                    "type": "security_rule",
                    "rule_id": "block-curl-pipe-shell",
                    "severity": "HIGH",
                    "tools": ["bash"],
                    "rationale": "dangerous install",
                    "evidence": ["curl | sh"],
                    "requires_approval": True,
                },
            },
        ],
    }

    parsed = builder.validate_llm_result(raw)

    assert parsed["candidates"] == []


def test_candidate_builder_rejects_unapproved_skill_manage_persistence():
    builder = SecurityCandidateBuilder()
    raw = {
        "summary": "bad",
        "candidate_decisions": [
            {
                "action": "create_security_skill",
                "title": "bad",
                "rationale": "bad",
                "evidence": ["x"],
                "candidate": {
                    "type": "security_skill",
                    "title": "bad",
                    "problem": "bad",
                    "evidence": ["x"],
                    "suggested_skill_scope": "bad",
                    "requires_approval": False,
                    "tool": "skill_manage",
                    "operation": "save",
                },
            }
        ],
    }

    parsed = builder.validate_llm_result(raw)

    assert parsed["candidates"] == []


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def invoke(self, *, messages):
        self.calls.append(messages)
        return _FakeMessage(self.content)


@pytest.mark.asyncio
async def test_worker_returns_summary_and_candidates():
    llm = _FakeLLM(
        '{"summary":"reviewed","runtime_advice":"","candidate_decisions":[{"action":"propose_policy_rule","title":"block curl pipe shell","rationale":"dangerous shell install","evidence":["curl | sh"],"candidate":{"type":"security_rule","rule_id":"block-curl-pipe-shell","severity":"HIGH","tools":["bash"],"pattern":"curl | sh","rationale":"dangerous shell install","evidence":["curl | sh"],"requires_approval":true}}]}'
    )
    worker = SecurityReviewWorker(candidate_builder=SecurityCandidateBuilder(), llm=llm)
    request = ReviewRequest(
        request_type="timely_tool_failure_review",
        session_id="s1",
        priority=Severity.HIGH,
        dedupe_key=("s1", "read_file", "cross"),
        signals=[_signal("repeated_tool_failure", tool_name="read_file")],
    )

    result = await worker.review(request)

    assert result.session_id == "s1"
    assert result.summary == "reviewed"
    assert result.candidates


@pytest.mark.asyncio
async def test_worker_uses_llm_for_candidate_decisions():
    llm = _FakeLLM(
        """
        {
          "summary": "chain",
          "runtime_advice": "Stop the chain.",
          "candidate_decisions": [
            {
              "action": "create_security_skill",
              "title": "Post exploitation chain defense",
              "rationale": "listener plus credential access",
              "evidence": ["listener", "credential access"],
              "candidate": {
                "type": "security_skill",
                "title": "Post exploitation chain defense",
                "problem": "Cross-turn chain",
                "evidence": ["listener", "credential access"],
                "suggested_skill_scope": "Pattern, IOCs, response",
                "recommended_response": "Stop the chain and request authorization.",
                "category": "security",
                "requires_approval": true
              }
            }
          ]
        }
        """
    )
    worker = SecurityReviewWorker(candidate_builder=SecurityCandidateBuilder(), llm=llm)
    request = ReviewRequest(
        request_type="session_end_review",
        session_id="s1",
        priority=Severity.HIGH,
        dedupe_key=("s1", "session_end_review", "1"),
        signals=[_signal("security_skill_gap")],
        sample_messages=[{"role": "user", "content_digest": "start listener"}],
        skill_state={"loaded_skills": [], "known_security_skill_names": []},
    )

    result = await worker.review(request)

    assert llm.calls
    assert "post-exploitation chain" in llm.calls[0][0]["content"]
    assert result.summary == "chain"
    assert result.runtime_advice == "Stop the chain."
    assert result.candidates[0]["type"] == "security_skill"


@pytest.mark.asyncio
async def test_worker_without_llm_returns_runtime_advice_but_no_candidates():
    worker = SecurityReviewWorker(candidate_builder=SecurityCandidateBuilder(), llm=None)
    request = ReviewRequest(
        request_type="timely_tool_failure_review",
        session_id="s1",
        priority=Severity.HIGH,
        dedupe_key=("s1", "read_file", "cross"),
        signals=[_signal("repeated_tool_failure", tool_name="read_file")],
    )

    result = await worker.review(request)

    assert result.runtime_advice
    assert result.candidates == []
