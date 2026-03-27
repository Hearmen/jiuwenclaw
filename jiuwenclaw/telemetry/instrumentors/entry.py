# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Instrumentor for MessageHandler._process_stream — ENTRY span."""

from __future__ import annotations

import time

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from jiuwenclaw.utils import logger
from jiuwenclaw.telemetry.attributes import (
    GEN_AI_SPAN_TYPE,
    JIUWENCLAW_CHANNEL_ID,
    JIUWENCLAW_REQUEST_ID,
    JIUWENCLAW_SESSION_ID,
)
from jiuwenclaw.telemetry.context_propagation import inject_trace_context
from jiuwenclaw.telemetry.metrics import request_count, request_duration, request_error_count

_tracer = trace.get_tracer("jiuwenclaw.entry")


def instrument_entry() -> None:
    """Monkey-patch MessageHandler to create entry spans and propagate trace context."""
    try:
        from jiuwenclaw.gateway.message_handler import MessageHandler
    except ImportError:
        logger.debug("[Telemetry] MessageHandler not available, skipping entry instrumentor")
        return

    _original_process_stream = MessageHandler._process_stream
    _original_message_to_request = MessageHandler._message_to_request

    @staticmethod
    def _traced_message_to_request(msg):
        request = _original_message_to_request(msg)
        # Inject W3C TraceContext into request.metadata for cross-WebSocket propagation
        if request.metadata is None:
            request.metadata = {}
        inject_trace_context(request.metadata)
        return request

    async def _traced_process_stream(self, req, session_id):
        with _tracer.start_as_current_span(
            "channel.request",
            attributes={
                JIUWENCLAW_CHANNEL_ID: req.channel_id or "",
                JIUWENCLAW_SESSION_ID: session_id or "",
                JIUWENCLAW_REQUEST_ID: req.request_id or "",
                GEN_AI_SPAN_TYPE: "workflow",
            },
        ) as span:
            # Re-inject after span is created so the correct trace_id is propagated
            if req.metadata is None:
                req.metadata = {}
            inject_trace_context(req.metadata)

            request_count.add(1, {JIUWENCLAW_CHANNEL_ID: req.channel_id or ""})
            start = time.monotonic()
            try:
                await _original_process_stream(self, req, session_id)
                span.set_status(StatusCode.OK)
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc)[:256])
                span.record_exception(exc)
                request_error_count.add(1, {JIUWENCLAW_CHANNEL_ID: req.channel_id or ""})
                raise
            finally:
                duration = time.monotonic() - start
                request_duration.record(duration, {JIUWENCLAW_CHANNEL_ID: req.channel_id or ""})

    MessageHandler._message_to_request = _traced_message_to_request
    MessageHandler._process_stream = _traced_process_stream
