# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Metric instrument definitions for JiuWenClaw telemetry."""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("jiuwenclaw")

# --- Histograms ---
request_duration = _meter.create_histogram(
    name="jiuwenclaw.request.duration",
    unit="s",
    description="End-to-end request processing duration",
)

agent_duration = _meter.create_histogram(
    name="jiuwenclaw.agent.duration",
    unit="s",
    description="Agent invoke duration",
)

llm_duration = _meter.create_histogram(
    name="gen_ai.client.operation.duration",
    unit="s",
    description="GenAI LLM call duration",
)

tool_duration = _meter.create_histogram(
    name="gen_ai.tool.duration",
    unit="s",
    description="Tool execution duration",
)

# --- Counters ---
request_count = _meter.create_counter(
    name="jiuwenclaw.request.count",
    unit="{request}",
    description="Total request count",
)

request_error_count = _meter.create_counter(
    name="jiuwenclaw.request.error.count",
    unit="{request}",
    description="Failed request count",
)

llm_call_count = _meter.create_counter(
    name="gen_ai.client.operation.count",
    unit="{call}",
    description="LLM call count",
)

token_usage = _meter.create_counter(
    name="gen_ai.client.token.usage",
    unit="{token}",
    description="Token usage by type (input/output/cache)",
)

tool_call_count = _meter.create_counter(
    name="gen_ai.tool.call.count",
    unit="{call}",
    description="Tool call count",
)

tool_error_count = _meter.create_counter(
    name="gen_ai.tool.error.count",
    unit="{call}",
    description="Tool call error count",
)

# --- Session metrics ---
session_state_count = _meter.create_counter(
    name="jiuwenclaw.session.state",
    unit="{transition}",
    description="Session state transition count",
)

session_stuck_count = _meter.create_counter(
    name="jiuwenclaw.session.stuck",
    unit="{occurrence}",
    description="Session stuck occurrence count",
)

session_stuck_age = _meter.create_histogram(
    name="jiuwenclaw.session.stuck_age_ms",
    unit="ms",
    description="Duration a session has been stuck",
)
