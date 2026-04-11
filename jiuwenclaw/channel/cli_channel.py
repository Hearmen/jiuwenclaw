# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import inspect
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.foundation.llm import ProviderType

from jiuwenclaw.config import (
    get_config,
    get_config_raw,
    update_context_engine_enabled_in_config,
    update_permissions_enabled_in_config,
)
from jiuwenclaw.gateway.route_binding import GatewayRouteBinding
from jiuwenclaw.version import __version__

logger = logging.getLogger(__name__)

# ── 需要转发到 Agent 的方法集合 ──────────────────────────────

CLI_FORWARD_REQ_METHODS = frozenset(
    {
        "command.add_dir",
        "command.chrome",
        "command.compact",
        "command.diff",
        "command.model",
        "command.resume",
        "command.session",
        "chat.send",
        "chat.interrupt",
        "chat.resume",
        "chat.user_answer",
        "history.get",
        "browser.start",
        "skills.marketplace.list",
        "skills.list",
        "skills.installed",
        "skills.get",
        "skills.install",
        "skills.import_local",
        "skills.marketplace.add",
        "skills.marketplace.remove",
        "skills.marketplace.toggle",
        "skills.uninstall",
        "skills.skillnet.search",
        "skills.skillnet.install",
        "skills.skillnet.install_status",
        "skills.skillnet.evaluate",
        "skills.clawhub.get_token",
        "skills.clawhub.set_token",
        "skills.clawhub.search",
        "skills.clawhub.download",
        "skills.evolution.status",
        "skills.evolution.get",
        "skills.evolution.save",
        "extensions.list",
        "extensions.import",
        "extensions.delete",
        "extensions.toggle",
    }
)

CLI_FORWARD_NO_LOCAL_HANDLER_METHODS = frozenset(
    {
        "command.add_dir",
        "command.chrome",
        "command.compact",
        "command.diff",
        "command.model",
        "command.resume",
        "command.session",
        "browser.start",
        "skills.marketplace.list",
        "skills.list",
        "skills.installed",
        "skills.get",
        "skills.install",
        "skills.import_local",
        "skills.marketplace.add",
        "skills.marketplace.remove",
        "skills.marketplace.toggle",
        "skills.uninstall",
        "skills.skillnet.search",
        "skills.skillnet.install",
        "skills.skillnet.install_status",
        "skills.skillnet.evaluate",
        "skills.clawhub.get_token",
        "skills.clawhub.set_token",
        "skills.clawhub.search",
        "skills.clawhub.download",
        "skills.evolution.status",
        "skills.evolution.get",
        "skills.evolution.save",
        "extensions.list",
        "extensions.import",
        "extensions.delete",
        "extensions.toggle",
    }
)


@dataclass
class CliHandlersBindParams:
    channel: Any  # GatewayServer instance
    agent_client: Any = None
    message_handler: Any = None
    on_config_saved: Any = None
    path: str = "/cli"


@dataclass
class CliRouteBindParams:
    agent_client: Any = None
    message_handler: Any = None
    on_config_saved: Any = None
    path: str = "/cli"
    channel_id: str = "cli"


_CLI_CONFIG_SET_ENV_MAP = {
    "model_provider": "MODEL_PROVIDER",
    "model": "MODEL_NAME",
    "api_base": "API_BASE",
    "api_key": "API_KEY",
    "video_api_base": "VIDEO_API_BASE",
    "video_api_key": "VIDEO_API_KEY",
    "video_model": "VIDEO_MODEL_NAME",
    "video_provider": "VIDEO_PROVIDER",
    "audio_api_base": "AUDIO_API_BASE",
    "audio_api_key": "AUDIO_API_KEY",
    "audio_model": "AUDIO_MODEL_NAME",
    "audio_provider": "AUDIO_PROVIDER",
    "vision_api_base": "VISION_API_BASE",
    "vision_api_key": "VISION_API_KEY",
    "vision_model": "VISION_MODEL_NAME",
    "vision_provider": "VISION_PROVIDER",
    "email_address": "EMAIL_ADDRESS",
    "email_token": "EMAIL_TOKEN",
    "embed_api_key": "EMBED_API_KEY",
    "embed_api_base": "EMBED_API_BASE",
    "embed_model": "EMBED_MODEL",
    "jina_api_key": "JINA_API_KEY",
    "serper_api_key": "SERPER_API_KEY",
    "perplexity_api_key": "PERPLEXITY_API_KEY",
    "github_token": "GITHUB_TOKEN",
    "evolution_auto_scan": "EVOLUTION_AUTO_SCAN",
}

_CLI_CONFIG_YAML_KEYS = frozenset({"context_engine_enabled", "permissions_enabled"})



