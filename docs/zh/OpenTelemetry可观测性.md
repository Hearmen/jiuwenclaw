# OpenTelemetry 可观测性

JiuWenClaw 内置了基于 OpenTelemetry 协议的可观测性能力，遵循 [OpenTelemetry GenAI 语义规范](https://opentelemetry.io/docs/specs/semconv/gen-ai/)，支持上报完整的调用链 Trace 和运行指标 Metric。

## 1. 功能概述

通过 OpenTelemetry 集成，可以观测以下调用链路：

| 类型 | 含义 | 记录内容 |
|------|------|----------|
| ENTRY | 请求入口 | 谁发的消息、从哪个渠道来的 |
| AGENT | Agent 调用 | 哪个 Agent 在执行、会话 ID |
| LLM | 大模型调用 | 模型名、Token 消耗（input/output/cache）、系统提示词、输入输出消息完整内容 |
| TOOL | 工具调用 | 工具名、参数、返回结果、是否报错 |

同时记录以下运行指标：
- 请求端到端时延、Agent 处理时延、LLM 调用时延、工具执行时延
- 请求总数、错误数、LLM 调用次数、工具调用次数、工具错误次数
- Token 消耗（按 input/output/cache 类型统计）

## 2. 快速启用

### 2.1 通过环境变量启用

```bash
# 启用 telemetry，使用 console 输出（开发调试）
OTEL_ENABLED=true OTEL_EXPORTER_TYPE=console jiuwenclaw-app

# 启用 telemetry，使用 OTLP 导出到 Jaeger/Tempo 等后端
OTEL_ENABLED=true OTEL_EXPORTER_TYPE=otlp OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 jiuwenclaw-app
```

### 2.2 通过 config.yaml 启用

在 `config.yaml` 中添加或修改 `telemetry` 段：

```yaml
telemetry:
  enabled: true                     # 总开关
  exporter: otlp                    # otlp / console / none
  endpoint: http://localhost:4317   # OTLP endpoint
  protocol: grpc                    # grpc / http
  log_messages: true                # 是否记录完整消息内容
  service_name: jiuwenclaw
```

> 环境变量优先级高于 config.yaml。

## 3. 配置参数

| 环境变量 | config.yaml 字段 | 默认值 | 说明 |
|----------|------------------|--------|------|
| `OTEL_ENABLED` | `telemetry.enabled` | `false` | 总开关 |
| `OTEL_EXPORTER_TYPE` | `telemetry.exporter` | `otlp` | 导出方式：otlp / console / none |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `telemetry.endpoint` | `http://localhost:4317` | OTLP 后端地址 |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `telemetry.protocol` | `grpc` | OTLP 协议：grpc / http |
| `OTEL_LOG_MESSAGES` | `telemetry.log_messages` | `true` | 是否在 Span Event 中记录完整消息内容 |
| `OTEL_SERVICE_NAME` | `telemetry.service_name` | `jiuwenclaw` | 服务名称 |

## 4. Trace 调用链结构

一次完整请求的 Trace 结构如下：

```
[ENTRY] channel.request
  └── [AGENT] jiuwenclaw.agent.invoke
        ├── [LLM] gen_ai.chat                    ← ReAct 第 1 轮
        │     ├── event: gen_ai.system.message    ← system prompt
        │     ├── event: gen_ai.user.message      ← 用户输入
        │     ├── event: gen_ai.assistant.message  ← 模型输出
        │     └── [TOOL] gen_ai.tool.execute: search_web
        │           ├── event: tool.arguments
        │           └── event: tool.result
        └── [LLM] gen_ai.chat                    ← ReAct 第 2 轮
              └── event: gen_ai.assistant.message  ← 最终回答
```

### Span Attributes（遵循 GenAI 语义规范）

**ENTRY span** (`gen_ai.span.type=workflow`):
- `jiuwenclaw.channel.id` — 渠道标识（feishu / web / wecom 等）
- `jiuwenclaw.session.id` — 会话 ID
- `jiuwenclaw.request.id` — 请求 ID
- `gen_ai.span.type` — `"workflow"`

**AGENT span** (`gen_ai.span.type=agent`):
- `jiuwenclaw.agent.name` — Agent 名称
- `jiuwenclaw.session.id` — 会话 ID
- `gen_ai.agent.name` — Agent 名称（GenAI 标准命名空间）
- `gen_ai.conversation.id` — 会话 ID（GenAI 标准命名空间）
- `gen_ai.span.type` — `"agent"`

**LLM span** (`gen_ai.span.type=model`):
- `gen_ai.system` — 模型提供商（openai 等）
- `gen_ai.request.model` — 请求模型名称
- `gen_ai.response.model` — 响应模型名称
- `gen_ai.operation.name` — 操作类型（chat）
- `gen_ai.request.temperature` — 温度参数
- `gen_ai.request.top_p` — top_p 参数
- `gen_ai.usage.input_tokens` — 输入 Token 数
- `gen_ai.usage.output_tokens` — 输出 Token 数
- `gen_ai.usage.total_tokens` — 总 Token 数
- `gen_ai.usage.cache_read_tokens` — 缓存读取 Token 数（有缓存时）
- `gen_ai.response.finish_reasons` — 结束原因（数组形式）
- `gen_ai.response.finish_reason` — 结束原因（字符串形式）
- `gen_ai.span.type` — `"model"`

**TOOL span** (`gen_ai.span.type=tool`):
- `gen_ai.tool.name` — 工具名称
- `gen_ai.tool.call.id` — 调用 ID
- `gen_ai.span.type` — `"tool"`

## 5. Metric 指标

| 指标名称 | 类型 | 说明 |
|----------|------|------|
| `jiuwenclaw.request.duration` | Histogram | 请求端到端时延（秒） |
| `jiuwenclaw.request.count` | Counter | 请求总数 |
| `jiuwenclaw.request.error.count` | Counter | 请求错误数 |
| `jiuwenclaw.agent.duration` | Histogram | Agent 处理时延（秒） |
| `gen_ai.client.operation.duration` | Histogram | LLM 调用时延（秒） |
| `gen_ai.client.token.usage` | Counter | Token 消耗（按 gen_ai.token.type 区分） |
| `gen_ai.client.operation.count` | Counter | LLM 调用次数 |
| `gen_ai.tool.duration` | Histogram | 工具执行时延（秒） |
| `gen_ai.tool.call.count` | Counter | 工具调用次数 |
| `gen_ai.tool.error.count` | Counter | 工具错误次数 |

## 6. 对接 Jaeger 示例

```bash
# 启动 Jaeger（支持 OTLP gRPC）
docker run -d --name jaeger \
  -p 16686:16686 \
  -p 4317:4317 \
  jaegertracing/all-in-one:latest

# 启动 JiuWenClaw
OTEL_ENABLED=true OTEL_EXPORTER_TYPE=otlp jiuwenclaw-app
```

打开 http://localhost:16686 即可在 Jaeger UI 中查看完整的调用链。

## 7. 注意事项

- 当 `telemetry.enabled` 为 `false` 时，telemetry 模块完全不加载，对性能零影响
- `log_messages` 设为 `true` 会在 Span Event 中记录完整的 system prompt、用户输入和模型输出，数据量较大，生产环境可按需关闭
- 支持跨 WebSocket 的 W3C TraceContext 传播，Gateway 和 AgentServer 分离部署时调用链仍然完整

## 8. Provider 低侵入扩展与第三方集成

为兼容第三方集成场景，JiuWenClaw 在默认 OpenTelemetry 初始化链路上提供了低侵入扩展能力，支持：

- Trace 和 Metrics 分别上报到不同后端
- `service.name`、`protocol`、`endpoint`、`headers/token` 由用户自定义输入
- 在默认实现不满足时，通过扩展点完全替换 provider 初始化逻辑

### 8.1 设计目标

本能力遵循以下原则：

- `OTEL_ENABLED` 仍然是 telemetry 的唯一总开关
- 不额外引入 trace / metrics 独立采集开关，只支持独立上报配置
- trace 和 metrics 是否真正导出，由各自 exporter 是否为 `none` 决定
- 默认 provider 实现继续可用
- 第三方可通过 provider factory 扩展点完全接管 provider 初始化

### 8.2 上报控制语义

本方案区分“采集”和“上报”：

- `OTEL_ENABLED=true` 时，现有埋点逻辑继续执行
- trace 是否真正上报，由 `OTEL_TRACES_EXPORTER` 控制
- metrics 是否真正上报，由 `OTEL_METRICS_EXPORTER` 控制

例如：

```bash
OTEL_ENABLED=true
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=none
```

表示：

- trace 继续采集并导出
- metrics 继续记录，但不导出到后端

这样可以在不重构埋点逻辑的前提下，实现 trace 和 metrics 的独立上报控制。

### 8.3 Signal 专属配置

除第 3 章中的公共配置外，还支持 trace / metrics 的独立配置。

公共配置：

```bash
OTEL_SERVICE_NAME
OTEL_EXPORTER_TYPE
OTEL_EXPORTER_OTLP_ENDPOINT
OTEL_EXPORTER_OTLP_PROTOCOL
OTEL_EXPORTER_OTLP_HEADERS
```

Trace 专属配置：

```bash
OTEL_TRACES_EXPORTER
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
OTEL_EXPORTER_OTLP_TRACES_PROTOCOL
OTEL_EXPORTER_OTLP_TRACES_HEADERS
```

Metrics 专属配置：

```bash
OTEL_METRICS_EXPORTER
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT
OTEL_EXPORTER_OTLP_METRICS_PROTOCOL
OTEL_EXPORTER_OTLP_METRICS_HEADERS
```

其中：

- `OTEL_EXPORTER_TYPE` 仍作为公共 exporter fallback，取值 `otlp` / `console` / `none`
- `OTEL_EXPORTER_OTLP_HEADERS` 使用 `k=v,k2=v2` 字符串格式

优先级如下：

1. signal 专属环境变量
2. 公共环境变量
3. `config.yaml` 默认值

例如：

- `OTEL_TRACES_EXPORTER` 未配置时，回退到 `OTEL_EXPORTER_TYPE`
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` 未配置时，回退到 `OTEL_EXPORTER_OTLP_ENDPOINT`
- `OTEL_EXPORTER_OTLP_TRACES_HEADERS` 未配置时，回退到 `OTEL_EXPORTER_OTLP_HEADERS`

Metrics 同理。

### 8.4 Provider Factory 扩展点

当默认 provider 逻辑不能满足需求时，可以通过以下扩展点完全接管 provider 初始化：

```bash
OTEL_PROVIDER_FACTORY=package.module:function
```

函数签名约定为：

```python
def build_providers() -> ProviderBundle:
    ...
```

返回值需要包含：

```python
ProviderBundle(
    tracer_provider=...,
    meter_provider=...,
)
```

框架行为如下：

- 若未配置 `OTEL_PROVIDER_FACTORY`，使用内置默认 provider 初始化逻辑
- 若配置了 `OTEL_PROVIDER_FACTORY`，框架动态加载该函数
- 第三方函数负责自行读取环境变量、构造 tracer provider 和 meter provider
- 框架仅负责安装 provider，并继续应用 instrumentor

### 8.5 第三方接入示例

直接使用默认 provider，将 trace 和 metrics 分别上报到不同后端：

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

只导出 trace、不导出 metrics：

```bash
export OTEL_ENABLED=true
export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=none
```

使用自定义 provider factory：

```bash
export OTEL_ENABLED=true
export OTEL_PROVIDER_FACTORY=mycompany.custom_otel:build_providers
```

示例：

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
