# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Async security review worker boundary."""
from __future__ import annotations

import json
from typing import Any

from jiuwenclaw.agentserver.deep_agent.security_review.candidate_builder import (
    SECURITY_ADDENDUM,
    SECURITY_CANDIDATE_SYSTEM_PROMPT,
    SecurityCandidateBuilder,
)
from jiuwenclaw.agentserver.deep_agent.security_review.schema import ReviewRequest, ReviewResult


class SecurityReviewWorker:
    """Run heavier security review outside hot callbacks."""

    def __init__(
        self,
        candidate_builder: SecurityCandidateBuilder | None = None,
        llm: Any | None = None,
    ) -> None:
        self._candidate_builder = candidate_builder or SecurityCandidateBuilder()
        self._llm = llm

    def update_llm(self, llm: Any | None) -> None:
        self._llm = llm

    async def review(self, request: ReviewRequest) -> ReviewResult:
        fallback_advice = self._build_runtime_advice(request)
        if self._llm is None:
            return ReviewResult(
                session_id=request.session_id,
                summary=self._summary(request),
                runtime_advice=fallback_advice,
                candidates=[],
            )

        raw_result = await self._invoke_llm(request)
        parsed = self._candidate_builder.validate_llm_result(raw_result)
        return ReviewResult(
            session_id=request.session_id,
            summary=parsed["summary"] or self._summary(request),
            runtime_advice=parsed["runtime_advice"] or fallback_advice,
            candidates=parsed["candidates"],
        )

    async def _invoke_llm(self, request: ReviewRequest) -> dict[str, Any]:
        payload = self._candidate_builder.build_llm_input(request)
        user_prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        response = await self._llm.invoke(
            messages=[
                {
                    "role": "system",
                    "content": SECURITY_CANDIDATE_SYSTEM_PROMPT + "\n\n" + SECURITY_ADDENDUM,
                },
                {"role": "user", "content": user_prompt},
            ]
        )
        content = getattr(response, "content", response)
        try:
            parsed = json.loads(str(content))
        except json.JSONDecodeError:
            return {"summary": "", "runtime_advice": "", "candidate_decisions": []}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _summary(request: ReviewRequest) -> str:
        tool_names = sorted({signal.tool_name for signal in request.signals if signal.tool_name})
        return (
            f"Security review {request.request_type} for tools: {', '.join(tool_names) or 'none'}"
        )

    @staticmethod
    def _build_runtime_advice(request: ReviewRequest) -> str:
        if not request.signals:
            return ""
        signal = request.signals[0]
        tool_name = signal.tool_name or "unknown"
        if signal.signal_type == "repeated_tool_failure":
            failure = signal.failure_class.value if signal.failure_class else "unknown_failure"
            return (
                f"安全监督提示：工具 {tool_name} 反复因 {failure} 失败。"
                "停止重复同一路径；说明安全边界并请求授权，或改用 workspace 内证据。"
            )
        return (
            f"安全监督提示：检测到安全风险 {signal.signal_type}。"
            "后续步骤必须避免重复高风险操作；如确需继续，请先说明安全目的并请求授权。"
        )
