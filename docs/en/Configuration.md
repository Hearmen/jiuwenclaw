# Configuration

JiuwenClaw reads settings from `config/config.yaml`, `.env`, and the web UI. This document explains **what you can change in the UI**, **what must be edited in files**, and what each option does.

---

## 1. Configurable in the web UI

These can be changed in the web app; values are written back to `.env` or config as appropriate.

**Path**: left sidebar → **Configuration**

![Configuration](../assets/images/config.png)

**Saved to**: `.env` (environment variables)

| Field | Environment variable | Description |
|--------|------------------------|-------------|
| `api_base` | `API_BASE` | Model API base URL (e.g. `https://api.deepseek.com`) |
| `api_key` | `API_KEY` | Model API key |
| `model` | `MODEL_NAME` | Model name (e.g. `deepseek-chat`) |
| `model_provider` | `MODEL_PROVIDER` | Provider (e.g. `OpenAI`) |
| `embed_api_base` | `EMBED_API_BASE` | Embedding API base URL |
| `embed_api_key` | `EMBED_API_KEY` | Embedding API key |
| `embed_model` | `EMBED_MODEL` | Embedding model name |
| `video_model` | `VIDEO_MODEL_NAME` | Video processing model |
| `audio_model` | `AUDIO_MODEL_NAME` | Audio processing model |
| `vision_model` | `VISION_MODEL_NAME` | Vision model |
| `image_gen_model` | `IMAGE_GEN_MODEL_NAME` | Image generation model |
| `jina_api_key` | `JINA_API_KEY` | Jina search API key |
| `serper_api_key` | `SERPER_API_KEY` | Serper search API key |
| `perplexity_api_key` | `PERPLEXITY_API_KEY` | Perplexity API key |
| `github_token` | `GITHUB_TOKEN` | GitHub PAT; SkillNet search/install uses the GitHub API |
| `free_search_proxy_url` | `FREE_SEARCH_PROXY_URL` | Optional HTTP/HTTPS proxy for free search, webpage fetch, and SkillNet network requests. The UI asks for username/password and stores `http://username:password@proxyhk.huawei.com:8080`. |
| `evolution_auto_scan` | `EVOLUTION_AUTO_SCAN` | Auto-scan evolvable skills after each turn (`true`/`false`) |

**Note**: After saving, the backend restarts to load new settings. Model fields (`api_base`, `api_key`, `model`, `model_provider`) are required.

### Multi-model management and aliases

The **model list** block in the Configuration panel supports maintaining multiple models simultaneously. Fields for each model entry:

| Field | Required | Description |
|-------|----------|-------------|
| `model_name` | Yes | API-level model identifier (e.g. `gpt-4o`, `deepseek-chat`) |
| `alias` | No | Display name / switch identifier; defaults to `model_name` if left empty |
| `api_base` | Yes | API base URL for this model |
| `api_key` | Yes | API key for this model |
| `model_provider` | Yes | Provider (e.g. `OpenAI`, `DeepSeek`) |
| `temperature` | No | Sampling temperature, default `0.95` |

**`alias` rules**:
- If left blank, the stored value is automatically set to `model_name`.
- Must be globally unique across all configured models: cannot match another model's `alias` or `model_name`.
- When switching models (web dropdown / CLI `\model <name>`), either `alias` or `model_name` can be used as the identifier.

Multi-model configuration is saved to the `models.defaults` list in `config.yaml`. Example:

```yaml
models:
  defaults:
    - alias: my-gpt          # display name; used for switching in both UI and CLI
      model_client_config:
        api_base: https://api.openai.com/v1
        api_key: ${API_KEY}
        model_name: gpt-4o
        client_provider: OpenAI
        timeout: 1800
        verify_ssl: false
      model_config_obj:
        temperature: 0.95
```

The first entry in the list is the default model; use the UI to drag-reorder or click "Set as default" to change it.

## 2. Not configurable in the web UI

