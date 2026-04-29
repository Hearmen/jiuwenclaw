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


@pytest.mark.asyncio
async def test_worker_returns_summary_and_candidates():
    worker = SecurityReviewWorker(candidate_builder=SecurityCandidateBuilder())
    request = ReviewRequest(
        request_type="timely_tool_failure_review",
        session_id="s1",
        priority=Severity.HIGH,
        dedupe_key=("s1", "read_file", "cross"),
        signals=[_signal("repeated_tool_failure", tool_name="read_file")],
    )

    result = await worker.review(request)

    assert result.session_id == "s1"
    assert result.summary
    assert result.candidates
