# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SecurityReviewAndSkillRail for in-task security supervision."""
from __future__ import annotations

from collections.abc import Callable
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
    SecuritySignal,
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
        self._session_signals: dict[str, list[SecuritySignal]] = {}
        self._message_provider: Callable[[str], list[dict[str, Any]]] | None = None
        self._skill_state_provider: Callable[[], dict[str, Any]] | None = None
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
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        query = self._extract_input_value(inputs, "query")
        if query:
            self.state.record_message(session_id, "user", self._truncate(query))
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

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        self._session_id = session_id
        response = self._extract_input_value(inputs, "response")
        content = getattr(response, "content", response)
        self.state.record_message(session_id, "assistant", self._truncate(content))
        event = SecurityEvent(
            event_type="model_output",
            session_id=session_id,
            iteration=self._extract_iteration(inputs),
            result_digest=self._truncate(content),
        )
        self._handle_event(event)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        session_id = self._extract_session_id(inputs, self._session_id)
        signals = self._session_signals.get(session_id, [])
        session_review_signals = [
            signal for signal in signals if signal.severity in {Severity.LOW, Severity.MEDIUM}
        ]
        if not session_review_signals:
            return
        iteration = max((signal.iteration for signal in session_review_signals), default=0)
        self.scheduler.schedule(
            ReviewRequest(
                request_type="session_end_review",
                session_id=session_id,
                priority=Severity.MEDIUM,
                dedupe_key=(session_id, "session_end_review", str(iteration)),
                iteration=iteration,
                signals=session_review_signals[-5:],
                counters=self.state.counter_snapshot(session_id),
                sample_events=self.state.snapshot_events(session_id)[-5:],
            )
        )

    def get_session_snapshot(self, session_id: str) -> list[SecurityEvent]:
        return self.state.snapshot_events(session_id)

    def drain_review_requests(self) -> list[ReviewRequest]:
        return self.scheduler.drain()

    def update_llm(self, llm: Any | None) -> None:
        self.worker.update_llm(llm)

    def update_config(self, config: dict[str, Any] | None = None) -> None:
        """Hot-update bounded security review configuration."""
        self.config = self._parse_config(config or {})
        self.state.config = self.config
        self.scheduler.config = self.config

    def set_context_providers(
        self,
        *,
        message_provider: Callable[[str], list[dict[str, Any]]] | None = None,
        skill_state_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._message_provider = message_provider
        self._skill_state_provider = skill_state_provider

    async def process_pending_reviews(self) -> list[ReviewResult]:
        if not self.config.async_review:
            return []

        results: list[ReviewResult] = []
        for request in self.scheduler.drain():
            if not self.scheduler.mark_review_started(request.session_id):
                continue
            request = self._enrich_request(request)
            result = await self.worker.review(request)
            self.worker_call_count += 1
            if result.runtime_advice:
                self.state.set_runtime_advice(
                    result.session_id,
                    result.runtime_advice,
                    severity=request.priority,
                )
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
            candidates.extend(
                candidate
                for candidate in result.get("candidates", [])
                if self._candidate_enabled(candidate)
            )
        self._results.clear()
        return candidates

    def add_review_result_for_test(self, result: dict[str, Any]) -> None:
        self._results.append(result)

    def _handle_event(self, event: SecurityEvent) -> None:
        self.state.record_event(event)
        signals = self.classifier.classify(event)
        if signals:
            self._session_signals.setdefault(event.session_id, []).extend(signals)
        self.state.record_signals(signals)

    def _enrich_request(self, request: ReviewRequest) -> ReviewRequest:
        request.sample_messages = self._sample_messages(request.session_id)[-8:]
        request.skill_state = self._skill_state()
        return request

    def _sample_messages(self, session_id: str) -> list[dict[str, str]]:
        if self._message_provider is not None:
            try:
                raw_messages = self._message_provider(session_id)
            except Exception:
                raw_messages = []
            messages: list[dict[str, str]] = []
            for message in raw_messages[-8:]:
                role = str(message.get("role") or message.get("type") or "unknown")
                content = str(message.get("content") or message.get("content_digest") or "")
                if content.strip():
                    messages.append({"role": role, "content_digest": self._truncate(content)})
            if messages:
                return messages
        return self.state.snapshot_messages(session_id)[-8:]

    def _skill_state(self) -> dict[str, Any]:
        if self._skill_state_provider is None:
            return {}
        try:
            return self._skill_state_provider()
        except Exception:
            return {}

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

    def _candidate_enabled(self, candidate: dict[str, Any]) -> bool:
        candidate_type = candidate.get("type")
        if candidate_type == "security_rule":
            return self.config.propose_policy_rules
        if candidate_type in {"security_skill", "security_evolution"}:
            return self.config.evolve_security_skills
        return True

    @staticmethod
    def _parse_config(config: dict[str, Any]) -> SecurityReviewConfig:
        allowed = {field.name for field in fields(SecurityReviewConfig)}
        values = {key: config[key] for key in allowed if key in config}
        return SecurityReviewConfig(**values)