Edit **`config/config.yaml`** or **`.env`** directly; there is no UI for these.

### 2.1 `config.yaml` — file-only fields

| Path | Description |
|------|-------------|
| `react.agent_name` | Agent name, default `main_agent` |
| `react.enable_task_loop` | Enable task loop, default `false` |
| `react.max_iterations` | Max iterations, default 100 |
| `react.model_name` | Model name override for the agent (env: `MODEL_NAME`, default `deepseek-chat`) |
| `react.answer_chunk_size` | Answer streaming chunk size, default 500 |
| `react.stream_chunk_threshold` | Stream chunk threshold, default 50 |
| `react.stream_character_threshold` | Stream character threshold, default 2000 |
| `react.context_engine_config.enable_reload` | Enable context reload |
| `react.context_engine_config.enable_kv_cache_release` | Enable KV cache release |
| `react.context_engine_config.enabled` | Enable context engine, default `true` |
| `react.subagents.code_agent.enabled` | Enable code sub-agent, default `false` |
| `react.subagents.research_agent.enabled` | Enable research sub-agent, default `false` |
| `react.evolution.enabled` | Enable online skill evolution |
| `react.evolution.auto_scan` | Auto-scan skills after each turn |
| `react.evolution.skill_create` | Enable SkillCreateRail (single agent) and TeamSkillCreateRail (leader) |
| `react.evolution.skill_base_dir` | Skill root, default `agent/skills` |
| `react.a2x_registry.base_url` | A2X registry base URL |
| `react.a2x_registry.timeout` | A2X registry timeout, default 30.0 |
| `react.a2x_registry.dataset` | Dataset name, default `team_pool` |
| `react.a2x_registry.role` | Role: `teammate` or `teamleader` |
| `tools` | Enabled tool list, e.g. `[todo, skill]` |
| `browser.remote_debugging_address` | Remote debugging address |
| `browser.remote_debugging_port` | Remote debugging port |
| `browser.user_data_dir` | Chrome user data directory |
| `browser.profile_directory` | Chrome profile directory |
| `browser.chrome_path` | Chrome executable path (string or per-OS map) |

See also:
- [Modes](Modes.md) — `modes` section configuration
- [Tool Permissions & Security](ToolPermissionsSecurity.md) — `permissions` section configuration

### 2.2 `models` section (Multi-Model Configuration)

Configure multiple model profiles for different use cases (main agent, video, audio, vision, image generation).

| Path | Description |
|------|-------------|
| `models.default.model_client_config` | Default (main) model connection: `api_base`, `api_key`, `model_name`, `client_provider`, `timeout`, `verify_ssl`, `custom_headers` |
| `models.default.model_config_obj` | Default model generation params: `temperature`, etc. |
| `models.default.is_default` | Mark this profile as the default, default `true` |
| `models.video.model_client_config` | Video processing model |
| `models.audio.model_client_config` | Audio processing model |
| `models.vision.model_client_config` | Vision / image understanding model |
| `models.image_gen.model_client_config` | Image generation model |
| `models.image_gen.model_config_obj` | Image generation params: `temperature`, etc. |

Each sub-model section supports the same `model_client_config` keys: `api_base`, `api_key`, `model_name`, `client_provider`, `timeout`, `verify_ssl`. Environment variable substitution (e.g. `${VIDEO_API_BASE}`) is used throughout.

### 2.2b `embed` section (Embedding Configuration)

| Path | Description |
|------|-------------|
| `embed.embed_api_key` | Embedding API key |
| `embed.embed_base_url` | Embedding API base URL |
| `embed.embed_model` | Embedding model name |

### 2.3 memory section (Memory System)

| Path | Description |
|------|-------------|
| `memory.mode` | Builtin memory storage mode: `local` \| `cloud` (env: `MEMORY_MODE`, default `local`) |
| `memory.engine` | Engine switch: `builtin` \| `external` \| `both` \| `none` |
| `memory.external.provider` | Provider name: `openjiuwen` \| `mem0` \| `openviking` \| `<plugin>` |
| `memory.external.user_id` | Data isolation identifier |
| `memory.external.scope_id` | Scope identifier |

