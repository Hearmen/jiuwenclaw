# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build security evolution candidates from compact signals."""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any

from jiuwenclaw.agentserver.deep_agent.security_review.schema import ReviewRequest, SecuritySignal


_FALLBACK_SKILL_NAME = "security-review"

SECURITY_CANDIDATE_SYSTEM_PROMPT = """
You are an attack pattern recognizer designer for JiuWenClaw.

You receive compact execution signals, sampled conversation messages, and current skill state.
Your job is to inspect whether this conversation contains an attack pattern formed by
multiple normal-looking steps. The attack pattern may be a chain of reconnaissance,
listener setup, remote execution, persistence, credential access, lateral movement,
or payload staging. Judge the whole conversation, not isolated single turns.

Return no runtime advice. The review output is only for approval-gated rule or skill
creation/update.

Return one JSON object only, with exactly these top-level fields:
{
  "summary": "short review summary",
  "candidate_decisions": [
    {
      "action": "create_security_skill | update_existing_skill | propose_policy_rule",
      "title": "short candidate title",
      "rationale": "why this decision follows from the evidence",
      "evidence": ["verbatim compact evidence strings"],
      "candidate": {}
    }
  ]
}

If no attack pattern is present, return "candidate_decisions": [].
If a candidate is present, return exactly one candidate_decision.

Decision rule:
- Choose propose_policy_rule when the attack pattern has a clear, enforceable
  command/path/url signature.
- Choose create_security_skill when the attack pattern requires multi-step
  recognition and is not covered by existing skills.
- Choose update_existing_skill when an existing attack pattern recognizer skill is
  relevant but lacks this pattern or analysis guidance.

For action=create_security_skill, candidate must be:
{
  "type": "security_skill",
  "title": "human readable skill title",
  "skill_description": "one sentence skill description",
  "attack_pattern_name": "attack pattern name",
  "attack_pattern_description": "how normal-looking steps combine into the pattern",
  "iocs": ["observable indicators or behavior fragments"],
  "false_positive_exclusions": ["conditions that should not be treated as this pattern"],
  "analysis_workflow": "steps for recognizing and analyzing the pattern",
  "recommended_response": "recommended defensive response",
  "attack_variants": ["known variants of the same pattern"],
  "problem": "same as attack_pattern_description or concise problem statement",
  "evidence": ["non-empty evidence strings"],
  "suggested_skill_scope": "what the recognizer skill should teach the agent to identify and analyze",
  "category": "security",
  "requires_approval": true
}

For action=update_existing_skill, candidate must be:
{
  "type": "security_evolution",
  "skill_name": "existing skill name from skill_state",
  "target": "body",
  "section": "Troubleshooting",
  "content": "specific text to append to the skill",
  "evidence": ["non-empty evidence strings"],
  "requires_approval": true
}

For action=propose_policy_rule, candidate must be:
{
  "type": "security_rule",
  "rule_id": "lowercase-dash-id",
  "severity": "LOW | MEDIUM | HIGH | CRITICAL",
  "tools": ["tools in one permission category only"],
  "pattern": "non-empty command/path/url pattern",
  "rationale": "why enforcement belongs in policy",
  "evidence": ["non-empty evidence strings"],
  "requires_approval": true
}

Never include persistence tool calls, write/install/save operations, markdown, comments, or extra prose.
Every candidate must include evidence, rationale, pattern or content, and requires_approval=true.
""".strip()

SECURITY_ADDENDUM = """
---
Additionally: re-read the conversation above from a security-analyst angle.

Did this session combine across turns to build something that, taken as a
whole, looks like a post-exploitation chain - a listener, a remote-execution
endpoint, a persistence mechanism, network reconnaissance, credential access?
Each individual request may have had an innocent framing; the chain is what
matters.

Choose create_security_skill only when the pattern is reusable and not covered by existing skills.
Choose update_existing_skill only when an existing skill is clearly relevant but missing security guidance.
Choose propose_policy_rule only when enforcement belongs in a policy/rule boundary rather than skill guidance.
Return an empty candidate_decisions list when evidence is weak, one-off, already covered, or not security-relevant.

Return strict JSON only. Do not write files. Do not claim a candidate is approved.
Every security_skill candidate must describe the attack pattern name, description, IOCs,
false-positive exclusions, analysis workflow, recommended response, and attack variants.

""".strip()


