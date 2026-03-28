# OpenTelemetry observability

JiuWenClaw includes observability built on the OpenTelemetry protocol. It follows the [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) and supports exporting full call-chain traces and runtime metrics.

## 1. Overview

Through OpenTelemetry integration, you can observe the following call paths:

| Type | Meaning | What is recorded |
|------|---------|------------------|
| ENTRY | Request entry | Who sent the message, which channel it came from |
| AGENT | Agent invocation | Which agent is running, session ID |
| LLM | Large model call | Model name, token usage (input/output/cache), system prompt, full input and output message content |
| TOOL | Tool call | Tool name, arguments, return value, whether an error occurred |

The following runtime metrics are also recorded:

- End-to-end request latency, agent processing latency, LLM call latency, tool execution latency
- Total requests, error count, LLM call count, tool call count, tool error count
- Token usage (broken down by input/output/cache)

## 2. Quick enable

### 2.1 Via environment variables

```bash
# Enable telemetry with console output (development / debugging)
OTEL_ENABLED=true OTEL_EXPORTER_TYPE=console jiuwenclaw-app

# Enable telemetry with OTLP export to Jaeger, Tempo, etc.
OTEL_ENABLED=true OTEL_EXPORTER_TYPE=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 jiuwenclaw-app
```

### 2.2 Via config.yaml

Add or edit the `telemetry` section in `config.yaml`:

```yaml
telemetry:
  enabled: true                     # Master switch
  exporter: otlp                    # otlp / console / none
  endpoint: http://localhost:4317   # OTLP endpoint
  protocol: grpc                    # grpc / http
  log_messages: true                # Whether to record full message content
  service_name: jiuwenclaw
```

> Environment variables take precedence over `config.yaml`.

## 3. Configuration parameters

| Environment variable | config.yaml field | Default | Description |
|----------------------|-------------------|---------|-------------|
| `OTEL_ENABLED` | `telemetry.enabled` | `false` | Master switch |
| `OTEL_EXPORTER_TYPE` | `telemetry.exporter` | `otlp` | Exporter: otlp / console / none |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `telemetry.endpoint` | `http://localhost:4317` | OTLP backend URL |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `telemetry.protocol` | `grpc` | OTLP protocol: grpc / http |
| `OTEL_LOG_MESSAGES` | `telemetry.log_messages` | `true` | Whether to record full message content in span events |
| `OTEL_SERVICE_NAME` | `telemetry.service_name` | `jiuwenclaw` | Service name |

## 4. Trace structure

A full request trace looks like this:

```
[ENTRY] channel.request
  └── [AGENT] jiuwenclaw.agent.invoke
        ├── [LLM] gen_ai.chat                    ← ReAct round 1
        │     ├── event: gen_ai.system.message    ← system prompt
        │     ├── event: gen_ai.user.message      ← user input
        │     ├── event: gen_ai.assistant.message  ← model output
        │     └── [TOOL] gen_ai.tool.execute: search_web
        │           ├── event: tool.arguments
        │           └── event: tool.result
        └── [LLM] gen_ai.chat                    ← ReAct round 2
              └── event: gen_ai.assistant.message  ← final answer
```

### Span attributes (GenAI semantic conventions)

**ENTRY span** (`gen_ai.span.type=workflow`):

- `jiuwenclaw.channel.id` — Channel identifier (feishu / web / wecom, etc.)
- `jiuwenclaw.session.id` — Session ID
- `jiuwenclaw.request.id` — Request ID
- `gen_ai.span.type` — `"workflow"`

**AGENT span** (`gen_ai.span.type=agent`):

- `jiuwenclaw.agent.name` — Agent name
- `jiuwenclaw.session.id` — Session ID
- `gen_ai.agent.name` — Agent name (GenAI standard namespace)
- `gen_ai.conversation.id` — Conversation ID (GenAI standard namespace)
- `gen_ai.span.type` — `"agent"`

**LLM span** (`gen_ai.span.type=model`):