See [Memory](Memory.md) for details.

### 2.4 team section (Distributed Team)

| Path | Description |
|------|-------------|
| `team.team_name` | Team name, default `jiuwen_team` |
| `team.lifecycle` | Lifecycle mode: `persistent` |
| `team.runtime.mode` | Runtime mode: `local` \| `distributed` |
| `team.runtime.role` | Process role: `leader` \| `teammate` |
| `team.runtime.member_name` | Teammate name identifier |
| `team.teammate_mode` | Teammate build method: `build_mode` |
| `team.spawn_mode` | Teammate process mode: `inprocess` |
| `team.leader.member_name` | Leader member name, default `team_leader` |
| `team.leader.display_name` | Leader display name |
| `team.leader.persona` | Leader persona description |
| `team.agents.leader.workspace.stable_base` | Leader workspace stable base |
| `team.agents.leader.max_iterations` | Leader max iterations, default 200 |
| `team.agents.leader.completion_timeout` | Leader completion timeout, default 600.0 |
| `team.agents.teammate.workspace.stable_base` | Teammate workspace stable base |
| `team.agents.teammate.max_iterations` | Teammate max iterations, default 200 |
| `team.agents.teammate.completion_timeout` | Teammate completion timeout, default 600.0 |
| `team.workspace.enabled` | Enable team workspace, default `true` |
| `team.transport.type` | Transport type: `inprocess` |
| `team.storage.type` | Storage type: `sqlite` |

See [Distributed Team](DistributedTeam.md) for details.

### 2.5 task_memory section (Experience Memory)

| Path | Description |
|------|-------------|
| `task_memory.enabled` | Enable experience memory tools, default `true` |
| `task_memory.llm_model` | LLM model for experience memory (empty = main model) |
| `task_memory.embedding_model` | Embedding model for experience memory |
| `task_memory.api_key` | API key for experience memory (empty = main key) |
| `task_memory.api_base` | API base URL for experience memory (empty = main base) |
| `task_memory.retrieval_algo` | Retrieval algorithm: `ACE` \| `ReasoningBank` \| `ReMe`, default `ACE` |
| `task_memory.summary_algo` | Summary algorithm: `ACE` \| `ReasoningBank` \| `ReMe`, default `ACE` |

When enabled, the agent gains `experience_retrieve`, `experience_learn`, and `experience_clear` tools. Available retrieval algorithms: `ACE`, `ReasoningBank`, `ReMe`. Default: `ACE`.

### 2.6 email_settings section (Email)

| Path | Description |
|------|-------------|
| `email_settings.email_address` | Sender email address |
| `email_settings.token` | Email authorization code |
| `email_settings.smtp_server` | SMTP server, default `smtp.gmail.com` (env: `SMTP_SERVER`) |
| `email_settings.port` | SMTP port, default `587` (env: `EMAIL_PORT`) |

### 2.7 extensions section (Extension Packages)

| Path | Description |
|------|-------------|
| `extensions.extension_dirs` | Extension search directories, semicolon-separated (e.g. `E:/a;D:/b`), maps to env var `EXTENSION_DIRS` |

### 2.8 mcp section (MCP Servers)

| Path | Description |
|------|-------------|
| `mcp.servers` | MCP server list; each entry includes `name`, `enabled`, `transport` (`stdio`/`sse`/`streamable-http`), `command`, `args`, `url`, etc. |

Browser MCP runtime is configured via environment variables (see below).

### 2.9 channels section (Channel Configuration)

Per-channel settings in `config.yaml`. See [Channels](Channels.md) for the full per-channel configuration reference.

Supported channels: `feishu`, `feishu_enterprise`, `xiaoyi`, `dingtalk`, `telegram`, `discord`, `whatsapp`, `wechat`, `wecom`.

