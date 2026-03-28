# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Standalone Gateway entrypoint (split deployment).

This process starts:
- Gateway MessageHandler + ChannelManager
- WebChannel websocket server (browser inbound)
- Heartbeat service
- Cron scheduler service (triggers remote AgentServer via ws)

It connects to a remote/local AgentServer WebSocket endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import time

from dotenv import load_dotenv
from openjiuwen.core.common.logging import LogManager

from jiuwenclaw.utils import USER_WORKSPACE_DIR, get_env_file, prepare_workspace, logger

from jiuwenclaw.app import (
    _CONFIG_SET_ENV_MAP,
    _DummyBus,
    _FORWARD_NO_LOCAL_HANDLER_METHODS,
    _FORWARD_REQ_METHODS,
    _register_web_handlers,
)
from jiuwenclaw.channel import (
    DingTalkChannel,
    DingTalkConfig,
    WhatsAppChannel,
    WhatsAppChannelConfig,
    WechatChannel,
    WechatConfig,
)

# Ensure workspace initialized
_config_file = USER_WORKSPACE_DIR / "config" / "config.yaml"
if not _config_file.exists():
    prepare_workspace(overwrite=False)

# Reduce openjiuwen internal logs (keep Gateway logs)
for _lg in LogManager.get_all_loggers().values():
    _lg.set_level(logging.CRITICAL)

# Load env from user workspace config/.env
load_dotenv(dotenv_path=get_env_file())


# ---- Reuse app.py helpers/constants for WebChannel methods ----
def _make_session_id() -> str:
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"sess_{ts}_{suffix}"


async def _connect_with_retry(
    client,
    uri: str,
    *,
    max_retries: int = 20,
    interval: float = 3.0,
) -> None:
    for attempt in range(1, max_retries + 1):
        try:
            await client.connect(uri)
            logger.info("[Gateway] connected to AgentServer: %s", uri)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                logger.error(
                    "[Gateway] connect AgentServer failed after %d tries: %s  last=%s",
                    attempt,
                    uri,
                    exc,
                )
                raise
            logger.warning(
                "[Gateway] connect AgentServer failed (%d/%d): %s  retry in %s s…",
                attempt,
                max_retries,
                exc,
                interval,
            )
            await asyncio.sleep(interval)