- `gen_ai.system` — Model provider (openai, etc.)
- `gen_ai.request.model` — Requested model name
- `gen_ai.response.model` — Response model name
- `gen_ai.operation.name` — Operation type (chat)
- `gen_ai.request.temperature` — Temperature
- `gen_ai.request.top_p` — top_p
- `gen_ai.usage.input_tokens` — Input tokens
- `gen_ai.usage.output_tokens` — Output tokens
- `gen_ai.usage.total_tokens` — Total tokens
- `gen_ai.usage.cache_read_tokens` — Cache read tokens (when caching is used)
- `gen_ai.response.finish_reasons` — Finish reasons (array)
- `gen_ai.response.finish_reason` — Finish reason (string)
- `gen_ai.span.type` — `"model"`

**TOOL span** (`gen_ai.span.type=tool`):

- `gen_ai.tool.name` — Tool name
- `gen_ai.tool.call.id` — Call ID
- `gen_ai.span.type` — `"tool"`

## 5. Metrics

| Metric name | Type | Description |
|-------------|------|-------------|
| `jiuwenclaw.request.duration` | Histogram | End-to-end request latency (seconds) |
| `jiuwenclaw.request.count` | Counter | Total requests |
| `jiuwenclaw.request.error.count` | Counter | Request errors |
| `jiuwenclaw.agent.duration` | Histogram | Agent processing latency (seconds) |
| `gen_ai.client.operation.duration` | Histogram | LLM call latency (seconds) |
| `gen_ai.client.token.usage` | Counter | Token usage (by `gen_ai.token.type`) |
| `gen_ai.client.operation.count` | Counter | LLM call count |
| `gen_ai.tool.duration` | Histogram | Tool execution latency (seconds) |
| `gen_ai.tool.call.count` | Counter | Tool call count |
| `gen_ai.tool.error.count` | Counter | Tool errors |

## 6. Jaeger example

```bash
# Start Jaeger (OTLP gRPC supported)
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest

# Start JiuWenClaw
OTEL_ENABLED=true OTEL_EXPORTER_TYPE=otlp jiuwenclaw-app
```

Open http://localhost:16686 to view full traces in the Jaeger UI.

## 7. Notes

- When `telemetry.enabled` is `false`, the telemetry module is not loaded at all; there is no performance impact.
- With `log_messages` set to `true`, full system prompts, user input, and model output are recorded in span events, which can be large; you may disable this in production as needed.
- W3C TraceContext propagation across WebSockets is supported, so traces stay complete when Gateway and AgentServer are deployed separately.

## 8. Low-intrusion provider extension and third-party integration

For third-party integration, JiuWenClaw exposes a low-intrusion extension on the default OpenTelemetry initialization path:

- Send traces and metrics to different backends
- Let users supply `service.name`, `protocol`, `endpoint`, and `headers` / tokens
- Replace provider initialization entirely via an extension point when the default implementation is not enough

### 8.1 Design goals

This behavior follows these principles:

- `OTEL_ENABLED` remains the single master switch for telemetry
- There are no separate “collect trace / collect metrics” switches—only independent export configuration
- Whether traces and metrics are actually exported is determined by each exporter being `none` or not
- The default provider implementation remains available
- Third parties can take over provider initialization completely via the provider factory extension

### 8.2 Export semantics

This design separates “collection” from “export”:

- When `OTEL_ENABLED=true`, existing instrumentation continues to run
- Whether traces are actually exported is controlled by `OTEL_TRACES_EXPORTER`
- Whether metrics are actually exported is controlled by `OTEL_METRICS_EXPORTER`

Example:

```bash
OTEL_ENABLED=true
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=none
```

Meaning:

- Traces are still collected and exported
- Metrics are still recorded but not exported to a backend

This allows independent control of trace and metric export without refactoring instrumentation.

### 8.3 Signal-specific configuration

In addition to the shared settings in section 3, trace and metrics can be configured independently.

Shared configuration:

