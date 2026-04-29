# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic security signal classification."""
from __future__ import annotations

import re

from jiuwenclaw.agentserver.deep_agent.security_review.schema import (
    FailureClass,
    SecurityEvent,
    SecuritySignal,
    Severity,
)

_DANGEROUS_COMMAND = re.compile(
    r"(curl|wget)\b[^|;\n]*\|\s*(sh|bash)|rm\s+-[rf]{2}\s+/(?:\*|\s|$)|chmod\s+777",
    re.IGNORECASE,
)
_SECRET_PATH = re.compile(
    r"(\.env\b|credentials?|token|secret|\.ssh|id_rsa|id_ed25519|private[_-]?key)",
    re.IGNORECASE,
)
_WORKSPACE_EXTERNAL = re.compile(
    r"(/Users/|/home/|/etc/|/var/|/root/|[A-Za-z]:\\\\)",
    re.IGNORECASE,
)
_NETWORK = re.compile(r"\b(curl|wget|nc|nmap|ssh|scp|ftp)\b", re.IGNORECASE)


class SecuritySignalClassifier:
    """Classify compact events without IO or LLM calls."""

    def classify(self, event: SecurityEvent) -> list[SecuritySignal]:
        text = f"{event.arguments_digest}\n{event.result_digest}"
        signals: list[SecuritySignal] = []

        if event.event_type == "tool_call":
            if _DANGEROUS_COMMAND.search(text):
                signals.append(self._signal(event, "dangerous_command", Severity.HIGH, text))
            if _SECRET_PATH.search(text):
                signals.append(self._signal(event, "secret_or_token_exposure", Severity.HIGH, text))
            elif _WORKSPACE_EXTERNAL.search(text):
                signals.append(self._signal(event, "cross_workspace_file_access", Severity.MEDIUM, text))
            if _NETWORK.search(text) and "|" in text:
                signals.append(self._signal(event, "unsafe_network_access", Severity.HIGH, text))

        if event.event_type == "tool_result":
            failure_class = self.classify_failure(text)
            if failure_class != FailureClass.UNKNOWN_FAILURE:
                signal_type = "permission_boundary_hit"
                severity = Severity.HIGH if failure_class in {
                    FailureClass.BLOCKED_BY_POLICY,
                    FailureClass.SECRET_ACCESS_DENIED,
                    FailureClass.CROSS_WORKSPACE_DENIED,
                } else Severity.MEDIUM
                signals.append(
                    self._signal(event, signal_type, severity, text, failure_class=failure_class)
                )

        return signals

    def classify_failure(self, text: str) -> FailureClass:
        lowered = (text or "").lower()
        if "blocked" in lowered and "policy" in lowered:
            return FailureClass.BLOCKED_BY_POLICY
        if "sandbox" in lowered and ("denied" in lowered or "forbid" in lowered):
            return FailureClass.SANDBOX_DENIED
        if "network" in lowered and ("denied" in lowered or "not allowed" in lowered):
            return FailureClass.NETWORK_DENIED
        if _SECRET_PATH.search(lowered) and (
            "access denied" in lowered or "not allowed" in lowered
        ):
            return FailureClass.SECRET_ACCESS_DENIED
        if _WORKSPACE_EXTERNAL.search(text or "") and (
            "outside" in lowered or "not allowed" in lowered
        ):
            return FailureClass.CROSS_WORKSPACE_DENIED
        if "permission denied" in lowered or "access denied" in lowered:
            return FailureClass.PERMISSION_DENIED
        return FailureClass.UNKNOWN_FAILURE

    @staticmethod
    def _signal(
        event: SecurityEvent,
        signal_type: str,
        severity: Severity,
        evidence: str,
        *,
        failure_class: FailureClass | None = None,
    ) -> SecuritySignal:
        return SecuritySignal(
            signal_type=signal_type,
            severity=severity,
            session_id=event.session_id,
            iteration=event.iteration,
            tool_name=event.tool_name,
            failure_class=failure_class,
            evidence=(evidence or "")[:500],
        )
