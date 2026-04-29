# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SecurityReviewAndSkillRail for in-task security supervision."""
from __future__ import annotations

from dataclasses import fields
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.security_review.classifier import (
    SecuritySignalClassifier,
)
from jiuwenclaw.agentserver.deep_agent.security_review.scheduler import (
    SecurityReviewScheduler,
)
from jiuwenclaw.agentserver.deep_agent.security_review.schema import (
    ReviewRequest,
    ReviewResult,
    SecurityEvent,
    SecurityReviewConfig,
    Severity,
)
from jiuwenclaw.agentserver.deep_agent.security_review.session_state import (
    SecuritySessionState,
)
from jiuwenclaw.agentserver.deep_agent.security_review.worker import SecurityReviewWorker


class SecurityReviewAndSkillRail(DeepAgentRail):
    """Observe security signals and inject bounded runtime advice."""

    priority = 88

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.config = self._parse_config(config or {})
        self.classifier = SecuritySignalClassifier()
        self.state = SecuritySessionState(self.config)
        self.scheduler = SecurityReviewScheduler(self.config)
        self.worker = SecurityReviewWorker()
        self.system_prompt_builder = None
        self._session_id = "default"
        self._results: list[dict[str, Any]] = []
        self.worker_call_count = 0

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section("security_runtime_advice")
        self.system_prompt_builder = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._session_id = self._extract_session_id(getattr(ctx, "inputs", None))

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        session_id = self._extract_session_id(getattr(ctx, "inputs", None), self._session_id)
        advice = self.state.consume_advice(session_id)
        if self.system_prompt_builder is None:
            return
        if advice is None or not self.config.runtime_advice:
            self.system_prompt_builder.remove_section("security_runtime_advice")
            return

        self.system_prompt_builder.add_section(
            PromptSection(
                name="security_runtime_advice",
                content={"cn": advice.content, "en": advice.content},
                priority=97,
            )
        )

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        self._session_id = session_id
        event = SecurityEvent(
            event_type="tool_call",
            session_id=session_id,
            iteration=self._extract_iteration(inputs),
            tool_name=str(self._extract_input_value(inputs, "tool_name") or ""),
            arguments_digest=self._truncate(self._extract_input_value(inputs, "tool_args")),
        )
        self._handle_event(event)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        self._session_id = session_id
        event = SecurityEvent(
            event_type="tool_result",
            session_id=session_id,
            iteration=self._extract_iteration(inputs),
            tool_name=str(self._extract_input_value(inputs, "tool_name") or ""),
            result_digest=self._truncate(self._extract_input_value(inputs, "tool_result")),
        )
        self._handle_event(event)

    def get_session_snapshot(self, session_id: str) -> list[SecurityEvent]:
        return self.state.snapshot_events(session_id)

    def drain_review_requests(self) -> list[ReviewRequest]:
        return self.scheduler.drain()

    async def process_pending_reviews(self) -> list[ReviewResult]:
        if not self.config.async_review:
            return []

        results: list[ReviewResult] = []
        for request in self.scheduler.drain():
            if not self.scheduler.mark_review_started(request.session_id):
                continue
            result = await self.worker.review(request)
            self.worker_call_count += 1
            self._results.append(
                {
                    "session_id": result.session_id,
                    "summary": result.summary,
                    "runtime_advice": result.runtime_advice,
                    "candidates": result.candidates,
                }
            )
            results.append(result)
        return results

    def drain_candidates(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for result in self._results:
            candidates.extend(result.get("candidates", []))
        self._results.clear()
        return candidates

    def add_review_result_for_test(self, result: dict[str, Any]) -> None:
        self._results.append(result)

    def _handle_event(self, event: SecurityEvent) -> None:
        self.state.record_event(event)
        signals = self.classifier.classify(event)
        generated = self.state.record_signals(signals)
        if not self.config.timely_tool_failure_review:
            return

        for signal in generated:
            if signal.signal_type != "repeated_tool_failure":
                continue
            failure = signal.failure_class.value if signal.failure_class else "unknown_failure"
            self.scheduler.schedule(
                ReviewRequest(
                    request_type="timely_tool_failure_review",
                    session_id=signal.session_id,
                    priority=Severity.HIGH,
                    dedupe_key=(signal.session_id, signal.tool_name, failure),
                    iteration=signal.iteration,
                    signals=[signal],
                    counters=self.state.counter_snapshot(signal.session_id),
                    sample_events=self.state.snapshot_events(signal.session_id)[-5:],
                )
            )

    @staticmethod
    def _extract_session_id(inputs: Any, fallback: str = "default") -> str:
        if isinstance(inputs, dict):
            return str(inputs.get("conversation_id") or inputs.get("session_id") or fallback)
        return str(
            getattr(inputs, "conversation_id", None)
            or getattr(inputs, "session_id", None)
            or fallback
        )

    @staticmethod
    def _extract_iteration(inputs: Any) -> int:
        try:
            if isinstance(inputs, dict):
                return int(inputs.get("iteration", 0) or 0)
            return int(getattr(inputs, "iteration", 0) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_input_value(inputs: Any, key: str) -> Any:
        if isinstance(inputs, dict):
            return inputs.get(key, "")
        return getattr(inputs, key, "")

    def _truncate(self, value: Any) -> str:
        return str(value or "")[: max(1, self.config.max_event_chars)]

    @staticmethod
    def _parse_config(config: dict[str, Any]) -> SecurityReviewConfig:
        allowed = {field.name for field in fields(SecurityReviewConfig)}
        values = {key: config[key] for key in allowed if key in config}
        return SecurityReviewConfig(**values)
