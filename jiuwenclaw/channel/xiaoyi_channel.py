# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoyiChannel - 华为小艺 A2A 协议客户端."""

from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import inspect
import json
import os
import re
import time
import ssl
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

import aiohttp

from jiuwenclaw.utils import logger
from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import EventType, Message, ReqMethod


@dataclass
class XiaoyiChannelConfig:
    """小艺通道配置（客户端模式）."""

    enabled: bool = False
    ak: str = ""
    sk: str = ""
    agent_id: str = ""
    ws_url1: str = ""
    ws_url2: str = ""
    enable_streaming: bool = True


def _generate_signature(sk: str, timestamp: str) -> str:
    """生成 HMAC-SHA256 签名（Base64 编码）."""
    h = hmac.new(
        sk.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(h.digest()).decode("utf-8")


def _generate_auth_headers(ak: str, sk: str, agent_id: str) -> dict[str, str]:
    """生成鉴权 Header."""
    timestamp = str(int(time.time() * 1000))
    signature = _generate_signature(sk, timestamp)
    return {
        "x-access-key": ak,
        "x-sign": signature,
        "x-ts": timestamp,
        "x-agent-id": agent_id,
    }


class XiaoyiChannel(BaseChannel):
    """小艺通道：作为客户端连接到小艺服务器，实现 A2A 协议."""

    name = "xiaoyi"
    _TASK_KEEPALIVE_INTERVAL_SECONDS = 8.0

    def __init__(self, config: XiaoyiChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: XiaoyiChannelConfig = config
        self._ws_connections: dict[str, Any] = {}  # Dual channel connections
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._running = False
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}  # Heartbeat tasks for each channel
        self._connect_tasks: dict[str, asyncio.Task] = {}  # Connection tasks for each channel
        self._session_task_map: dict[str, str] = {}
        self._session_heartbeat_tasks: dict[str, asyncio.Task] = {}  # Response heartbeat tasks for each session
        self._artifact_map: dict[str, str] = {}
        self._stream_text_buffers: dict[str, str] = {}
        self._task_keepalive_tasks: dict[str, asyncio.Task] = {}
        self._task_last_activity: dict[str, float] = {}
        self._on_message_cb: Callable[[Message], Any] | None = None

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def clients(self) -> set[Any]:
        return set()

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if self._running:
            logger.warning("XiaoyiChannel 已在运行")
            return
        if not self.config.enabled:
            logger.warning("XiaoyiChannel 未启用（enabled=False）")
            return
        if not self.config.ak or not self.config.sk or not self.config.agent_id:
            logger.error("XiaoyiChannel 未配置 ak/sk/agent_id")
            return

        self._running = True
        # Start dual channel connections
        for url_key, url in [("ws_url1", self.config.ws_url1), ("ws_url2", self.config.ws_url2)]:
            if url:
                self._connect_tasks[url_key] = asyncio.create_task(self._reconnect_loop(url_key, url))
        logger.info("XiaoyiChannel 已启动（客户端模式，双通道）")

    async def stop(self) -> None:
        self._running = False
        # Cancel all heartbeat tasks
        for url_key in list(self._heartbeat_tasks.keys()):
            if self._heartbeat_tasks[url_key]:
                self._heartbeat_tasks[url_key].cancel()
                self._heartbeat_tasks[url_key] = None
        # Cancel all connection tasks
        for url_key in list(self._connect_tasks.keys()):
            if self._connect_tasks[url_key]:
                self._connect_tasks[url_key].cancel()
                self._connect_tasks[url_key] = None
        # Cancel all session heartbeat tasks
        for session_id in list(self._session_heartbeat_tasks.keys()):
            if self._session_heartbeat_tasks[session_id]:
                self._session_heartbeat_tasks[session_id].cancel()
                self._session_heartbeat_tasks[session_id] = None
        # Close all websocket connections
        for url_key, ws in list(self._ws_connections.items()):
            if ws:
                try:
                    await ws.close()
                except Exception as e:
                    logger.warning(f"关闭 WebSocket 连接失败 ({url_key}): {e}")
                self._ws_connections[url_key] = None
        self._heartbeat_tasks.clear()
        self._connect_tasks.clear()
        self._session_heartbeat_tasks.clear()
        self._ws_connections.clear()
        self._send_locks.clear()
        self._artifact_map.clear()
        self._stream_text_buffers.clear()
        await self._stop_all_task_keepalive()
        logger.info("XiaoyiChannel 已停止")

    def _extract_platform_receive_info(self, msg: Message) -> tuple[str, str]:
        """
        从消息中提取小艺平台会话 ID 与任务 ID。
        优先使用 metadata（避免 \new_session 覆盖 session_id 后无法回发），否则回退到 session_id 与 _session_task_map。
        """
        meta = getattr(msg, "metadata", None) or {}
        platform_session_id = (meta.get("xiaoyi_session_id") or "").strip()
        platform_task_id = (meta.get("xiaoyi_task_id") or "").strip()
        if platform_session_id or platform_task_id:
            return (
                platform_session_id or (msg.session_id or ""),
                platform_task_id or platform_session_id,
            )
        session_id = msg.session_id or ""
        task_id = self._session_task_map.get(session_id, session_id)
        return session_id, task_id

    async def send(self, msg: Message) -> None:
        """发送消息到小艺服务端（A2A 格式，双通道发送）."""
        if not self._ws_connections:
            return
        session_id, task_id = self._extract_platform_receive_info(msg)
        logger.info(f"XiaoyiChannel 发送消息: {msg}")

        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_name = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""
        stream_key = str(getattr(msg, "id", "") or "")
        streaming_enabled = bool(self.config.enable_streaming)

        if event_name == "chat.delta":
            delta = self._extract_message_content(msg)
            if delta and stream_key:
                self._stream_text_buffers[stream_key] = (
                    self._stream_text_buffers.get(stream_key, "") + delta
                )
            if not streaming_enabled:
                return
            content = delta
            append = True
            final = False
        elif event_name == "chat.processing_status":
            # 非 streaming 模式下，仅在 is_processing=false 且无 final 的场景回放一次缓存作为最终结果。
            if payload.get("is_processing") is not False:
                return
            if streaming_enabled:
                return
            content = self._stream_text_buffers.pop(stream_key, "")
            if not content.strip():
                return
            append = False
            final = True
        else:
            if (not streaming_enabled) and event_name in {"chat.tool_call", "chat.tool_result", "todo.updated"}:
                return
            content = self._extract_message_content(msg)
            if event_name == "chat.final":
                buffered_text = self._stream_text_buffers.pop(stream_key, "")
                content = self._merge_stream_and_final_content(buffered_text, content)
            elif event_name in {"chat.error", "chat.interrupt_result"}:
                self._stream_text_buffers.pop(stream_key, None)
            append = event_name in {"chat.tool_call", "chat.tool_result"}
            final = event_name in {"chat.final", "chat.error", "chat.interrupt_result"}

        if not content.strip():
            logger.debug("XiaoyiChannel 发送消息为空，跳过: id={} event={}", msg.id, getattr(msg.event_type, "value", None))
            return

        task_key = self._make_task_key(session_id, task_id)
        self._touch_task_activity(task_key)

        # Send to all active connections
        for url_key, ws in self._ws_connections.items():
            if ws:
                try:
                    await self._send_text_response(
                        session_id,
                        task_id,
                        content,
                        url_key,
                        append=append,
                        final=final,
                    )
                except Exception as e:
                    logger.warning(f"XiaoyiChannel 发送消息失败 ({url_key}): {e}")

        if final and session_id:
            await self._stop_session_heartbeat(session_id)

        if final:
            await self._stop_task_keepalive(task_key)

    @staticmethod
    def _merge_stream_and_final_content(stream_text: str, final_text: str) -> str:
        """合并流式累计文本与 final 文本，优先保留信息更完整的一侧。"""
        stream_text = stream_text or ""
        final_text = final_text or ""
        if not stream_text.strip():
            return final_text
        if not final_text.strip():
            return stream_text
        if stream_text == final_text:
            return final_text
        if final_text.startswith(stream_text):
            return final_text
        if stream_text.startswith(final_text):
            return stream_text
        return final_text if len(final_text) >= len(stream_text) else stream_text

    @staticmethod
    def _stringify_value(value: Any) -> str:
        """将任意对象转为适合外发的文本."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value)

    @staticmethod
    def _extract_preferred_text(value: Any) -> str:
        """从结构化内容中提取优先展示的自然语言文本，避免透传大段 JSON."""
        if value is None:
            return ""
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return ""
            # 兼容 {"output":"...","result_type":"answer"} / [...] 字符串
            if (
                (text.startswith("{") and text.endswith("}"))
                or (text.startswith("[") and text.endswith("]"))
            ):
                try:
                    parsed = json.loads(text)
                    extracted = XiaoyiChannel._extract_preferred_text(parsed)
                    if extracted:
                        return extracted
                    # 如果是结构化 JSON 但提取不到可读字段，直接丢弃，避免原样透传 JSON。
                    return ""
                except Exception:
                    # 兼容 Python dict 字符串
                    match = re.search(
                        r"['\"](output|content|text|message|result|error|summary)['\"]\s*:\s*['\"](.+?)['\"]",
                        text,
                        flags=re.DOTALL,
                    )
                    if match:
                        return match.group(2).strip()
                    return ""
            return text

        if isinstance(value, dict):
            for key in ("output", "content", "text", "message", "result", "error", "summary"):
                if key in value:
                    text = XiaoyiChannel._extract_preferred_text(value.get(key))
                    if text:
                        return text
            return ""

        if isinstance(value, list):
            parts: list[str] = []
            for item in value[:3]:
                text = XiaoyiChannel._extract_preferred_text(item)
                if text:
                    parts.append(text)
            return "\n".join(parts).strip()

        return str(value).strip()

    @staticmethod
    def _truncate_text(text: str, max_len: int = 240) -> str:
        text = (text or "").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "..."

    def _extract_message_content(self, msg: Message) -> str:
        """按事件类型提取适合发送到小艺的文本."""
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        event_name = getattr(msg.event_type, "value", None) or payload.get("event_type") or ""

        if event_name == "chat.tool_call":
            tool_info = payload.get("tool_call", payload)
            if isinstance(tool_info, dict):
                tool_name = tool_info.get("tool_name") or tool_info.get("name") or "unknown_tool"
                return f"[工具调用] {tool_name}"
            return "[工具调用]"

        if event_name == "chat.tool_result":
            tool_name = payload.get("tool_name") or "unknown_tool"
            result_text = self._extract_tool_result_text(payload.get("result"))
            return f"[工具完成] {tool_name}" if not result_text else f"[工具完成] {tool_name}: {result_text}"

        if event_name == "chat.error":
            error_text = self._truncate_text(self._extract_preferred_text(payload.get("error")))
            return f"[错误] {error_text}" if error_text else "[错误] 未知错误"

        if event_name == "chat.processing_status":
            # 小艺端不消费中间状态事件；下发会干扰流式 task 状态。
            return ""

        if event_name == "chat.interrupt_result":
            return self._extract_preferred_text(payload.get("message")) or "[状态] 任务已中断"

        if event_name == "heartbeat.relay":
            return self._extract_preferred_text(payload.get("heartbeat"))

        content = (msg.params or {}).get("content")
        if not content:
            content = payload.get("content")
        text = self._extract_preferred_text(content)
        if text:
            return self._truncate_text(text, max_len=4000)

        # 其他结构化状态事件默认不下发，避免输出原始格式噪音。
        return ""

    def _extract_tool_result_text(self, value: Any) -> str:
        """提取工具结果摘要，优先 summary/message，避免外发结构化数据."""
        if isinstance(value, dict):
            for key in ("summary", "message", "output", "result", "content", "text", "error"):
                if key in value:
                    text = self._extract_preferred_text(value.get(key))
                    if text:
                        return self._truncate_text(text, max_len=240)
        text = self._extract_preferred_text(value)
        return self._truncate_text(text, max_len=240)

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={
                "mode": "client",
                "ws_url1": self.config.ws_url1,
                "ws_url2": self.config.ws_url2,
                "agent_id": self.config.agent_id,
            },
        )

    async def _reconnect_loop(self, url_key: str, url: str) -> None:
        """自动重连循环（双通道）."""
        while self._running:
            try:
                await self._connect(url_key, url)
                if not self._running:
                    break
                # 连接被远端正常关闭时也做退避，避免瞬时重连刷屏。
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"XiaoyiChannel 连接失败 ({url}): {e}")
                await asyncio.sleep(5)

    async def _connect(self, url_key: str, url: str) -> None:
        """连接到小艺服务器（双通道）."""
        import websockets

        headers = _generate_auth_headers(self.config.ak, self.config.sk, self.config.agent_id)
        parsed = urlparse(url)
        is_ip = bool(parsed.hostname and parsed.hostname.replace(".", "").isdigit())

        ssl_context = ssl.create_default_context()
        if is_ip:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
            url,
            additional_headers=headers,
            ssl=ssl_context,
            ping_interval=15,
            ping_timeout=15,
            close_timeout=5,
        ) as ws:
            self._ws_connections[url_key] = ws
            self._send_locks[url_key] = asyncio.Lock()
            logger.info(f"XiaoyiChannel 已连接 {url_key}: {url}")

            # 发送初始化消息（必须在 heartbeat 之前）
            await self._send_init_message(url_key)

            # 启动心跳
            self._heartbeat_tasks[url_key] = asyncio.create_task(self._heartbeat_loop(url_key))

            try:
                async for raw in ws:
                    await self._handle_raw_message(raw)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 连接异常 ({url_key}): {e}")
            finally:
                if self._heartbeat_tasks.get(url_key):
                    self._heartbeat_tasks[url_key].cancel()
                    self._heartbeat_tasks[url_key] = None
                self._ws_connections[url_key] = None
                self._send_locks.pop(url_key, None)
                close_code = getattr(ws, "close_code", None)
                close_reason = getattr(ws, "close_reason", None)
                logger.info(
                    f"XiaoyiChannel 连接关闭 {url_key}: {url} (code={close_code}, reason={close_reason})"
                )
    async def _send_init_message(self, url_key: str) -> None:
        """发送初始化消息 (clawd_bot_init) 到指定通道."""
        ws = self._ws_connections.get(url_key)
        if not ws:
            return
        init_message = {
            "msgType": "clawd_bot_init",
            "agentId": self.config.agent_id,
        }
        try:
            await self._safe_ws_send(url_key, init_message)
            logger.info(f"XiaoyiChannel 已发送初始化消息 ({url_key})")
        except Exception as e:
            logger.warning(f"XiaoyiChannel 发送初始化消息失败 ({url_key}): {e}")
            raise

    async def _heartbeat_loop(self, url_key: str) -> None:
        """应用层心跳循环（20秒间隔）."""
        while self._running and self._ws_connections.get(url_key):
            try:
                heartbeat = {"msgType": "heartbeat", "agentId": self.config.agent_id}
                await self._safe_ws_send(url_key, heartbeat)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 心跳发送失败 ({url_key}): {e}")
                ws = self._ws_connections.get(url_key)
                if ws:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                break
            await asyncio.sleep(20)

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        """处理接收到的原始消息，转换为 JiuwenClaw 内部格式."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("XiaoyiChannel JSON 解析失败")
            return

        msg_type = message.get("msgType")
        if msg_type == "heartbeat":
            return

        method = message.get("method")
        if method == "message/stream":
            await self._handle_message_stream(message)
        elif method == "clearContext":
            await self._handle_clear_context(message)
        elif method == "tasks/cancel":
            await self._handle_tasks_cancel(message)
        else:
            logger.warning(f"XiaoyiChannel 未知方法: {method}")

    async def _handle_message_stream(self, message: dict[str, Any]) -> None:
        """处理 message/stream 消息，转换为 JiuwenClaw Message."""
        session_id = message.get("sessionId") or message.get("params", {}).get("sessionId", "")
        task_id = message.get("params", {}).get("id", "")
        user_message = message.get("params", {}).get("message", {})
        parts = user_message.get("parts", [])

        text = ""
        files = []
        for part in parts:
            if part.get("kind") == "text":
                text = part.get("text", "")
            elif part.get("kind") == "file":
                file_data = part.get("file", {})
                file_info = {
                    "name": file_data.get("name", ""),
                    "url": file_data.get("uri", ""),
                    "size": file_data.get("size", 0),
                    "type": file_data.get("mimeType", "")
                }

                file_url = file_info.get("url", "")
                if file_url:
                    file_content = await self._download_file(file_url)
                    if file_content:
                        workspace_dir = os.path.expanduser("~/.jiuwenclaw/workspace")
                        file_path = os.path.join(workspace_dir, file_info["name"])
                        try:
                            os.makedirs(workspace_dir, exist_ok=True)
                            with open(file_path, 'wb') as f:
                                f.write(file_content)
                            file_info["path"] = file_path
                        except Exception as e:
                            logger.warning("XiaoyiChannel 文件保存失败: %s", e)
                files.append(file_info)

        self._session_task_map[session_id] = task_id
        await self._start_task_keepalive(session_id, task_id)

        # 将最近一次可回发的小艺身份写入 config.yaml，供 cron 推送时使用
        try:
            from jiuwenclaw.config import update_channel_in_config

            update_channel_in_config(
                "xiaoyi",
                {
                    "last_session_id": session_id or "",
                    "last_task_id": task_id or "",
                },
            )
        except Exception:
            pass

        # 平台身份写入 metadata，供回发时使用（与 session_id 解耦，\new_session 后仍可正确回发）
        user_message = Message(
            id=message.get("id", ""),
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params={"query": text, "task_id": task_id, "files": files},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=bool(self.config.enable_streaming),
            metadata={
                "method": "message/stream",
                "xiaoyi_session_id": session_id,
                "xiaoyi_task_id": task_id,
            },
        )

        handled = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled = bool(result)

        if not handled:
            await self.bus.route_user_message(user_message)

        # Start session heartbeat to prevent xiaoyi client timeout
        if session_id:
            await self._start_session_heartbeat(session_id, task_id)

    async def _start_session_heartbeat(self, session_id: str, task_id: str) -> None:
        """启动会话心跳任务，每隔5秒发送空消息直到final消息发出."""
        await self._stop_session_heartbeat(session_id)

        async def heartbeat_loop():
            try:
                while self._running:
                    await asyncio.sleep(5)
                    # Send empty heartbeat message (non-final)
                    for url_key, ws in self._ws_connections.items():
                        if ws:
                            try:
                                await self._send_text_response(
                                    session_id,
                                    task_id,
                                    "",
                                    url_key,
                                    append=True,
                                    final=False,
                                )
                            except Exception as e:
                                logger.warning(f"XiaoyiChannel 发送心跳消息失败 ({url_key}): {e}")
            except asyncio.CancelledError:
                logger.info(f"XiaoyiChannel 会话心跳已停止: {session_id}")
            except Exception as e:
                logger.warning(f"XiaoyiChannel 会话心跳异常 ({session_id}): {e}")

        self._session_heartbeat_tasks[session_id] = asyncio.create_task(heartbeat_loop())
        logger.info(f"XiaoyiChannel 会话心跳已启动: {session_id}")

    async def _stop_session_heartbeat(self, session_id: str) -> None:
        """停止会话心跳任务."""
        if session_id in self._session_heartbeat_tasks:
            task = self._session_heartbeat_tasks[session_id]
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._session_heartbeat_tasks.pop(session_id, None)
            logger.info(f"XiaoyiChannel 会话心跳已停止: {session_id}")

    async def _download_file(self, url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.warning(f"XiaoyiChannel 文件下载失败: {url}, 状态码: {response.status}")
                        return None
        except Exception as e:
            logger.warning(f"XiaoyiChannel 文件下载异常: {url}, 错误: {e}")
            return None

    async def _handle_clear_context(self, message: dict[str, Any]) -> None:
        """处理清空上下文请求."""
        session_id = message.get("sessionId", "")
        logger.info(f"XiaoyiChannel 清空上下文: {session_id}")

        self._session_task_map.pop(session_id, None)
        for key in [k for k in self._artifact_map.keys() if k.startswith(f"{session_id}:")]:
            self._artifact_map.pop(key, None)
        await self._stop_task_keepalive_by_session(session_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"status": {"state": "cleared"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, session_id, response, url_key)

    async def _handle_tasks_cancel(self, message: dict[str, Any]) -> None:
        """处理取消任务请求."""
        session_id = message.get("sessionId", "")
        task_id = message.get("params", {}).get("id") or message.get("taskId", "")
        logger.info(f"XiaoyiChannel 取消任务: {session_id} {task_id}")
        await self._stop_task_keepalive(self._make_task_key(session_id, task_id))
        if session_id:
            await self._stop_session_heartbeat(session_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"id": message.get("id", ""), "status": {"state": "canceled"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_text_response(
        self,
        session_id: str,
        task_id: str,
        text: str,
        url_key: str,
        *,
        append: bool = False,
        final: bool = True,
    ) -> None:
        """发送文本响应（A2A 格式）到指定通道."""
        artifact_key = f"{session_id}:{task_id}"
        artifact_id = self._artifact_map.get(artifact_key)
        append_flag = append
        if not artifact_id:
            artifact_id = f"artifact_{int(time.time() * 1000)}"
            self._artifact_map[artifact_key] = artifact_id
            append_flag = False

        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": append_flag,
                "lastChunk": final,
                "final": final,
                "artifact": {
                    "artifactId": artifact_id,
                    "parts": [{"kind": "text", "text": text}],
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response, url_key)
        if final:
            self._artifact_map.pop(artifact_key, None)

    async def _send_agent_response(self, session_id: str, task_id: str, response: dict[str, Any], url_key: str) -> None:
        """发送 agent_response 包装的消息（A2A 格式）到指定通道."""
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response),
        }
        try:
            await self._safe_ws_send(url_key, wrapper)
        except Exception as e:
            logger.warning(f"XiaoyiChannel 发送响应失败 ({url_key}): {e}")

    async def _safe_ws_send(self, url_key: str, payload: dict[str, Any]) -> None:
        """串行发送同一连接上的消息，避免业务消息和心跳并发发送导致连接不稳定."""
        ws = self._ws_connections.get(url_key)
        if not ws:
            raise RuntimeError(f"ws connection not available: {url_key}")
        lock = self._send_locks.get(url_key)
        if lock is None:
            lock = asyncio.Lock()
            self._send_locks[url_key] = lock
        data = json.dumps(payload, ensure_ascii=False)
        async with lock:
            await ws.send(data)

    @staticmethod
    def _make_task_key(session_id: str, task_id: str) -> str:
        return f"{session_id}:{task_id}"

    def _touch_task_activity(self, task_key: str) -> None:
        self._task_last_activity[task_key] = time.time()

    async def _start_task_keepalive(self, session_id: str, task_id: str) -> None:
        """为长任务启动任务级保活，防止平台因长时间无任务更新断开。"""
        task_key = self._make_task_key(session_id, task_id)
        await self._stop_task_keepalive(task_key)
        self._touch_task_activity(task_key)

        async def _loop() -> None:
            while self._running and task_key in self._task_keepalive_tasks:
                await asyncio.sleep(self._TASK_KEEPALIVE_INTERVAL_SECONDS)
                if task_key not in self._task_keepalive_tasks:
                    break
                last = self._task_last_activity.get(task_key, 0.0)
                if (time.time() - last) < self._TASK_KEEPALIVE_INTERVAL_SECONDS:
                    continue
                try:
                    await self._send_task_status(session_id, task_id, "running")
                    self._touch_task_activity(task_key)
                except Exception as e:
                    logger.debug("XiaoyiChannel 任务保活发送失败: {}", e)

        self._task_keepalive_tasks[task_key] = asyncio.create_task(
            _loop(),
            name=f"xiaoyi-task-keepalive:{task_key}",
        )

    async def _stop_task_keepalive(self, task_key: str) -> None:
        task = self._task_keepalive_tasks.pop(task_key, None)
        self._task_last_activity.pop(task_key, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _stop_task_keepalive_by_session(self, session_id: str) -> None:
        keys = [k for k in self._task_keepalive_tasks.keys() if k.startswith(f"{session_id}:")]
        for key in keys:
            await self._stop_task_keepalive(key)

    async def _stop_all_task_keepalive(self) -> None:
        keys = list(self._task_keepalive_tasks.keys())
        for key in keys:
            await self._stop_task_keepalive(key)

    async def _send_task_status(self, session_id: str, task_id: str, state: str) -> None:
        """发送任务状态更新，作为长任务保活信号."""
        response = {
            "jsonrpc": "2.0",
            "id": f"status_{int(time.time() * 1000)}",
            "result": {
                "id": task_id,
                "status": {"state": state},
            },
        }
        for url_key in list(self._ws_connections.keys()):
            try:
                await self._send_agent_response(session_id, task_id, response, url_key)
            except Exception as e:
                logger.debug("XiaoyiChannel 发送任务状态失败 ({}): {}", url_key, e)