async def _clear_agent_config_cache(agent_client=None) -> None:
    try:
        if agent_client is not None:
            from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
            from jiuwenclaw.schema.message import ReqMethod
            import uuid

            env = e2a_from_agent_fields(
                request_id=f"cfg-cache-clear-{uuid.uuid4().hex[:8]}",
                channel_id="",
                req_method=ReqMethod.CONFIG_CACHE_CLEAR,
            )
            await agent_client.send_request(env)
        else:
            get_config()
    except Exception as e:  # noqa: BLE001
        logger.debug("[cli config.set] clear agent config cache skipped: %s", e)


def _persist_env_updates(updates: dict[str, str]) -> None:
    from jiuwenclaw.utils import get_env_file

    env_path = get_env_file()
    if not updates:
        return
    try:
        lines: list[str] = []
        if env_path.is_file():
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            found = False
            for env_key, value in updates.items():
                if stripped.startswith(env_key + "="):
                    new_lines.append(
                        f'{env_key}="{value}"\n' if value else f"{env_key}=\n"
                    )
                    found = True
                    break
            if not found:
                new_lines.append(line)
        for env_key, value in updates.items():
            if not any(s.strip().startswith(env_key + "=") for s in new_lines):
                new_lines.append(f'{env_key}="{value}"\n' if value else f"{env_key}=\n")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except OSError as e:
        logger.warning("[cli config.set] 写回 .env 失败: %s", e)


