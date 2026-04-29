# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Build security evolution candidates from compact signals."""
from __future__ import annotations

from hashlib import sha256
import re
from typing import Any

from jiuwenclaw.agentserver.deep_agent.security_review.schema import SecuritySignal


_FALLBACK_SKILL_NAME = "security-review"


class SecurityCandidateBuilder:
    """Build approval-required security candidates."""

    def build(self, signals: list[SecuritySignal]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen_candidate_ids: set[str] = set()
        for signal in signals:
            candidate: dict[str, Any] | None = None
            if signal.signal_type in {"dangerous_command", "unsafe_network_access"}:
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

    @staticmethod
    def _skill_candidate(signal: SecuritySignal) -> dict[str, Any]:
        evidence = _evidence_text(signal)
        candidate_id = f"security-skill:{_digest(signal.signal_type, signal.tool_name, evidence)}"
        return {
            "candidate_id": candidate_id,
            "type": "security_skill",
            "title": "Reusable security workflow needed",
            "problem": evidence,
            "evidence": [evidence],
            "suggested_skill_scope": "Guide the agent through safe handling of this security workflow.",
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