### 2.10 updater section (Auto-Update)

| Path | Description |
|------|-------------|
| `updater.enabled` | Enable auto-update, default `true` |
| `updater.repo_owner` | GitHub repository owner |
| `updater.repo_name` | GitHub repository name |
| `updater.release_api_url` | Custom release API URL (empty = use default) |
| `updater.asset_name_pattern` | Release asset name pattern |
| `updater.sha256_name_pattern` | SHA256 checksum file name pattern |
| `updater.timeout_seconds` | Update check timeout, default `20` |

---

### 2.11 gateway section (Gateway Routing)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `gateway.session_map_scope` | string | `per_chat_bot` | SessionMap scope: `per_chat_bot` (shared session per chat+bot) or `per_chat_bot_user` (session per user). Enterprise channels only (e.g. Feishu Enterprise). |

### 2.12 logging section (Logging)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `logging.level` | string | `INFO` | Global log level |
| `logging.console_level` | string | `INFO` | Console log level |
| `logging.gateway` | string | `INFO` | Gateway module log level |
| `logging.channel` | string | `INFO` | Channel module log level |
| `logging.agent_server` | string | `INFO` | Agent server log level |
| `logging.full` | string | `INFO` | Full (aggregate) log level |

Log files are stored in `~/.jiuwenclaw/agent/.logs/`, split into `gateway.log`, `channel.log`, `agent_server.log`; `full.log` is the aggregate.

### 2.13 telemetry section (OpenTelemetry)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `telemetry.enabled` | bool | `false` | Master switch (env var `OTEL_ENABLED` takes priority) |
| `telemetry.exporter` | string | `otlp` | Exporter type: `otlp` / `console` / `none` |
| `telemetry.endpoint` | string | `http://localhost:4317` | OTLP endpoint |
| `telemetry.protocol` | string | `grpc` | Transport protocol: `grpc` / `http` |
| `telemetry.headers` | map | `{}` | Common OTLP headers; can be overridden by traces/metrics |
| `telemetry.log_messages` | bool | `true` | Whether to record full message content in span events |
| `telemetry.service_name` | string | `jiuwenclaw` | Service name identifier |
| `telemetry.provider_factory` | string | | Custom Provider Factory, format `module:function` |
| `telemetry.traces.exporter` | string | | Trace exporter; falls back to `telemetry.exporter` |
| `telemetry.traces.endpoint` | string | | Trace endpoint; falls back to `telemetry.endpoint` |
| `telemetry.traces.protocol` | string | | Trace protocol; falls back to `telemetry.protocol` |
| `telemetry.traces.headers` | map | `{}` | Trace-specific headers |
| `telemetry.metrics.exporter` | string | | Metrics exporter; falls back to `telemetry.exporter` |
| `telemetry.metrics.endpoint` | string | | Metrics endpoint; falls back to `telemetry.endpoint` |
| `telemetry.metrics.protocol` | string | | Metrics protocol; falls back to `telemetry.protocol` |
| `telemetry.metrics.headers` | map | `{}` | Metrics-specific headers |

### 2.14 Other Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preferred_language` | string | `zh` | Agent default reply language (`zh` / `en`) |

### 2.15 Environment-only fields