async def _run(agent_server_url: str, web_host: str, web_port: int, web_path: str) -> None:
    from openjiuwen.core.runner import Runner
    from jiuwenclaw.channel.discord_channel import DiscordChannel, DiscordChannelConfig
    from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig
    from jiuwenclaw.channel.telegram_channel import TelegramChannel, TelegramChannelConfig
    from jiuwenclaw.channel.web_channel import WebChannel, WebChannelConfig
    from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig
    from jiuwenclaw.gateway import (
        GatewayHeartbeatService,
        HeartbeatConfig,
        WebSocketAgentServerClient,
    )
    from jiuwenclaw.gateway.channel_manager import ChannelManager
    from jiuwenclaw.gateway.cron import CronController, CronJobStore, CronSchedulerService
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.extensions import ExtensionManager, ExtensionRegistry
    from jiuwenclaw.schema.message import Message, ReqMethod
    from jiuwenclaw.agentserver.memory.config import _load_config as _load_agent_config
    from jiuwenclaw.agentserver.tools.browser_tools import restart_local_browser_runtime_server

    logger.info("[Gateway] starting, connecting AgentServer: %s", agent_server_url)

    # ---------- 扩展系统初始化 ----------
    callback_framework = Runner.callback_framework
    extension_registry = ExtensionRegistry.create_instance(
        callback_framework=callback_framework,
        config={},
        logger=logger,
    )
    extension_manager = ExtensionManager(
        registry=extension_registry,
    )
    await extension_manager.load_all_extensions()
    logger.info("[Gateway] 扩展加载完成，共 %d 个", len(extension_manager.list_extensions()))

    max_retries = int(os.getenv("AGENT_CONNECT_RETRY", "20"))
    retry_interval = float(os.getenv("AGENT_CONNECT_RETRY_INTERVAL", "3"))

    agent_server_ext = extension_registry.get_agent_server_client_extension()
    if agent_server_ext is not None:
        logger.info("[Gateway] 使用扩展提供的 AgentServerClient: %s", agent_server_ext.metadata.name)
        client = agent_server_ext.get_client()
    else:
        client = WebSocketAgentServerClient(ping_interval=20.0, ping_timeout=20.0)
    await _connect_with_retry(
        client,
        agent_server_url,
        max_retries=max_retries,
        interval=retry_interval,
    )

    message_handler = MessageHandler(client)
    await message_handler.start_forwarding()

    cron_store = CronJobStore()
    cron_scheduler = CronSchedulerService(
        store=cron_store,
        agent_client=client,
        message_handler=message_handler,
    )
    cron_controller = CronController.get_instance(store=cron_store, scheduler=cron_scheduler)

    heartbeat_cfg: dict | None = None
    channels_cfg: dict | None = None
    try:
        full_cfg = _load_agent_config()
        heartbeat_cfg = full_cfg.get("heartbeat") if isinstance(full_cfg, dict) else None
        channels_cfg = full_cfg.get("channels") if isinstance(full_cfg, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Gateway] failed to read config.yaml heartbeat/channels, fallback to defaults: %s", exc)

    if isinstance(heartbeat_cfg, dict):
        cfg_every = heartbeat_cfg.get("every")
        cfg_target = heartbeat_cfg.get("target")
        cfg_active_hours = heartbeat_cfg.get("active_hours")
    else:
        cfg_every = None
        cfg_target = None
        cfg_active_hours = None

    heartbeat_interval = float(
        os.getenv("HEARTBEAT_INTERVAL")
        or (str(cfg_every) if cfg_every is not None else "60")
    )
    heartbeat_timeout = float(os.getenv("HEARTBEAT_TIMEOUT", "30")) if os.getenv("HEARTBEAT_TIMEOUT") else None
    heartbeat_relay_channel = os.getenv("HEARTBEAT_RELAY_CHANNEL_ID") or (
        str(cfg_target) if cfg_target is not None else "web"
    )

    heartbeat_config = HeartbeatConfig(
        interval_seconds=heartbeat_interval,
        timeout_seconds=heartbeat_timeout,
        relay_channel_id=heartbeat_relay_channel,
        active_hours=cfg_active_hours if isinstance(cfg_active_hours, dict) else None,
    )
    heartbeat_service = GatewayHeartbeatService(
        client,
        heartbeat_config,
        message_handler=message_handler,
    )
    await heartbeat_service.start()

    initial_channels_conf: dict = channels_cfg if isinstance(channels_cfg, dict) else {}
    channel_manager = ChannelManager(message_handler, config=initial_channels_conf)

    def _on_config_saved(updated_env_keys: set[str] | None = None) -> bool:
        try:
            browser_runtime_keys = {"MODEL_PROVIDER", "MODEL_NAME", "API_BASE", "API_KEY"}
            if updated_env_keys and (browser_runtime_keys & set(updated_env_keys)):
                restart_local_browser_runtime_server()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Gateway] config hot reload failed; scheduling restart: %s", exc)
            _schedule_restart()
            return False

    def _do_restart() -> None:
        import sys

        logger.info("[Gateway] restarting process…")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def _schedule_restart() -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(2.0, _do_restart)
        except RuntimeError:
            _do_restart()

    web_config = WebChannelConfig(enabled=True, host=web_host, port=web_port, path=web_path)
    web_channel = WebChannel(web_config, _DummyBus())
    _register_web_handlers(
        web_channel,
        agent_client=client,
        message_handler=message_handler,
        channel_manager=channel_manager,
        on_config_saved=_on_config_saved,
        heartbeat_service=heartbeat_service,
        cron_controller=cron_controller,
    )

    def _norm_and_forward(msg: Message) -> bool:
        method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""
        if method_val not in _FORWARD_REQ_METHODS:
            return False
        is_stream = bool(msg.is_stream or method_val == ReqMethod.CHAT_SEND.value)
        params = dict(msg.params or {})
        if "query" not in params and "content" in params:
            params["query"] = params["content"]
        normalized = Message(
            id=msg.id,
            type=msg.type,
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params=params,
            timestamp=msg.timestamp,
            ok=msg.ok,
            req_method=getattr(msg, "req_method", None) or ReqMethod.CHAT_SEND,
            mode=msg.mode,
            is_stream=is_stream,
            stream_seq=msg.stream_seq,
            stream_id=msg.stream_id,
            metadata=msg.metadata,
        )
        message_handler.handle_message(normalized)
        if method_val in _FORWARD_NO_LOCAL_HANDLER_METHODS:
            return True
        return False

    # register_channel 会设置默认 on_message；WebChannel.on_message 会整体替换回调，故先注册再挂上自定义转发
    channel_manager.register_channel(web_channel)
    web_channel.on_message(_norm_and_forward)

    feishu_channel = None
    feishu_task = None
    feishu_enterprise_channels: dict[str, FeishuChannel] = {}
    feishu_enterprise_tasks: dict[str, asyncio.Task] = {}
    xiaoyi_channel = None
    xiaoyi_task = None
    dingtalk_channel = None
    dingtalk_task = None
    telegram_channel = None
    telegram_task = None
    discord_channel = None
    discord_task = None
    whatsapp_channel = None
    whatsapp_task = None
    wechat_channel = None
    wechat_task = None

    _last_channels_conf: dict = {}

    def _should_restart_channel(channel_name: str, old_conf: dict, new_conf: dict) -> bool:
        old_channel_conf = old_conf.get(channel_name) if isinstance(old_conf, dict) else None
        new_channel_conf = new_conf.get(channel_name) if isinstance(new_conf, dict) else None
        if (old_channel_conf is None) != (new_channel_conf is None):
            return True
        if old_channel_conf is None:
            return False
        return old_channel_conf != new_channel_conf

    async def _stop_channel(channel, task, channel_name: str, background_wait: bool = False) -> None:
        if task is not None:
            task.cancel()
            if background_wait:
                async def _wait_cancel():
                    try:
                        await task
                    except (TypeError, asyncio.CancelledError):
                        logger.info("[Gateway] cancelled %s channel task", channel_name)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("[Gateway] ignore exception while cancelling %s: %s", channel_name, exc)

                asyncio.create_task(_wait_cancel(), name=f"wait_{channel_name}_cancel")
            else:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[Gateway] ignore exception while waiting %s stop: %s", channel_name, exc)

        if channel is not None:
            try:
                await asyncio.wait_for(channel.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
            except Exception:  # noqa: BLE001
                pass
            channel_manager.unregister_channel(channel.channel_id)

    def _is_channel_enabled(conf: dict | None, required_fields: list[str]) -> tuple[bool, str]:
        if conf is None:
            return False, "not configured"
        enabled_raw = conf.get("enabled", None)
        if enabled_raw is None:
            all_fields_present = all(conf.get(f) for f in required_fields)
            return all_fields_present, "missing required fields" if not all_fields_present else ""
        return bool(enabled_raw), "enabled=false" if not enabled_raw else ""

    async def _apply_channel_config(conf: dict) -> None:
        nonlocal feishu_channel, feishu_task, xiaoyi_channel, xiaoyi_task
        nonlocal dingtalk_channel, dingtalk_task, telegram_channel, telegram_task
        nonlocal discord_channel, discord_task
        nonlocal whatsapp_channel, whatsapp_task
        nonlocal wechat_channel, wechat_task
        nonlocal _last_channels_conf
        nonlocal feishu_enterprise_channels, feishu_enterprise_tasks

        changed = [
            c
            for c in ["feishu", "feishu_enterprise", "xiaoyi", "dingtalk", "telegram", "discord", "whatsapp", "wechat"]
            if _should_restart_channel(c, _last_channels_conf, conf)
        ]
        _last_channels_conf = dict(conf or {})

        if "feishu" in changed:
            feishu_conf = conf.get("feishu") if isinstance(conf, dict) else None
            await _stop_channel(feishu_channel, feishu_task, "feishu")
            feishu_channel, feishu_task = None, None
            if isinstance(feishu_conf, dict):
                enabled, _ = _is_channel_enabled(feishu_conf, ["app_id", "app_secret"])
                if enabled:
                    feishu_config = FeishuConfig(
                        enabled=True,
                        app_id=str(feishu_conf.get("app_id") or "").strip(),
                        app_secret=str(feishu_conf.get("app_secret") or "").strip(),
                        encrypt_key=str(feishu_conf.get("encrypt_key") or "").strip(),
                        verification_token=str(feishu_conf.get("verification_token") or "").strip(),
                        allow_from=feishu_conf.get("allow_from") or [],
                        enable_streaming=bool(feishu_conf.get("enable_streaming", True)),
                        chat_id=str(feishu_conf.get("chat_id") or "").strip(),
                    )
                    feishu_channel = FeishuChannel(feishu_config, _DummyBus())
                    channel_manager.register_channel(feishu_channel)
                    feishu_task = asyncio.create_task(feishu_channel.start(), name="feishu")

        if "feishu_enterprise" in changed:
            for bot_key, task in list(feishu_enterprise_tasks.items()):
                await _stop_channel(
                    feishu_enterprise_channels.get(bot_key),
                    task,
                    f"feishu_enterprise[{bot_key}]",
                )
            feishu_enterprise_channels = {}
            feishu_enterprise_tasks = {}

            enterprise_conf = conf.get("feishu_enterprise") if isinstance(conf, dict) else None
            if not isinstance(enterprise_conf, dict):
                logger.info("[Gateway] channels.feishu_enterprise 未配置或格式错误，FeishuEnterpriseChannel 不启用")
            else:
                for bot_key, bot_conf_raw in enterprise_conf.items():
                    if not isinstance(bot_key, str) or not bot_key.strip():
                        continue
                    bot_conf = bot_conf_raw if isinstance(bot_conf_raw, dict) else None
                    if bot_conf is None:
                        logger.info("[Gateway] channels.feishu_enterprise.%s 配置格式错误，跳过", bot_key)
                        continue
                    enabled, reason = _is_channel_enabled(bot_conf, ["app_id", "app_secret"])
                    if not enabled:
                        logger.info(
                            "[Gateway] channels.feishu_enterprise.%s.%s，FeishuEnterpriseChannel 未启用",
                            bot_key,
                            reason,
                        )
                        continue

                    bot_key = bot_key.strip()
                    app_id = str(bot_conf.get("app_id") or "").strip()
                    channel_id = f"feishu_enterprise:{app_id}"
                    feishu_config = FeishuConfig(
                        enabled=True,
                        app_id=app_id,
                        app_secret=str(bot_conf.get("app_secret") or "").strip(),
                        encrypt_key=str(bot_conf.get("encrypt_key") or "").strip(),
                        verification_token=str(bot_conf.get("verification_token") or "").strip(),
                        allow_from=bot_conf.get("allow_from") or [],
                        enable_streaming=bool(bot_conf.get("enable_streaming", True)),
                        chat_id=str(bot_conf.get("chat_id") or "").strip(),
                        channel_id=channel_id,
                        bot_key=bot_key,
                    )
                    channel = FeishuChannel(feishu_config, _DummyBus())
                    channel_manager.register_channel(channel)
                    task = asyncio.create_task(channel.start(), name=f"feishu-enterprise-{bot_key}")
                    feishu_enterprise_channels[bot_key] = channel
                    feishu_enterprise_tasks[bot_key] = task
                    logger.info(
                        "[Gateway] 已按 config.yaml.channels.feishu_enterprise.%s 注册 FeishuChannel(%s)",
                        bot_key,
                        channel_id,
                    )

        if "xiaoyi" in changed:
            xiaoyi_conf = conf.get("xiaoyi") if isinstance(conf, dict) else None
            await _stop_channel(xiaoyi_channel, xiaoyi_task, "xiaoyi")
            xiaoyi_channel, xiaoyi_task = None, None
            if isinstance(xiaoyi_conf, dict):
                enabled, _ = _is_channel_enabled(xiaoyi_conf, ["ak", "sk", "agent_id"])
                if enabled:
                    xiaoyi_config = XiaoyiChannelConfig(
                        enabled=True,
                        mode=str(xiaoyi_conf.get("mode") or "xiaoyi_claw").strip(),
                        ak=str(xiaoyi_conf.get("ak") or "").strip(),
                        sk=str(xiaoyi_conf.get("sk") or "").strip(),
                        api_id=str(xiaoyi_conf.get("api_id") or "").strip(),
                        push_id=str(xiaoyi_conf.get("push_id") or "").strip(),
                        push_url=str(xiaoyi_conf.get("push_url") or "").strip(),
                        agent_id=str(xiaoyi_conf.get("agent_id") or "").strip(),
                        uid=str(xiaoyi_conf.get("uid") or "").strip(),
                        api_key=str(xiaoyi_conf.get("api_key") or "").strip(),
                        file_upload_url=str(xiaoyi_conf.get("file_upload_url") or "").strip(),
                        ws_url1=str(xiaoyi_conf.get("ws_url1")).strip(),
                        ws_url2=str(xiaoyi_conf.get("ws_url2")).strip(),
                        enable_streaming=bool(xiaoyi_conf.get("enable_streaming", True)),
                    )
                    xiaoyi_channel = XiaoyiChannel(xiaoyi_config, _DummyBus())
                    channel_manager.register_channel(xiaoyi_channel)
                    xiaoyi_task = asyncio.create_task(xiaoyi_channel.start(), name="xiaoyi")

        if "dingtalk" in changed:
            dingtalk_conf = conf.get("dingtalk") if isinstance(conf, dict) else None
            await _stop_channel(dingtalk_channel, dingtalk_task, "dingtalk", background_wait=True)
            dingtalk_channel, dingtalk_task = None, None
            if isinstance(dingtalk_conf, dict):
                enabled, _ = _is_channel_enabled(dingtalk_conf, ["client_id", "client_secret"])
                if enabled:
                    dingtalk_config = DingTalkConfig(
                        enabled=True,
                        client_id=str(dingtalk_conf.get("client_id") or "").strip(),
                        client_secret=str(dingtalk_conf.get("client_secret") or "").strip(),
                        allow_from=dingtalk_conf.get("allow_from") or [],
                    )
                    dingtalk_channel = DingTalkChannel(dingtalk_config, _DummyBus())
                    channel_manager.register_channel(dingtalk_channel)
                    dingtalk_task = asyncio.create_task(dingtalk_channel.start(), name="dingtalk")

        if "telegram" in changed:
            telegram_conf = conf.get("telegram") if isinstance(conf, dict) else None
            await _stop_channel(telegram_channel, telegram_task, "telegram")
            telegram_channel, telegram_task = None, None
            if isinstance(telegram_conf, dict):
                enabled, _ = _is_channel_enabled(telegram_conf, ["bot_token"])
                if enabled:
                    telegram_config = TelegramChannelConfig(
                        enabled=True,
                        bot_token=str(telegram_conf.get("bot_token") or "").strip(),
                        allow_from=telegram_conf.get("allow_from") or [],
                        parse_mode=str(telegram_conf.get("parse_mode") or "Markdown").strip(),
                        group_chat_mode=str(telegram_conf.get("group_chat_mode") or "mention").strip(),
                    )
                    telegram_channel = TelegramChannel(telegram_config, _DummyBus())
                    channel_manager.register_channel(telegram_channel)
                    telegram_task = asyncio.create_task(telegram_channel.start(), name="telegram")

        if "discord" in changed:
            discord_conf = conf.get("discord") if isinstance(conf, dict) else None
            await _stop_channel(discord_channel, discord_task, "discord")
            discord_channel, discord_task = None, None
            if isinstance(discord_conf, dict):
                enabled, _ = _is_channel_enabled(discord_conf, ["bot_token"])
                if enabled:
                    discord_config = DiscordChannelConfig(
                        enabled=True,
                        bot_token=str(discord_conf.get("bot_token") or "").strip(),
                        application_id=str(discord_conf.get("application_id") or "").strip(),
                        guild_id=str(discord_conf.get("guild_id") or "").strip(),
                        channel_id=str(discord_conf.get("channel_id") or "").strip(),
                        allow_from=discord_conf.get("allow_from") or [],
                        block_dm=(str(discord_conf.get("block_dm")).lower() in ["true", "1"]) or False,
                    )
                    discord_channel = DiscordChannel(discord_config, _DummyBus())
                    channel_manager.register_channel(discord_channel)
                    discord_task = asyncio.create_task(discord_channel.start(), name="discord")

        if "whatsapp" in changed:
            whatsapp_conf = conf.get("whatsapp") if isinstance(conf, dict) else None
            await _stop_channel(whatsapp_channel, whatsapp_task, "whatsapp")
            whatsapp_channel, whatsapp_task = None, None
            if isinstance(whatsapp_conf, dict):
                bridge_ws_url = str(whatsapp_conf.get("bridge_ws_url") or "ws://127.0.0.1:19600/ws").strip()
                enabled_raw = whatsapp_conf.get("enabled", None)
                enabled = bool(bridge_ws_url) if enabled_raw is None else bool(enabled_raw)
                if enabled and bridge_ws_url:
                    whatsapp_config = WhatsAppChannelConfig(
                        enabled=True,
                        enable_streaming=bool(whatsapp_conf.get("enable_streaming", True)),
                        bridge_ws_url=bridge_ws_url,
                        allow_from=whatsapp_conf.get("allow_from") or [],
                        default_jid=str(whatsapp_conf.get("default_jid") or "").strip(),
                        auto_start_bridge=bool(whatsapp_conf.get("auto_start_bridge", False)),
                        bridge_command=str(
                            whatsapp_conf.get("bridge_command") or "node scripts/whatsapp-bridge.js"
                        ).strip(),
                        bridge_workdir=str(whatsapp_conf.get("bridge_workdir") or "").strip(),
                        bridge_env={str(k): str(v) for k, v in (whatsapp_conf.get("bridge_env") or {}).items()},
                    )
                    whatsapp_channel = WhatsAppChannel(whatsapp_config, _DummyBus())
                    channel_manager.register_channel(whatsapp_channel)
                    whatsapp_task = asyncio.create_task(whatsapp_channel.start(), name="whatsapp")

        if "wechat" in changed:
            wechat_conf = conf.get("wechat") if isinstance(conf, dict) else None
            await _stop_channel(wechat_channel, wechat_task, "wechat")
            wechat_channel, wechat_task = None, None
            if isinstance(wechat_conf, dict):
                enabled, _ = _is_channel_enabled(wechat_conf, [])
                if enabled:
                    wechat_config = WechatConfig(
                        enabled=True,
                        base_url=str(wechat_conf.get("base_url") or "https://ilinkai.weixin.qq.com").strip(),
                        bot_token=str(wechat_conf.get("bot_token") or "").strip(),
                        ilink_bot_id=str(wechat_conf.get("ilink_bot_id") or "").strip(),
                        ilink_user_id=str(wechat_conf.get("ilink_user_id") or "").strip(),
                        allow_from=wechat_conf.get("allow_from") or [],
                        auto_login=bool(wechat_conf.get("auto_login", True)),
                        qrcode_poll_interval_sec=float(wechat_conf.get("qrcode_poll_interval_sec", 2.0)),
                        long_poll_timeout_sec=int(wechat_conf.get("long_poll_timeout_sec", 45)),
                        backoff_base_sec=float(wechat_conf.get("backoff_base_sec", 1.0)),
                        backoff_max_sec=float(wechat_conf.get("backoff_max_sec", 30.0)),
                        credential_file=str(
                            wechat_conf.get("credential_file") or "~/.wx-ai-bridge/credentials.json"
                        ).strip(),
                    )
                    wechat_channel = WechatChannel(wechat_config, _DummyBus())
                    channel_manager.register_channel(wechat_channel)
                    wechat_task = asyncio.create_task(wechat_channel.start(), name="wechat")

    channel_manager.set_config_callback(_apply_channel_config)
    await channel_manager.set_config(initial_channels_conf)

    await channel_manager.start_dispatch()
    await cron_scheduler.start()
    web_task = asyncio.create_task(web_channel.start(), name="web-channel")

    logger.info(
        "[Gateway] ready: Web ws://%s:%s%s  AgentServer: %s  Ctrl+C to stop",
        web_host,
        web_port,
        web_path,
        agent_server_url,
    )

    try:
        await web_task
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass
        await web_channel.stop()

        for ch, task, name in [
            (feishu_channel, feishu_task, "feishu"),
            (xiaoyi_channel, xiaoyi_task, "xiaoyi"),
            (dingtalk_channel, dingtalk_task, "dingtalk"),
            (telegram_channel, telegram_task, "telegram"),
            (discord_channel, discord_task, "discord"),
            (whatsapp_channel, whatsapp_task, "whatsapp"),
            (wechat_channel, wechat_task, "wechat"),
        ]:
            if ch is not None and task is not None:
                task.cancel()
                try:
                    await task
                except (TypeError, asyncio.CancelledError):
                    pass
                await ch.stop()

        for bot_key, task in list(feishu_enterprise_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            channel = feishu_enterprise_channels.get(bot_key)
            if channel is not None:
                await channel.stop()

        await cron_scheduler.stop()
        await channel_manager.stop_dispatch()
        await heartbeat_service.stop()
        await message_handler.stop_forwarding()
        await client.disconnect()
        logger.info("[Gateway] stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-gateway",
        description="Start JiuwenClaw Gateway + Channels (split deployment; connects to jiuwenclaw-agentserver).",
    )
    parser.add_argument(
        "--agent-server-url",
        "-u",
        default=None,
        metavar="URL",
        help="AgentServer WebSocket URL (default: AGENT_SERVER_URL or ws://AGENT_SERVER_HOST:AGENT_SERVER_PORT).",
    )
    parser.add_argument(
        "--host",
        "-H",
        default=None,
        metavar="HOST",
        help="WebChannel bind host (default: WEB_HOST or 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        metavar="PORT",
        help="WebChannel bind port (default: WEB_PORT or 19000).",
    )
    parser.add_argument(
        "--web-path",
        default=None,
        metavar="PATH",
        help="WebChannel ws path (default: WEB_PATH or /ws).",
    )
    args = parser.parse_args()

    agent_server_url = (
        args.agent_server_url
        or os.getenv("AGENT_SERVER_URL")
        or f"ws://{os.getenv('AGENT_SERVER_HOST', '127.0.0.1')}:{os.getenv('AGENT_SERVER_PORT', '18092')}"
    )
    web_host = args.host or os.getenv("WEB_HOST", "127.0.0.1")
    web_port = args.port or int(os.getenv("WEB_PORT", "19000"))
    web_path = args.web_path or os.getenv("WEB_PATH", "/ws")

    asyncio.run(
        _run(
            agent_server_url=agent_server_url,
            web_host=web_host,
            web_port=web_port,
            web_path=web_path,
        )
    )


if __name__ == "__main__":
    main()