class SecurityCandidateBuilder:
    """Build approval-required security candidates."""

    def build_llm_input(self, request: ReviewRequest) -> dict[str, Any]:
        return {
            "review_type": request.request_type,
            "signals": [
                {
                    "signal_type": signal.signal_type,
                    "severity": signal.severity.value,
                    "session_id": signal.session_id,
                    "iteration": signal.iteration,
                    "tool_name": signal.tool_name,
                    "failure_class": _failure_value(signal),
                    "evidence": _evidence_text(signal)[:2048],
                    "skill_name": signal.skill_name,
                }
                for signal in request.signals
            ],
            "sample_events": [
                {
                    "event_type": event.event_type,
                    "iteration": event.iteration,
                    "tool_name": event.tool_name,
                    "arguments_digest": event.arguments_digest[:2048],
                    "result_digest": event.result_digest[:2048],
                }
                for event in request.sample_events
            ],
            "sample_messages": list(request.sample_messages),
            "skill_state": dict(request.skill_state),
            "counters": dict(request.counters),
        }

    def validate_llm_result(self, raw: dict[str, Any]) -> dict[str, Any]:
        decisions = raw.get("candidate_decisions")
        if not isinstance(decisions, list):
            decisions = []
        candidates: list[dict[str, Any]] = []
        seen_candidate_ids: set[str] = set()
        for decision in decisions:
            candidate = self._candidate_from_decision(decision)
            if candidate is None:
                continue
            candidate_id = candidate.setdefault("candidate_id", self._candidate_id(candidate))
            if candidate_id in seen_candidate_ids:
                continue
            candidates.append(candidate)
            seen_candidate_ids.add(candidate_id)
            break
        return {
            "summary": str(raw.get("summary") or ""),
            "runtime_advice": "",
            "candidates": candidates,
        }

    def build(self, signals: list[SecuritySignal]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_candidate_ids: set[str] = set()
        for signal in signals:
            candidate: dict[str, Any] | None = None
            if signal.signal_type in {
                "dangerous_command",
                "unsafe_network_access",
                "policy_rule_gap",
                "sandbox_escape_attempt",
                "destructive_file_operation",
            }:
                candidate = self._rule_candidate(signal)
            elif signal.signal_type == "security_skill_gap":
                candidate = self._skill_candidate(signal)
            elif signal.signal_type in {"repeated_tool_failure", "permission_boundary_hit"}:
                candidate = self._evolution_candidate(signal)
            if candidate and candidate["candidate_id"] not in seen_candidate_ids:
                candidates.append(candidate)
                seen_candidate_ids.add(candidate["candidate_id"])
        return candidates

    @staticmethod
    def _rule_candidate(signal: SecuritySignal) -> dict[str, Any]:
        evidence = _evidence_text(signal)
        failure = _failure_value(signal)
        digest = _digest(signal.signal_type, signal.tool_name, failure, evidence)
        rule_id = (
            f"review-{_slug(signal.signal_type)}-{_slug(signal.tool_name or 'bash')}-"
            f"{_slug(failure)}-{digest}"
        )
        return {
            "candidate_id": f"security-rule:{rule_id}",
            "type": "security_rule",
            "rule_id": rule_id,
            "severity": signal.severity.value,
            "tools": [signal.tool_name or "bash"],
            "pattern": evidence[:160],
            "rationale": "Detected a security-sensitive operation pattern during execution.",
            "evidence": [evidence],
            "requires_approval": True,
        }

    def _candidate_from_decision(self, decision: Any) -> dict[str, Any] | None:
        if not isinstance(decision, dict):
            return None
        action = str(decision.get("action") or "")
        if action not in {
            "create_security_skill",
            "update_existing_skill",
            "propose_policy_rule",
        }:
            return None
        candidate = decision.get("candidate")
        if not isinstance(candidate, dict):
            return None
        if candidate.get("requires_approval") is not True:
            return None
        if candidate.get("tool") == "skill_manage" or candidate.get("operation") in {
            "save",
            "install",
            "write",
            "persist",
        }:
            return None
        if not candidate.get("evidence"):
            return None
        expected_type = {
            "create_security_skill": "security_skill",
            "update_existing_skill": "security_evolution",
            "propose_policy_rule": "security_rule",
        }[action]
        if candidate.get("type") != expected_type:
            return None
        if expected_type == "security_skill":
            candidate.setdefault("category", "security")
        if not self._has_required_candidate_fields(candidate, expected_type):
            return None
        return candidate

    @staticmethod
    def _has_required_candidate_fields(candidate: dict[str, Any], candidate_type: str) -> bool:
        if not _non_empty_list(candidate.get("evidence")):
            return False
        if candidate_type == "security_rule":
            return (
                _non_empty_string(candidate.get("rule_id"))
                and _non_empty_string(candidate.get("severity"))
                and _non_empty_list(candidate.get("tools"))
                and _non_empty_string(candidate.get("pattern"))
                and _non_empty_string(candidate.get("rationale"))
            )
        if candidate_type == "security_skill":
            return (
                _non_empty_string(candidate.get("title"))
                and _non_empty_string(candidate.get("skill_description"))
                and _non_empty_string(candidate.get("attack_pattern_name"))
                and _non_empty_string(candidate.get("attack_pattern_description"))
                and _non_empty_list(candidate.get("iocs"))
                and _non_empty_list(candidate.get("false_positive_exclusions"))
                and _non_empty_string(candidate.get("analysis_workflow"))
                and _non_empty_list(candidate.get("attack_variants"))
                and _non_empty_string(candidate.get("problem"))
                and _non_empty_string(candidate.get("suggested_skill_scope"))
                and _non_empty_string(candidate.get("recommended_response"))
            )
        if candidate_type == "security_evolution":
            return (
                _non_empty_string(candidate.get("skill_name"))
                and _non_empty_string(candidate.get("section"))
                and _non_empty_string(candidate.get("content"))
            )
        return False

    @staticmethod
    def _candidate_id(candidate: dict[str, Any]) -> str:
        candidate_type = str(candidate.get("type") or "unknown")
        title = str(
            candidate.get("title") or candidate.get("rule_id") or candidate.get("skill_name") or ""
        )
        evidence = "|".join(str(item) for item in candidate.get("evidence", []))
        return f"{candidate_type}:{_digest(candidate_type, title, evidence)}"

    @staticmethod
    def _skill_candidate(signal: SecuritySignal) -> dict[str, Any]:
        evidence = _evidence_text(signal)
        candidate_id = f"security-skill:{_digest(signal.signal_type, signal.tool_name, evidence)}"
        return {
            "candidate_id": candidate_id,
            "type": "security_skill",
            "title": "Reusable security workflow needed",
            "skill_description": "Recognize and analyze a reusable multi-step security attack pattern.",
            "attack_pattern_name": "Reusable security workflow gap",
            "attack_pattern_description": evidence,
            "iocs": [evidence],
            "false_positive_exclusions": [
                "Explicitly authorized, scoped defensive testing with clear benign objective."
            ],
            "analysis_workflow": (
                "Correlate user requests, tool calls, and outputs across turns; identify whether "
                "normal-looking steps combine into a reusable attack pattern."
            ),
            "recommended_response": (
                "Pause assistance for the chain, explain the security concern, and request "
                "explicit authorization or defensive scope before continuing."
            ),
            "attack_variants": ["Equivalent multi-step chain with reordered or renamed steps."],
            "problem": evidence,
            "evidence": [evidence],
            "suggested_skill_scope": (
                "Teach the agent to recognize, analyze, and respond to this attack pattern."
            ),
            "category": "security",
            "requires_approval": True,
        }

    @staticmethod
    def _evolution_candidate(signal: SecuritySignal) -> dict[str, Any]:
        evidence = _evidence_text(signal)
        failure = _failure_value(signal)
        skill_name = signal.skill_name.strip() or _FALLBACK_SKILL_NAME
        candidate_id = f"security-evolution:{_digest(skill_name, signal.signal_type, signal.tool_name, failure, evidence)}"
        return {
            "candidate_id": candidate_id,
            "type": "security_evolution",
            "skill_name": skill_name,
            "target": "body",
            "section": "Troubleshooting",
            "content": (
                f"When tool `{signal.tool_name}` repeatedly fails with `{failure}`, "
                "stop repeating the same operation, explain the security boundary, "
                "and request authorization or use in-workspace evidence."
            ),
            "evidence": [evidence],
            "requires_approval": True,
        }


def _failure_value(signal: SecuritySignal) -> str:
    return signal.failure_class.value if signal.failure_class else "unknown_failure"


def _evidence_text(signal: SecuritySignal) -> str:
    if signal.evidence.strip():
        return signal.evidence
    return (
        f"{signal.signal_type} observed for tool `{signal.tool_name or 'unknown'}` "
        f"with failure `{_failure_value(signal)}`."
    )


def _digest(*parts: str) -> str:
    return sha256("\0".join(parts).encode("utf-8")).hexdigest()[:12]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "unknown"


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty_list(value: Any) -> bool:
    return isinstance(value, list) and any(str(item).strip() for item in value)