def register_cli_handlers(bind: CliHandlersBindParams) -> None:
    channel = bind.channel
    agent_client = bind.agent_client
    on_config_saved = bind.on_config_saved
    path = bind.path

    async def _config_get(ws, req_id, params, session_id):
        payload = {
            param_key: (os.getenv(env_key) or "")
            for param_key, env_key in _CLI_CONFIG_SET_ENV_MAP.items()
        }
        payload["app_version"] = __version__
        try:
            raw = get_config_raw()
            for key, val in payload.items():
                from jiuwenclaw.extensions import ExtensionRegistry

                crypto_provider = ExtensionRegistry.get_instance().get_crypto_provider()
                if (
                    "api_key" in key.lower() or "token" in key.lower()
                ) and crypto_provider:
                    payload[key] = crypto_provider.decrypt(val)
            ctx_cfg = (raw.get("react") or {}).get("context_engine_config") or {}
            payload["context_engine_enabled"] = (
                "true" if ctx_cfg.get("enabled", False) else "false"
            )
            perm_cfg = raw.get("permissions") or {}
            payload["permissions_enabled"] = (
                "true" if perm_cfg.get("enabled", False) else "false"
            )
        except Exception:
            payload.setdefault("context_engine_enabled", "false")
            payload.setdefault("permissions_enabled", "false")
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _config_set(ws, req_id, params, session_id):
        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        for key, val in params.items():
            from jiuwenclaw.extensions import ExtensionRegistry

            crypto_provider = ExtensionRegistry.get_instance().get_crypto_provider()
            if ("api_key" in key.lower() or "token" in key.lower()) and crypto_provider:
                params[key] = crypto_provider.encrypt(val)

        env_updates: dict[str, str] = {}
        yaml_updated: list[str] = []
        available_model_providers = [provider.value for provider in ProviderType]

        for param_key, env_key in _CLI_CONFIG_SET_ENV_MAP.items():
            if param_key not in params:
                continue
            val = params[param_key]
            if (
                param_key.endswith("_provider")
                and val
                and val not in available_model_providers
            ):
                await channel.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=f"Model provider must in: {available_model_providers} ",
                    code="BAD_REQUEST",
                )
                return
            env_updates[env_key] = "" if val is None else str(val).strip()

        for param_key in _CLI_CONFIG_YAML_KEYS:
            if param_key not in params:
                continue
            parsed = str(params[param_key]).strip().lower() in ("true", "1", "yes")
            try:
                if param_key == "context_engine_enabled":
                    update_context_engine_enabled_in_config(parsed)
                elif param_key == "permissions_enabled":
                    update_permissions_enabled_in_config(parsed)
                yaml_updated.append(param_key)
            except Exception as e:
                logger.warning(
                    "[cli config.set] 写回 config.yaml 失败 %s: %s", param_key, e
                )

        for env_key, value in env_updates.items():
            os.environ[env_key] = value
        applied_without_restart = True

        if env_updates:
            _persist_env_updates(env_updates)
        if yaml_updated:
            real_client = agent_client.get("value") if isinstance(agent_client, dict) else agent_client
            await _clear_agent_config_cache(real_client)

        if env_updates or yaml_updated:
            if on_config_saved:
                config_payload = get_config()
                callback_result = on_config_saved(
                    set(env_updates.keys()) | set(yaml_updated),
                    env_updates=dict(env_updates),
                    config_payload=config_payload,
                )
                if inspect.isawaitable(callback_result):
                    callback_result = await callback_result
                applied_without_restart = bool(callback_result)

        updated_param_keys = [
            k for k, e in _CLI_CONFIG_SET_ENV_MAP.items() if e in env_updates
        ] + yaml_updated
        await channel.send_response(
            ws,
            req_id,
            ok=True,
            payload={
                "updated": updated_param_keys,
                "applied_without_restart": applied_without_restart,
            },
        )

    async def _session_list(ws, req_id, params, session_id):
        from jiuwenclaw.utils import get_agent_sessions_dir

        limit = 20
        if isinstance(params, dict):
            raw_limit = params.get("limit")
            if isinstance(raw_limit, int):
                limit = raw_limit
            elif isinstance(raw_limit, str) and raw_limit.strip().isdigit():
                limit = int(raw_limit.strip())
        limit = max(1, min(limit, 200))

        workspace_session_dir = get_agent_sessions_dir()
        if not workspace_session_dir.exists() or not workspace_session_dir.is_dir():
            sessions = []
        else:
            sessions = sorted(
                [d.name for d in workspace_session_dir.iterdir() if d.is_dir()],
                reverse=True,
            )[:limit]
        await channel.send_response(ws, req_id, ok=True, payload={"sessions": sessions})

    async def _session_create(ws, req_id, params, session_id):
        from jiuwenclaw.utils import get_agent_sessions_dir

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target = str(params.get("session_id") or "").strip()
        if not target:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        workspace_session_dir = get_agent_sessions_dir()
        workspace_session_dir.mkdir(parents=True, exist_ok=True)
        session_dir = workspace_session_dir / target
        if session_dir.exists():
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="session already exists",
                code="ALREADY_EXISTS",
            )
            return
        session_dir.mkdir()
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": target})

    async def _session_delete(ws, req_id, params, session_id):
        from jiuwenclaw.utils import get_agent_sessions_dir

        if not isinstance(params, dict):
            await channel.send_response(
                ws, req_id, ok=False, error="params must be object", code="BAD_REQUEST"
            )
            return
        target = str(params.get("session_id") or "").strip()
        if not target:
            await channel.send_response(
                ws, req_id, ok=False, error="session_id is required", code="BAD_REQUEST"
            )
            return
        session_dir = get_agent_sessions_dir() / target
        if not session_dir.exists():
            await channel.send_response(
                ws, req_id, ok=False, error="session not found", code="NOT_FOUND"
            )
            return
        if not session_dir.is_dir():
            await channel.send_response(
                ws,
                req_id,
                ok=False,
                error="session is not a directory",
                code="BAD_REQUEST",
            )
            return
        shutil.rmtree(session_dir)
        await channel.send_response(ws, req_id, ok=True, payload={"session_id": target})

    async def _chat_send(ws, req_id, params, session_id):
        await channel.send_response(
            ws, req_id, ok=True, payload={"accepted": True, "session_id": session_id}
        )

    async def _chat_resume(ws, req_id, params, session_id):
        await channel.send_response(
            ws, req_id, ok=True, payload={"accepted": True, "session_id": session_id}
        )

    async def _chat_interrupt(ws, req_id, params, session_id):
        intent = params.get("intent") if isinstance(params, dict) else None
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(intent, str) and intent:
            payload["intent"] = intent
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _chat_user_answer(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        request_id = params.get("request_id") if isinstance(params, dict) else None
        if isinstance(request_id, str) and request_id:
            payload["request_id"] = request_id
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    async def _history_get(ws, req_id, params, session_id):
        payload = {"accepted": True, "session_id": session_id}
        if isinstance(params, dict):
            if "session_id" in params:
                payload["session_id"] = params.get("session_id")
            if "page_idx" in params:
                payload["page_idx"] = params.get("page_idx")
        await channel.send_response(ws, req_id, ok=True, payload=payload)

    channel.register_local_handler(path, "config.get", _config_get)
    channel.register_local_handler(path, "config.set", _config_set)
    channel.register_local_handler(path, "session.list", _session_list)
    channel.register_local_handler(path, "session.create", _session_create)
    channel.register_local_handler(path, "session.delete", _session_delete)
    channel.register_local_handler(path, "chat.send", _chat_send)
    channel.register_local_handler(path, "chat.resume", _chat_resume)
    channel.register_local_handler(path, "chat.interrupt", _chat_interrupt)
    channel.register_local_handler(path, "chat.user_answer", _chat_user_answer)
    channel.register_local_handler(path, "history.get", _history_get)


def build_cli_route_binding(bind: CliRouteBindParams) -> GatewayRouteBinding:
    def _install(channel: Any) -> None:
        register_cli_handlers(
            CliHandlersBindParams(
                channel=channel,
                agent_client=bind.agent_client,
                message_handler=bind.message_handler,
                on_config_saved=bind.on_config_saved,
                path=bind.path,
            )
        )

    return GatewayRouteBinding(
        path=bind.path,
        channel_id=bind.channel_id,
        forward_methods=CLI_FORWARD_REQ_METHODS,
        forward_no_local_handler_methods=CLI_FORWARD_NO_LOCAL_HANDLER_METHODS,
        install=_install,
    )