| Variable | Description |
|----------|-------------|
| `HEARTBEAT_TIMEOUT` | Heartbeat request timeout (seconds) |
| `HEARTBEAT_RELAY_CHANNEL_ID` | Heartbeat relay channel (overrides `target` in config) |
| `HEARTBEAT_INTERVAL` | Heartbeat interval (seconds), overrides `every` in config |
| `BROWSER_RUNTIME_MCP_ENABLED` | Enable browser MCP runtime (`0`/`1`), default `1` |
| `BROWSER_RUNTIME_MCP_CLIENT_TYPE` | MCP client type (`stdio` / `sse` / `streamable-http`), recommended `streamable-http` |
| `BROWSER_RUNTIME_MCP_SERVER_ID` | MCP server identifier, default `playwright_runtime_wrapper` |
| `BROWSER_RUNTIME_MCP_SERVER_NAME` | MCP server display name, default `playwright-runtime-wrapper` |
| `BROWSER_RUNTIME_MCP_SERVER_PATH` | MCP server URL/path (default `http://127.0.0.1:8940/mcp` for streamable-http) |
| `BROWSER_RUNTIME_MCP_TIMEOUT_S` | MCP server request timeout (seconds), default `300` |
| `BROWSER_RUNTIME_MCP_HOST` | MCP server host, default `127.0.0.1` |
| `BROWSER_RUNTIME_MCP_PORT` | MCP server port, default `8940` |
| `BROWSER_RUNTIME_MCP_PATH` | MCP server path, default `/mcp` |
| `BROWSER_RUNTIME_MCP_COMMAND` | MCP server command (stdio mode, empty = use current python executable) |
| `BROWSER_RUNTIME_MCP_ARGS` | MCP server args (stdio mode, empty = use built-in default) |
| `BROWSER_RUNTIME_MCP_AUTO_SSE_FALLBACK` | Auto SSE fallback in stdio mode (`0`/`1`), default `1` |
| `PLAYWRIGHT_CDP_URL` | Playwright CDP URL for Chrome, default `http://127.0.0.1:9222` |
| `PLAYWRIGHT_TOOL_TIMEOUT_S` | Playwright tool timeout (seconds), default `300` |
| `BROWSER_TIMEOUT_S` | Browser task timeout (seconds), default `300` |
| `BROWSER_DRIVER` | Browser driver mode: `managed` / `remote` / `extension` |
| `BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE` | Allow model-provided timeout shorter than `BROWSER_TIMEOUT_S`; default `0` (off) |
| `BROWSER_PROFILE_NAME` | Browser profile name, default `Default` |
| `PLAYWRIGHT_MCP_COMMAND` | Playwright MCP command, default `npx` |
| `PLAYWRIGHT_MCP_ARGS` | Playwright MCP args, default `-y @playwright/mcp@latest` |
| `CUSTOM_HEADERS` | Custom HTTP headers for model API calls (JSON or empty) |
| `VIDEO_API_BASE` | Video model API base URL |
| `VIDEO_API_KEY` | Video model API key |
| `VIDEO_MODEL_NAME` | Video model name |
| `VIDEO_PROVIDER` | Video model provider |
| `AUDIO_API_BASE` | Audio model API base URL |
| `AUDIO_API_KEY` | Audio model API key |
| `AUDIO_MODEL_NAME` | Audio model name, default `openai/gpt-4o-audio-preview` |
| `AUDIO_PROVIDER` | Audio model provider |
| `VISION_API_BASE` | Vision model API base URL |
| `VISION_API_KEY` | Vision model API key |
| `VISION_MODEL_NAME` | Vision model name, default `bytedance-seed/seed-2.0-mini` |
| `VISION_PROVIDER` | Vision model provider |
| `IMAGE_GEN_API_BASE` | Image generation model API base URL, default `https://dashscope.aliyuncs.com/api/v1` |
| `IMAGE_GEN_API_KEY` | Image generation model API key |
| `IMAGE_GEN_MODEL_NAME` | Image generation model name, default `qwen-image-max` |
| `IMAGE_GEN_PROVIDER` | Image generation model provider, default `DashScope` |
| `JINA_API_KEY` | Jina search API key |
| `SERPER_API_KEY` | Serper search API key |
| `PERPLEXITY_API_KEY` | Perplexity API key |
| `GITHUB_TOKEN` | GitHub Personal Access Token |
| `FREE_SEARCH_PROXY_URL` | Optional HTTP/HTTPS proxy for free search |
| `FREE_SEARCH_DDG_URL` | DuckDuckGo HTML endpoint URL, default `https://html.duckduckgo.com/html/` |
| `FREE_SEARCH_SSL_VERIFY` | Enable SSL verification for free search; default `true`, set `false` behind corporate proxies |
| `NO_PROXY` | Comma-separated hosts to bypass proxy |
| `EMAIL_ADDRESS` | Sender email address (maps to `email_settings.email_address`) |
| `EMAIL_TOKEN` | Email authorization code (maps to `email_settings.token`) |
| `SMTP_SERVER` | SMTP server (maps to `email_settings.smtp_server`) |
| `EMAIL_PORT` | SMTP port (maps to `email_settings.port`) |
| `EVOLUTION_AUTO_SCAN` | Auto-scan evolvable skills after each turn (`true`/`false`) |
| `SKILLNET_DOWNLOAD_TIMEOUT` | SkillNet download timeout (seconds), default 60 |
| `SKILLNET_MAX_RETRIES` | SkillNet download max retries, default 3 |
| `TEAM_SKILLS_HUB_BASE_URL` | TeamSkillsHub market URL (empty = default `https://teamskills.openjiuwen.com`) |
| `TEAM_SKILLS_HUB_USER_TOKEN` | TeamSkillsHub user token (mutually exclusive with system token) |
| `TEAM_SKILLS_HUB_SYSTEM_TOKEN` | TeamSkillsHub system token (mutually exclusive with user token) |
| `TEAM_SKILLS_HUB_TIMEOUT` | TeamSkillsHub request timeout (seconds), default 60 |
| `TEAM_SKILLS_HUB_ALLOWED_DOWNLOAD_HOSTS` | TeamSkillsHub ZIP download host allowlist, comma-separated |
| `MEMORY_MODE` | Memory mode (empty = `local` default) |
| `MEMORY_ENGINE` | Memory engine: `builtin` \| `external` \| `both` \| `none` |
| `MEMORY_EXTERNAL_PROVIDER` | External memory provider: `openjiuwen` \| `mem0` \| `openviking` \| `<plugin>` |
| `MEM0_API_KEY` | Mem0 API key |
| `MEM0_USER_ID` | Mem0 user ID, default `jiuwenclaw-user` |
| `MEM0_AGENT_ID` | Mem0 agent ID, default `jiuwenclaw` |
| `OPENVIKING_ENDPOINT` | OpenViking endpoint, default `http://127.0.0.1:1933` |
| `OPENVIKING_API_KEY` | OpenViking API key |
| `OPENVIKING_ACCOUNT` | OpenViking account, default `root` |
| `OPENVIKING_USER` | OpenViking user, default `default` |
| `EXTENSION_DIRS` | Extension search directories, semicolon-separated (maps to `extensions.extension_dirs`) |
| `OTEL_ENABLED` | Enable OpenTelemetry (`true`/`false`), takes priority over `telemetry.enabled` |
| `OTEL_EXPORTER` | OTLP exporter type (maps to `telemetry.exporter`) |
| `OTEL_ENDPOINT` | OTLP endpoint (maps to `telemetry.endpoint`) |
| `JIUWENCLAW_CONFIG_DIR` | Custom config directory path |
| `JIUWENCLAW_DATA_DIR` | Absolute path to the user data root (`config/`, `agent/`, `.logs`, etc.). If unset, defaults to `~/.jiuwenclaw`. Set in the shell or service environment **before** starting the process so workspace paths resolve from the first import; defining it only in `config/.env` is often too late for that bootstrap. |
| `JIUWENCLAW_DISABLE_CRON_TOOLS` | Set to `1` to disable Agent-side cron tool registration and hide cron-tool prompt text |

See `.env.template` for more variables.

---

### 2.16 Precedence

- **Environment variables** override **`config.yaml`**
- Example: `react.model_name: ${MODEL_NAME:-deepseek-chat}` reads `MODEL_NAME` first, then falls back to `deepseek-chat`.
- Values saved from the Config panel go to `.env` and take effect on next start.
