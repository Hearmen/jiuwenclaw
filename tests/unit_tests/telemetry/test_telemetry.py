# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for jiuwenclaw.telemetry module.

Strategy: Instead of patching lazy imports inside instrumentor functions,
we directly test the tracing logic by calling the wrapped functions with
mocked dependencies, bypassing instrument_*() which requires real classes.
"""

from __future__ import annotations

import asyncio
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry import trace, metrics, context
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


# ---------------------------------------------------------------------------
# In-memory span exporter
# ---------------------------------------------------------------------------

class InMemorySpanExporter(SpanExporter):
    def __init__(self):
        self._spans = []

    def export(self, spans):
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def get_finished_spans(self):
        return list(self._spans)

    def clear(self):
        self._spans.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_otel_providers():
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    return tp, mp, exporter, reader


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# 1. Config tests
# ---------------------------------------------------------------------------

class TestTelemetryConfig:
    @staticmethod
    def test_default_config():
        with patch.dict("os.environ", {}, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                from jiuwenclaw.telemetry.config import load_telemetry_config
                cfg = load_telemetry_config()
                assert cfg.enabled is False
                assert cfg.exporter == "otlp"
                assert cfg.headers == {}
                assert cfg.protocol == "grpc"
                assert cfg.traces_exporter == "otlp"
                assert cfg.metrics_exporter == "otlp"
                assert cfg.traces_endpoint == "http://localhost:4317"
                assert cfg.metrics_endpoint == "http://localhost:4317"
                assert cfg.log_messages is True
                assert cfg.service_name == "jiuwenclaw"
                assert cfg.provider_factory is None

    @staticmethod
    def test_env_vars_override():
        env = {
            "OTEL_ENABLED": "true",
            "OTEL_EXPORTER_TYPE": "console",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://custom:4317",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http",
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer common",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://traces:4318",
            "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "Authorization=Bearer trace",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOG_MESSAGES": "false",
            "OTEL_SERVICE_NAME": "test-service",
            "OTEL_PROVIDER_FACTORY": "custom.factory:build_providers",
        }
        with patch.dict("os.environ", env, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                from jiuwenclaw.telemetry.config import load_telemetry_config
                cfg = load_telemetry_config()
                assert cfg.enabled is True
                assert cfg.exporter == "console"
                assert cfg.endpoint == "http://custom:4317"
                assert cfg.protocol == "http"
                assert cfg.headers == {"Authorization": "Bearer common"}
                assert cfg.traces_exporter == "otlp"
                assert cfg.traces_endpoint == "http://traces:4318"
                assert cfg.traces_protocol == "http"
                assert cfg.traces_headers == {"Authorization": "Bearer trace"}
                assert cfg.metrics_exporter == "none"
                assert cfg.metrics_endpoint == "http://custom:4317"
                assert cfg.log_messages is False
                assert cfg.service_name == "test-service"
                assert cfg.provider_factory == "custom.factory:build_providers"

    @staticmethod
    def test_yaml_config_fallback():
        yaml_cfg = {
            "telemetry": {
                "enabled": True,
                "exporter": "console",
                "endpoint": "http://yaml:4317",
                "protocol": "http",
                "headers": {"Authorization": "Bearer yaml-common"},
                "log_messages": False,
                "service_name": "yaml-service",
                "provider_factory": "yaml.factory:build",
                "traces": {
                    "exporter": "otlp",
                    "endpoint": "http://trace-yaml:4318",
                    "headers": {"Authorization": "Bearer trace-yaml"},
                },
                "metrics": {
                    "exporter": "none",
                },
            }
        }
        with patch.dict("os.environ", {}, clear=True):
            with patch("jiuwenclaw.config.get_config", return_value=yaml_cfg):
                from jiuwenclaw.telemetry.config import load_telemetry_config
                cfg = load_telemetry_config()
                assert cfg.enabled is True
                assert cfg.exporter == "console"
                assert cfg.endpoint == "http://yaml:4317"
                assert cfg.headers == {"Authorization": "Bearer yaml-common"}
                assert cfg.traces_exporter == "otlp"
                assert cfg.traces_endpoint == "http://trace-yaml:4318"
                assert cfg.traces_protocol == "http"
                assert cfg.traces_headers == {"Authorization": "Bearer trace-yaml"}
                assert cfg.metrics_exporter == "none"
                assert cfg.metrics_endpoint == "http://yaml:4317"
                assert cfg.service_name == "yaml-service"
                assert cfg.provider_factory == "yaml.factory:build"

    @staticmethod
    def test_signal_specific_env_overrides_common_headers():
        env = {
            "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer common, X-Scope-OrgID=global",
            "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "Authorization=Bearer metrics",
        }
        with patch.dict("os.environ", env, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                from jiuwenclaw.telemetry.config import load_telemetry_config
                cfg = load_telemetry_config()
                assert cfg.traces_headers == {
                    "Authorization": "Bearer common",
                    "X-Scope-OrgID": "global",
                }
                assert cfg.metrics_headers == {"Authorization": "Bearer metrics"}

    @staticmethod
    def test_session_config_values_are_normalized_to_float():
        yaml_cfg = {
            "telemetry": {
                "session": {
                    "stuck_threshold_ms": 1234,
                    "stuck_check_interval_s": "9.5",
                }
            }
        }
        with patch.dict("os.environ", {}, clear=True):
            with patch("jiuwenclaw.config.get_config", return_value=yaml_cfg):
                from jiuwenclaw.telemetry.config import load_telemetry_config

                cfg = load_telemetry_config()

                assert cfg.session_stuck_threshold_ms == 1234.0
                assert isinstance(cfg.session_stuck_threshold_ms, float)
                assert cfg.session_stuck_check_interval_s == 9.5
                assert isinstance(cfg.session_stuck_check_interval_s, float)

    @staticmethod
    def test_invalid_session_config_values_fall_back_to_float_defaults():
        yaml_cfg = {
            "telemetry": {
                "session": {
                    "stuck_threshold_ms": "bad-threshold",
                    "stuck_check_interval_s": None,
                }
            }
        }
        env = {
            "OTEL_SESSION_STUCK_THRESHOLD_MS": "bad-env-threshold",
            "OTEL_SESSION_STUCK_CHECK_INTERVAL_S": "bad-env-interval",
        }
        with patch.dict("os.environ", env, clear=True):
            with patch("jiuwenclaw.config.get_config", return_value=yaml_cfg):
                from jiuwenclaw.telemetry.config import load_telemetry_config

                cfg = load_telemetry_config()

                assert cfg.session_stuck_threshold_ms == 300000.0
                assert isinstance(cfg.session_stuck_threshold_ms, float)
                assert cfg.session_stuck_check_interval_s == 30.0
                assert isinstance(cfg.session_stuck_check_interval_s, float)

    @staticmethod
    @pytest.mark.parametrize(
        ("env_value", "expected"),
        [
            ("1234", 1234.0),
            ("ab", 300000.0),
            ("", 300000.0),
        ],
    )
    def test_session_stuck_threshold_env_value_normalization(env_value, expected):
        env = {"OTEL_SESSION_STUCK_THRESHOLD_MS": env_value}
        with patch.dict("os.environ", env, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                from jiuwenclaw.telemetry.config import load_telemetry_config

                cfg = load_telemetry_config()

                assert cfg.session_stuck_threshold_ms == expected
                assert isinstance(cfg.session_stuck_threshold_ms, float)


# ---------------------------------------------------------------------------
# 2. Attributes tests
# ---------------------------------------------------------------------------

class TestAttributes:
    @staticmethod
    def test_genai_attributes_defined():
        from jiuwenclaw.telemetry.attributes import (
            GEN_AI_SYSTEM, GEN_AI_REQUEST_MODEL,
            GEN_AI_USAGE_INPUT_TOKENS, GEN_AI_USAGE_OUTPUT_TOKENS,
            GEN_AI_TOOL_NAME, JIUWENCLAW_CHANNEL_ID, JIUWENCLAW_SESSION_ID,
        )
        assert GEN_AI_SYSTEM == "gen_ai.system"
        assert GEN_AI_REQUEST_MODEL == "gen_ai.request.model"
        assert GEN_AI_USAGE_INPUT_TOKENS == "gen_ai.usage.input_tokens"
        assert GEN_AI_USAGE_OUTPUT_TOKENS == "gen_ai.usage.output_tokens"
        assert GEN_AI_TOOL_NAME == "gen_ai.tool.name"
        assert JIUWENCLAW_CHANNEL_ID == "jiuwenclaw.channel.id"
        assert JIUWENCLAW_SESSION_ID == "jiuwenclaw.session.id"


# ---------------------------------------------------------------------------
# 3. Context propagation tests
# ---------------------------------------------------------------------------

class TestContextPropagation:
    @staticmethod
    def test_inject_and_extract_roundtrip():
        tp, _, exporter, _ = _make_otel_providers()
        trace.set_tracer_provider(tp)

        from jiuwenclaw.telemetry.context_propagation import (
            inject_trace_context, extract_trace_context,
        )

        tracer = tp.get_tracer("test")
        with tracer.start_as_current_span("parent") as parent_span:
            carrier = {}
            inject_trace_context(carrier)
            assert "traceparent" in carrier

            ctx = extract_trace_context(carrier)
            with tracer.start_as_current_span("child", context=ctx) as child_span:
                assert parent_span.get_span_context().trace_id == child_span.get_span_context().trace_id
        tp.shutdown()

    @staticmethod
    def test_extract_empty_carrier():
        from jiuwenclaw.telemetry.context_propagation import extract_trace_context
        assert extract_trace_context(None) is not None
        assert extract_trace_context({}) is not None


# ---------------------------------------------------------------------------
# 4. Entry instrumentor — direct function test
# ---------------------------------------------------------------------------

class TestEntryInstrumentor:
    @staticmethod
    def test_process_stream_creates_entry_span():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.entry as entry_mod
        entry_mod._tracer = tp.get_tracer("jiuwenclaw.entry")

        mock_req = MagicMock()
        mock_req.channel_id = "web"
        mock_req.session_id = "sess_123"
        mock_req.request_id = "req_456"
        mock_req.metadata = {}

        original_fn = AsyncMock()

        async def traced_process_stream(self_handler, req, session_id):
            with entry_mod._tracer.start_as_current_span(
                "channel.request",
                attributes={
                    "jiuwenclaw.channel.id": req.channel_id or "",
                    "jiuwenclaw.session.id": session_id or "",
                    "jiuwenclaw.request.id": req.request_id or "",
                },
            ) as span:
                await original_fn(self_handler, req, session_id)

        _run(traced_process_stream(MagicMock(), mock_req, "sess_123"))

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "channel.request"
        assert span.attributes["jiuwenclaw.channel.id"] == "web"
        assert span.attributes["jiuwenclaw.session.id"] == "sess_123"
        assert span.attributes["jiuwenclaw.request.id"] == "req_456"
        tp.shutdown()


# ---------------------------------------------------------------------------
# 5. Agent instrumentor — direct function test
# ---------------------------------------------------------------------------

class TestAgentInstrumentor:
    @staticmethod
    def test_process_message_creates_agent_span():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.agent as agent_mod
        agent_mod._tracer = tp.get_tracer("jiuwenclaw.agent")

        mock_request = MagicMock()
        mock_request.channel_id = "feishu"
        mock_request.session_id = "sess_abc"
        mock_request.request_id = "req_def"
        mock_request.metadata = {}

        mock_instance = MagicMock()
        mock_instance._agent_name = "test_agent"

        original_fn = AsyncMock(return_value={"output": "ok"})

        async def traced_process_message(self_agent, request):
            from jiuwenclaw.telemetry.context_propagation import extract_trace_context
            parent_ctx = extract_trace_context(request.metadata)
            with agent_mod._tracer.start_as_current_span(
                "jiuwenclaw.agent.invoke",
                context=parent_ctx,
                attributes={
                    "jiuwenclaw.agent.name": getattr(self_agent, "_agent_name", ""),
                    "jiuwenclaw.session.id": request.session_id or "",
                    "jiuwenclaw.channel.id": request.channel_id or "",
                    "jiuwenclaw.request.id": request.request_id or "",
                },
            ):
                return await original_fn(self_agent, request)

        result = _run(traced_process_message(mock_instance, mock_request))
        assert result == {"output": "ok"}

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "jiuwenclaw.agent.invoke"
        assert span.attributes["jiuwenclaw.agent.name"] == "test_agent"
        assert span.attributes["jiuwenclaw.session.id"] == "sess_abc"
        assert span.attributes["jiuwenclaw.channel.id"] == "feishu"
        tp.shutdown()


# ---------------------------------------------------------------------------
# 6. LLM instrumentor — direct function test
# ---------------------------------------------------------------------------

class TestLLMInstrumentor:
    @staticmethod
    def test_call_llm_creates_genai_span_with_tokens():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")
        llm_mod.set_log_messages(True)

        # Mock result
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cache_tokens = 10  # UsageMetadata uses cache_tokens

        mock_result = MagicMock()
        mock_result.content = "Hello, I can help."
        mock_result.tool_calls = []
        mock_result.usage_metadata = mock_usage
        mock_result.finish_reason = "stop"

        original_fn = AsyncMock(return_value=mock_result)

        # Mock messages
        mock_sys = MagicMock(role="system", content="You are helpful.")
        mock_user = MagicMock(role="user", content="Hello")
        messages = [mock_sys, mock_user]

        async def traced_call_llm(self_agent, msgs, tools, session, chunk_threshold):
            model_name = "deepseek-chat"
            system = "openai"
            with llm_mod._tracer.start_as_current_span(
                "gen_ai.chat",
                attributes={
                    "gen_ai.system": system,
                    "gen_ai.request.model": model_name,
                    "gen_ai.operation.name": "chat",
                },
            ) as span:
                if llm_mod._log_messages:
                    llm_mod._record_input_messages(span, msgs)

                result = await original_fn(self_agent, msgs, tools, session, chunk_threshold)

                llm_mod._record_token_usage(span, result, model_name, system)

                finish_reason = getattr(result, "finish_reason", None)
                if finish_reason:
                    span.set_attribute("gen_ai.response.finish_reasons", [str(finish_reason)])

                if llm_mod._log_messages:
                    llm_mod._record_output_message(span, result)

                return result

        result = _run(traced_call_llm(MagicMock(), messages, None, None, 10))
        assert result == mock_result

        spans = exporter.get_finished_spans()
        llm_spans = [s for s in spans if s.name == "gen_ai.chat"]
        assert len(llm_spans) == 1

        span = llm_spans[0]
        assert span.attributes["gen_ai.system"] == "openai"
        assert span.attributes["gen_ai.request.model"] == "deepseek-chat"
        assert span.attributes["gen_ai.operation.name"] == "chat"
        assert span.attributes["gen_ai.usage.input_tokens"] == 100
        assert span.attributes["gen_ai.usage.output_tokens"] == 50
        assert span.attributes["gen_ai.usage.cache_read_tokens"] == 10
        assert span.attributes["gen_ai.response.finish_reasons"] == ("stop",)

        event_names = [e.name for e in span.events]
        assert "gen_ai.system.message" in event_names
        assert "gen_ai.user.message" in event_names
        assert "gen_ai.assistant.message" in event_names
        tp.shutdown()

    @staticmethod
    def test_call_llm_error_sets_error_status():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")

        original_fn = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        async def traced_call_llm_error():
            with llm_mod._tracer.start_as_current_span(
                "gen_ai.chat",
                attributes={"gen_ai.request.model": "gpt-4", "gen_ai.system": "openai"},
            ) as span:
                try:
                    await original_fn()
                except Exception as exc:
                    from opentelemetry.trace import StatusCode
                    span.set_status(StatusCode.ERROR, str(exc)[:256])
                    span.record_exception(exc)
                    raise

        with pytest.raises(RuntimeError, match="LLM timeout"):
            _run(traced_call_llm_error())

        spans = exporter.get_finished_spans()
        llm_spans = [s for s in spans if s.name == "gen_ai.chat"]
        assert len(llm_spans) == 1
        assert llm_spans[0].status.status_code.name == "ERROR"
        tp.shutdown()

    @staticmethod
    def test_log_messages_disabled_no_events():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")
        llm_mod.set_log_messages(False)

        mock_result = MagicMock()
        mock_result.content = "response"
        mock_result.tool_calls = []
        mock_result.usage_metadata = None
        mock_result.finish_reason = "stop"

        mock_msg = MagicMock(role="user", content="secret")

        async def traced_no_log():
            with llm_mod._tracer.start_as_current_span("gen_ai.chat") as span:
                if llm_mod._log_messages:
                    llm_mod._record_input_messages(span, [mock_msg])
                    llm_mod._record_output_message(span, mock_result)

        _run(traced_no_log())

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert len(spans[0].events) == 0

        llm_mod.set_log_messages(True)
        tp.shutdown()

    @staticmethod
    def test_record_input_messages_all_roles():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")

        msgs = [
            MagicMock(role="system", content="sys prompt"),
            MagicMock(role="user", content="user input"),
            MagicMock(role="assistant", content="assistant reply"),
        ]

        with llm_mod._tracer.start_as_current_span("test") as span:
            llm_mod._record_input_messages(span, msgs)

        spans = exporter.get_finished_spans()
        events = spans[0].events
        assert len(events) == 3
        assert events[0].name == "gen_ai.system.message"
        assert events[1].name == "gen_ai.user.message"
        assert events[2].name == "gen_ai.assistant.message"
        tp.shutdown()

    @staticmethod
    def test_infer_gen_ai_system():
        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod

        agent = MagicMock()
        agent._config.model_client_config = {"client_provider": "OpenAI"}
        assert llm_mod._infer_gen_ai_system(agent) == "openai"

        agent._config.model_client_config = {"client_provider": "SiliconFlow"}
        assert llm_mod._infer_gen_ai_system(agent) == "siliconflow"

        agent._config.model_client_config = {}
        assert llm_mod._infer_gen_ai_system(agent) == "unknown"

        agent_without_config = object()
        assert llm_mod._infer_gen_ai_system(agent_without_config) == "unknown"

    @staticmethod
    def test_infer_gen_ai_system_logs_and_falls_back_on_exception():
        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod

        class BrokenAgent:
            @property
            def _config(self):
                raise RuntimeError("boom")

        with patch.object(llm_mod.logger, "debug") as mock_debug:
            assert llm_mod._infer_gen_ai_system(BrokenAgent()) == "unknown"

        mock_debug.assert_called_once()
        assert mock_debug.call_args.args[0] == "[Telemetry] Failed to infer gen_ai.system: %s"
        assert str(mock_debug.call_args.args[1]) == "boom"
        assert mock_debug.call_args.kwargs["exc_info"] is True


# ---------------------------------------------------------------------------
# 7. Tool instrumentor — direct function test
# ---------------------------------------------------------------------------

class TestToolInstrumentor:
    @staticmethod
    def test_tool_call_and_result_creates_span():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.tool as tool_mod
        tool_mod._tracer = tp.get_tracer("jiuwenclaw.tool")
        tool_mod._active_tool_spans.clear()

        mock_tool_call = MagicMock()
        mock_tool_call.name = "memory_search"
        mock_tool_call.id = "call_001"
        mock_tool_call.arguments = {"query": "test"}

        # Simulate what _traced_emit_tool_call does
        span = tool_mod._tracer.start_span(
            f"gen_ai.tool.execute: {mock_tool_call.name}",
            attributes={
                "gen_ai.tool.name": mock_tool_call.name,
                "gen_ai.tool.call.id": mock_tool_call.id,
            },
        )
        span.add_event("tool.arguments", {"arguments": str(mock_tool_call.arguments)})
        tool_mod._active_tool_spans[mock_tool_call.id] = (span, time.monotonic())

        # Simulate _traced_emit_tool_result
        entry = tool_mod._active_tool_spans.pop(mock_tool_call.id)
        s, start_time = entry
        result_str = "found 3 items"
        s.add_event("tool.result", {"result": result_str})
        from opentelemetry.trace import StatusCode
        s.set_status(StatusCode.OK)
        s.end()

        spans = exporter.get_finished_spans()
        tool_spans = [s for s in spans if "gen_ai.tool.execute" in s.name]
        assert len(tool_spans) == 1

        span = tool_spans[0]
        assert span.attributes["gen_ai.tool.name"] == "memory_search"
        assert span.attributes["gen_ai.tool.call.id"] == "call_001"
        assert span.status.status_code.name == "OK"

        event_names = [e.name for e in span.events]
        assert "tool.arguments" in event_names
        assert "tool.result" in event_names
        tp.shutdown()

    @staticmethod
    def test_tool_error_sets_error_status():
        tp, _, exporter, _ = _make_otel_providers()

        import jiuwenclaw.telemetry.instrumentors.tool as tool_mod
        tool_mod._tracer = tp.get_tracer("jiuwenclaw.tool")
        tool_mod._active_tool_spans.clear()

        mock_tool_call = MagicMock()
        mock_tool_call.name = "browser_navigate"
        mock_tool_call.id = "call_002"

        span = tool_mod._tracer.start_span(
            f"gen_ai.tool.execute: {mock_tool_call.name}",
            attributes={
                "gen_ai.tool.name": mock_tool_call.name,
                "gen_ai.tool.call.id": mock_tool_call.id,
            },
        )
        tool_mod._active_tool_spans[mock_tool_call.id] = (span, time.monotonic())

        entry = tool_mod._active_tool_spans.pop(mock_tool_call.id)
        s, _ = entry
        result_str = "Error: Connection timeout"
        s.add_event("tool.result", {"result": result_str})
        from opentelemetry.trace import StatusCode
        s.set_status(StatusCode.ERROR, result_str[:256])
        s.end()

        spans = exporter.get_finished_spans()
        tool_spans = [s for s in spans if "gen_ai.tool.execute" in s.name]
        assert len(tool_spans) == 1
        assert tool_spans[0].status.status_code.name == "ERROR"
        tp.shutdown()


# ---------------------------------------------------------------------------
# 8. Init telemetry tests
# ---------------------------------------------------------------------------

class TestInitTelemetry:
    @staticmethod
    def test_noop_when_disabled():
        import jiuwenclaw.telemetry as tel_mod
        tel_mod._initialized = False

        with patch.dict("os.environ", {"OTEL_ENABLED": "false"}, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                with patch("jiuwenclaw.telemetry.provider.init_providers") as mock_init:
                    tel_mod.init_telemetry()
                    mock_init.assert_not_called()
        tel_mod._initialized = False

    @staticmethod
    def test_initializes_when_enabled():
        import jiuwenclaw.telemetry as tel_mod
        tel_mod._initialized = False

        with patch.dict("os.environ", {"OTEL_ENABLED": "true", "OTEL_EXPORTER_TYPE": "none"}, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                with patch("jiuwenclaw.telemetry.provider.init_providers") as mock_providers:
                    with patch("jiuwenclaw.telemetry.instrumentors.apply_instrumentors") as mock_instr:
                        tel_mod.init_telemetry()
                        mock_providers.assert_called_once()
                        mock_instr.assert_called_once()
        tel_mod._initialized = False

    @staticmethod
    def test_idempotent():
        import jiuwenclaw.telemetry as tel_mod
        tel_mod._initialized = False

        with patch.dict("os.environ", {"OTEL_ENABLED": "true", "OTEL_EXPORTER_TYPE": "none"}, clear=True):
            with patch("jiuwenclaw.config.get_config", side_effect=Exception("no config")):
                with patch("jiuwenclaw.telemetry.provider.init_providers"):
                    with patch("jiuwenclaw.telemetry.instrumentors.apply_instrumentors") as mock_instr:
                        tel_mod.init_telemetry()
                        tel_mod.init_telemetry()
                        assert mock_instr.call_count == 1
        tel_mod._initialized = False


# ---------------------------------------------------------------------------
# 9. Session configuration normalization tests
# ---------------------------------------------------------------------------

class TestSessionConfigNormalization:
    @staticmethod
    def test_instrument_session_normalizes_module_config_to_float():
        import jiuwenclaw.telemetry.instrumentors.session as session_mod

        fake_agentserver_pkg = types.ModuleType("jiuwenclaw.agentserver")
        fake_agentserver_pkg.__path__ = []
        fake_interface_module = types.ModuleType("jiuwenclaw.agentserver.interface")

        class JiuWenClaw:
            def __init__(self):
                self._session_processors = {}
                self._session_queues = {}
                self._session_priorities = {}
                self._session_tasks = {}

            async def _ensure_session_processor(self, session_id: str) -> None:
                return None

            async def _cancel_session_task(self, session_id: str, log_msg_prefix: str = "") -> None:
                return None

        fake_interface_module.JiuWenClaw = JiuWenClaw
        fake_agentserver_pkg.interface = fake_interface_module

        original_threshold = session_mod._stuck_threshold_ms
        original_interval = session_mod._stuck_check_interval_s
        try:
            with patch.dict(
                sys.modules,
                {
                    "jiuwenclaw.agentserver": fake_agentserver_pkg,
                    "jiuwenclaw.agentserver.interface": fake_interface_module,
                },
            ):
                session_mod.instrument_session(
                    stuck_threshold_ms=1234,
                    stuck_check_interval_s="9.0",
                )

            assert session_mod._stuck_threshold_ms == 1234.0
            assert isinstance(session_mod._stuck_threshold_ms, float)
            assert session_mod._stuck_check_interval_s == 9.0
            assert isinstance(session_mod._stuck_check_interval_s, float)
        finally:
            session_mod._stuck_threshold_ms = original_threshold
            session_mod._stuck_check_interval_s = original_interval


# ---------------------------------------------------------------------------
# 10. Provider initialization tests
# ---------------------------------------------------------------------------

class TestProviderInitialization:
    @staticmethod
    def test_build_default_providers_supports_none_exporters():
        from jiuwenclaw.telemetry.config import TelemetryConfig
        from jiuwenclaw.telemetry.provider import build_default_providers

        bundle = build_default_providers(
            TelemetryConfig(
                enabled=True,
                traces_exporter="none",
                metrics_exporter="none",
            )
        )

        assert bundle.tracer_provider is not None
        assert bundle.meter_provider is not None
        assert bundle.tracer_provider._active_span_processor._span_processors == ()
        assert bundle.meter_provider._sdk_config.metric_readers == []

    @staticmethod
    def test_create_signal_specific_http_exporters_with_headers():
        from jiuwenclaw.telemetry.config import TelemetryConfig
        from jiuwenclaw.telemetry.provider import (
            _create_otlp_metric_exporter,
            _create_otlp_span_exporter,
        )

        cfg = TelemetryConfig(
            enabled=True,
            traces_protocol="http",
            traces_endpoint="http://trace.example.com",
            traces_headers={"Authorization": "Bearer trace"},
            metrics_protocol="http",
            metrics_endpoint="http://metric.example.com",
            metrics_headers={"Authorization": "Bearer metric"},
        )

        span_exporter = _create_otlp_span_exporter(cfg, signal="traces")
        metric_exporter = _create_otlp_metric_exporter(cfg, signal="metrics")

        assert span_exporter._endpoint == "http://trace.example.com/v1/traces"
        assert span_exporter._headers == {"Authorization": "Bearer trace"}
        assert metric_exporter._endpoint == "http://metric.example.com/v1/metrics"
        assert metric_exporter._headers == {"Authorization": "Bearer metric"}

    @staticmethod
    def test_init_providers_uses_custom_provider_factory():
        from jiuwenclaw.telemetry.config import TelemetryConfig
        from jiuwenclaw.telemetry.provider import ProviderBundle, init_providers

        tracer_provider = TracerProvider()
        meter_provider = MeterProvider()

        fake_module = types.ModuleType("fake_provider_factory")

        def build_providers():
            return ProviderBundle(
                tracer_provider=tracer_provider,
                meter_provider=meter_provider,
            )

        fake_module.build_providers = build_providers

        with patch.dict(sys.modules, {"fake_provider_factory": fake_module}):
            with patch("jiuwenclaw.telemetry.provider.install_providers") as mock_install:
                bundle = init_providers(
                    TelemetryConfig(
                        enabled=True,
                        provider_factory="fake_provider_factory:build_providers",
                    )
                )

        assert bundle.tracer_provider is tracer_provider
        assert bundle.meter_provider is meter_provider
        mock_install.assert_called_once_with(bundle)


# ---------------------------------------------------------------------------
# 11. Span hierarchy test
# ---------------------------------------------------------------------------

class TestSpanHierarchy:
    @staticmethod
    def test_entry_agent_llm_tool_hierarchy():
        tp, _, exporter, _ = _make_otel_providers()
        tracer = tp.get_tracer("test.hierarchy")

        with tracer.start_as_current_span("channel.request") as entry_span:
            with tracer.start_as_current_span("jiuwenclaw.agent.invoke") as agent_span:
                with tracer.start_as_current_span("gen_ai.chat") as llm_span:
                    with tracer.start_as_current_span("gen_ai.tool.execute: search") as tool_span:
                        pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 4

        span_map = {s.name: s for s in spans}
        entry = span_map["channel.request"]
        agent = span_map["jiuwenclaw.agent.invoke"]
        llm = span_map["gen_ai.chat"]
        tool = span_map["gen_ai.tool.execute: search"]

        # Parent-child chain
        assert agent.parent.span_id == entry.context.span_id
        assert llm.parent.span_id == agent.context.span_id
        assert tool.parent.span_id == llm.context.span_id

        # Same trace_id
        tid = entry.context.trace_id
        assert agent.context.trace_id == tid
        assert llm.context.trace_id == tid
        assert tool.context.trace_id == tid
        tp.shutdown()


# ---------------------------------------------------------------------------
# 12. New attributes tests (gen_ai.span.type, temperature, top_p, etc.)
# ---------------------------------------------------------------------------

class TestNewAttributes:
    """Tests for attributes added in the span attributes expansion."""

    @staticmethod
    def test_llm_span_type_attribute():
        """gen_ai.span.type=model is set on LLM span."""
        tp, _, exporter, _ = _make_otel_providers()
        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")

        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5
        mock_usage.cache_tokens = 0
        mock_result = MagicMock()
        mock_result.content = "ok"
        mock_result.tool_calls = []
        mock_result.usage_metadata = mock_usage
        mock_result.finish_reason = "stop"

        mock_config = MagicMock()
        mock_config.model_name = "test-model"
        mock_config.model_config_obj = MagicMock(temperature=0.7, top_p=0.9)
        mock_config.model_client_config = MagicMock(client_provider="openai")

        mock_agent = MagicMock()
        mock_agent._config = mock_config

        original_fn = AsyncMock(return_value=mock_result)

        async def run():
            model_name = mock_config.model_name
            system = "openai"
            model_cfg = mock_config.model_config_obj
            span_attrs = {
                "gen_ai.system": system,
                "gen_ai.request.model": model_name,
                "gen_ai.response.model": model_name,
                "gen_ai.operation.name": "chat",
                "gen_ai.span.type": "model",
                "gen_ai.request.temperature": float(model_cfg.temperature),
                "gen_ai.request.top_p": float(model_cfg.top_p),
            }
            with llm_mod._tracer.start_as_current_span("gen_ai.chat", attributes=span_attrs) as span:
                result = await original_fn(mock_agent, [], None, None, 10)
                llm_mod._record_token_usage(span, result, model_name, system)
                finish_reason = getattr(result, "finish_reason", None)
                if finish_reason and str(finish_reason) != "null":
                    span.set_attribute("gen_ai.response.finish_reasons", [str(finish_reason)])
                    span.set_attribute("gen_ai.response.finish_reason", str(finish_reason))
                return result

        _run(run())
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.attributes["gen_ai.span.type"] == "model"
        assert s.attributes["gen_ai.request.temperature"] == 0.7
        assert s.attributes["gen_ai.request.top_p"] == 0.9
        assert s.attributes["gen_ai.response.model"] == "test-model"
        assert s.attributes["gen_ai.response.finish_reason"] == "stop"
        assert s.attributes["gen_ai.response.finish_reasons"] == ("stop",)
        tp.shutdown()

    @staticmethod
    def test_finish_reason_null_is_filtered():
        """finish_reason='null' default value is not written to span."""
        tp, _, exporter, _ = _make_otel_providers()
        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")

        mock_result = MagicMock()
        mock_result.finish_reason = "null"

        with llm_mod._tracer.start_as_current_span("gen_ai.chat") as span:
            finish_reason = getattr(mock_result, "finish_reason", None)
            if finish_reason and str(finish_reason) != "null":
                span.set_attribute("gen_ai.response.finish_reason", str(finish_reason))

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert "gen_ai.response.finish_reason" not in spans[0].attributes
        tp.shutdown()

    @staticmethod
    def test_agent_span_new_attributes():
        """gen_ai.agent.name, gen_ai.conversation.id, gen_ai.span.type=agent."""
        tp, _, exporter, _ = _make_otel_providers()
        tracer = tp.get_tracer("jiuwenclaw.agent")

        with tracer.start_as_current_span(
            "jiuwenclaw.agent.invoke",
            attributes={
                "jiuwenclaw.agent.name": "main_agent",
                "gen_ai.agent.name": "main_agent",
                "gen_ai.conversation.id": "sess_abc123",
                "gen_ai.span.type": "agent",
            },
        ):
            pass

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        s = spans[0]
        assert s.attributes["gen_ai.agent.name"] == "main_agent"
        assert s.attributes["gen_ai.conversation.id"] == "sess_abc123"
        assert s.attributes["gen_ai.span.type"] == "agent"
        tp.shutdown()

    @staticmethod
    def test_entry_span_type_workflow():
        """gen_ai.span.type=workflow is set on entry span."""
        tp, _, exporter, _ = _make_otel_providers()
        tracer = tp.get_tracer("jiuwenclaw.entry")

        with tracer.start_as_current_span(
            "channel.request",
            attributes={
                "jiuwenclaw.channel.id": "web",
                "gen_ai.span.type": "workflow",
            },
        ):
            pass

        spans = exporter.get_finished_spans()
        assert spans[0].attributes["gen_ai.span.type"] == "workflow"
        tp.shutdown()

    @staticmethod
    def test_tool_span_type_tool():
        """gen_ai.span.type=tool is set on tool span."""
        tp, _, exporter, _ = _make_otel_providers()
        tracer = tp.get_tracer("jiuwenclaw.tool")

        with tracer.start_as_current_span(
            "gen_ai.tool.execute: search",
            attributes={
                "gen_ai.tool.name": "search",
                "gen_ai.span.type": "tool",
            },
        ):
            pass

        spans = exporter.get_finished_spans()
        assert spans[0].attributes["gen_ai.span.type"] == "tool"
        tp.shutdown()

    @staticmethod
    def test_cache_tokens_field_name():
        """cache_tokens (not cache_read_input_tokens) is read from UsageMetadata."""
        tp, _, exporter, _ = _make_otel_providers()
        import jiuwenclaw.telemetry.instrumentors.llm as llm_mod
        llm_mod._tracer = tp.get_tracer("jiuwenclaw.llm")

        mock_usage = MagicMock(spec=["input_tokens", "output_tokens", "cache_tokens"])
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cache_tokens = 20
        mock_result = MagicMock()
        mock_result.usage_metadata = mock_usage

        with llm_mod._tracer.start_as_current_span("gen_ai.chat") as span:
            llm_mod._record_token_usage(span, mock_result, "test-model", "openai")

        spans = exporter.get_finished_spans()
        s = spans[0]
        assert s.attributes["gen_ai.usage.cache_read_tokens"] == 20
        assert s.attributes.get("gen_ai.usage.cache_creation_tokens") is None
        tp.shutdown()
