# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Async security review worker boundary."""
from __future__ import annotations

from jiuwenclaw.agentserver.deep_agent.security_review.candidate_builder import (
    SecurityCandidateBuilder,
)
from jiuwenclaw.agentserver.deep_agent.security_review.schema import ReviewRequest, ReviewResult


class SecurityReviewWorker:
    """Run heavier security review outside hot callbacks.

    The first implementation is deterministic. An LLM-backed implementation can be
    added behind this boundary without changing rail hot-path behavior.
    """

    def __init__(self, candidate_builder: SecurityCandidateBuilder | None = None) -> None:
        self._candidate_builder = candidate_builder or SecurityCandidateBuilder()

    async def review(self, request: ReviewRequest) -> ReviewResult:
        candidates = self._candidate_builder.build(request.signals)
        tool_names = sorted({signal.tool_name for signal in request.signals if signal.tool_name})
        summary = (
            f"Security review {request.request_type} for tools: {', '.join(tool_names) or 'none'}"
        )
        return ReviewResult(
            session_id=request.session_id,
            summary=summary,
            candidates=candidates,
        )