```bash
OTEL_SERVICE_NAME
OTEL_EXPORTER_TYPE
OTEL_EXPORTER_OTLP_ENDPOINT
OTEL_EXPORTER_OTLP_PROTOCOL
OTEL_EXPORTER_OTLP_HEADERS
```

Trace-specific configuration:

```bash
OTEL_TRACES_EXPORTER
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
OTEL_EXPORTER_OTLP_TRACES_PROTOCOL
OTEL_EXPORTER_OTLP_TRACES_HEADERS
```

Metrics-specific configuration:

```bash
OTEL_METRICS_EXPORTER
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
OTEL_EXPORTER_OTLP_METRICS_PROTOCOL
OTEL_EXPORTER_OTLP_METRICS_HEADERS
```

Details:

- `OTEL_EXPORTER_TYPE` still acts as the shared exporter fallback; values are `otlp` / `console` / `none`
- `OTEL_EXPORTER_OTLP_HEADERS` uses the `k=v,k2=v2` string format

Precedence:

1. Signal-specific environment variables
2. Shared environment variables
3. Defaults from `config.yaml`

Examples:

- If `OTEL_TRACES_EXPORTER` is unset, it falls back to `OTEL_EXPORTER_TYPE`
- If `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is unset, it falls back to `OTEL_EXPORTER_OTLP_ENDPOINT`
- If `OTEL_EXPORTER_OTLP_TRACES_HEADERS` is unset, it falls back to `OTEL_EXPORTER_OTLP_HEADERS`

The same applies to metrics.

### 8.4 Provider factory extension

When the default provider logic is insufficient, you can take over initialization with:

```bash
OTEL_PROVIDER_FACTORY=package.module:function
```

The function signature is:

```python
def build_providers() -> ProviderBundle:
    ...
```

The return value must include:

```python
ProviderBundle(
    tracer_provider=...,
    meter_provider=...,
)
```

Framework behavior:

- If `OTEL_PROVIDER_FACTORY` is unset, the built-in default provider initialization is used
- If it is set, the framework loads that function dynamically
- The third-party function reads environment variables and constructs the tracer and meter providers
- The framework installs the providers and continues to apply instrumentors

### 8.5 Third-party examples

Default provider, traces and metrics to different backends:

```bash
export OTEL_ENABLED=true
export OTEL_SERVICE_NAME=my-app

export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://trace.example.com
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http
export OTEL_EXPORTER_OTLP_TRACES_HEADERS='Authorization=Bearer trace-token'

export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://metrics.example.com
export OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http
export OTEL_EXPORTER_OTLP_METRICS_HEADERS='Authorization=Bearer metrics-token'
```

Export traces only, not metrics:

```bash
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
```

Custom provider factory:

```bash
export OTEL_ENABLED=true
export OTEL_PROVIDER_FACTORY=mycompany.custom_otel:build_providers
```

Example implementation:

```python
import os

from jiuwenclaw.telemetry.provider import ProviderBundle
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def build_providers() -> ProviderBundle:
    service_name = os.getenv("OTEL_SERVICE_NAME", "my-app")
    trace_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
    metric_endpoint = os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "")

    trace_token = os.getenv("TRACE_BACKEND_TOKEN", "")
    metric_token = os.getenv("METRIC_BACKEND_TOKEN", "")

    resource = Resource.create({
        SERVICE_NAME: service_name,
        "service.version": os.getenv("APP_VERSION", "unknown"),
    })

    tracer_provider = TracerProvider(resource=resource)
    if trace_endpoint:
        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{trace_endpoint}/v1/traces",
                    headers={"Authorization": f"Bearer {trace_token}"},
                )
            )
        )

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{metric_endpoint}/v1/metrics",
                    headers={"Authorization": f"Bearer {metric_token}"},
                ),
                export_interval_millis=15000,
            )
        ],
    )

    return ProviderBundle(
        tracer_provider=tracer_provider,
        meter_provider=meter_provider,
    )
```
