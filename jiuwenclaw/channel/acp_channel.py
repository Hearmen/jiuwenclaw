from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from jiuwenclaw.channel.base import BaseChannel, RobotMessageRouter
from jiuwenclaw.e2a.acp.session_updates import (
    build_acp_final_text_update,
    build_acp_session_update,
    build_acp_usage_update,
)
from jiuwenclaw.e2a.adapters import envelope_from_acp_jsonrpc
from jiuwenclaw.e2a.constants import (
    E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR,
    E2A_RESPONSE_KIND_ACP_PROMPT_RESULT,
    E2A_RESPONSE_KIND_ACP_SESSION_UPDATE,
    E2A_RESPONSE_KIND_E2A_CHUNK,
    E2A_RESPONSE_STATUS_FAILED,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_RESPONSE_STATUS_SUCCEEDED,
    E2A_SOURCE_PROTOCOL_E2A,
)
from jiuwenclaw.e2a.models import E2AEnvelope, E2AProvenance, E2AResponse, utc_now_iso
from jiuwenclaw.schema.message import EventType, Message, ReqMethod
from jiuwenclaw.version import __version__

logger = logging.getLogger(__name__)

_ACP_PROTOCOL_VERSION = 1
_ACP_STDOUT = getattr(sys, "__stdout__", sys.stdout)
_STDIN_EOF_GRACE_SECONDS = 5.0
_PROMPT_IDLE_FINALIZE_SECONDS = 3.0
_ACP_PENDING_RPC_TIMEOUT_SECONDS = 60.0
_ACP_GATEWAY_CONNECT_MAX_ATTEMPTS = 12
_ACP_GATEWAY_CONNECT_BASE_DELAY_SEC = 0.15


@dataclass
class AcpChannelConfig:
    enabled: bool = True
    channel_id: str = "acp"
    default_session_id: str = "acp_cli_session"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _AcpRequestContext:
    jsonrpc_id: str | int | None
    method: str | None
    response_mode: str = "e2a"
    session_id: str | None = None
    assistant_message_id: str | None = None
    thought_message_id: str | None = None
    sequence: int = 0
    idle_finalize_task: asyncio.Task | None = None


class AcpChannel(BaseChannel):
    """ACP stdio 通道。

    - 入站：stdin 每行一个 JSON，支持 E2AEnvelope 或 ACP JSON-RPC 请求。
    - 出站：stdout 每行一个 E2AResponse JSON。
    - 语义：将 ``session/prompt`` 映射为内部 ``chat.send``。
    """

    name = "acp"

    def __init__(
        self,
        config: AcpChannelConfig,
        router: RobotMessageRouter,
        *,
        gateway_url: str | None = None,
    ):
        super().__init__(config, router)
        self.config: AcpChannelConfig = config
        self._gateway_url = gateway_url
        self._gateway_ws = None
        self._gateway_reader_task: asyncio.Task | None = None
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._request_ctx: dict[str, _AcpRequestContext] = {}
        self._session_ctx: dict[str, dict[str, Any]] = {}
        self._active_prompt_request_by_session: dict[str, str] = {}
        # value: (session_id, created_at)
        self._pending_client_rpc_session_by_id: dict[str, tuple[str, float]] = {}

    @property
    def channel_id(self) -> str:
        return str(self.config.channel_id or self.name).strip() or self.name

    def on_message(self, callback: Callable[[Message], Any]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        if self._on_message_cb is None and self._gateway_url:
            await self._ensure_gateway_connection()
        stdin_eof = False
        stdin_eof_since: float | None = None
        while self._running:
            if stdin_eof:
                if not self._request_ctx and not self._pending_client_rpc_session_by_id and stdin_eof_since is not None:
                    if (time.time() - stdin_eof_since) >= _STDIN_EOF_GRACE_SECONDS:
                        break
                await asyncio.sleep(0.05)
                continue
            raw = await asyncio.to_thread(sys.stdin.buffer.readline)
            if not raw:
                stdin_eof = True
                if stdin_eof_since is None:
                    stdin_eof_since = time.time()
                continue
            stdin_eof = False
            stdin_eof_since = None
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                await self._handle_raw_line(line)
            except Exception as exc:  # noqa: BLE001
                logger.exception("[ACP] stdio inbound failed: %s", exc)
                await self._write_response(
                    E2AResponse(
                        response_id=f"acp-err-{uuid.uuid4().hex[:8]}",
                        request_id=None,
                        is_final=True,
                        status=E2A_RESPONSE_STATUS_FAILED,
                        response_kind=E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR,
                        timestamp=utc_now_iso(),
                        provenance=self._provenance("stdio_error"),
                        channel=self.channel_id,
                        body={"code": -32603, "message": str(exc)},
                    )
                )

    async def stop(self) -> None:
        self._running = False
        for request_id in list(self._request_ctx.keys()):
            await self._clear_request_context(request_id)
        self._pending_client_rpc_session_by_id.clear()
        await self._close_gateway_connection()

    def _sweep_stale_pending(self) -> None:
        """移除超时的 pending RPC 条目，防止永久堆积。"""
        now = time.time()
        stale = [
            jsonrpc_id
            for jsonrpc_id, entry in self._pending_client_rpc_session_by_id.items()
            if isinstance(entry, tuple) and len(entry) >= 2 and (now - entry[1]) > _ACP_PENDING_RPC_TIMEOUT_SECONDS
        ]
        for jsonrpc_id in stale:
            self._pending_client_rpc_session_by_id.pop(jsonrpc_id, None)
            logger.info("[ACP] pending RPC entry expired: jsonrpc_id=%s", jsonrpc_id)

    async def send(self, msg: Message) -> None:
        ctx = self._request_ctx.get(str(msg.id))
        if ctx is None:
            logger.debug("[ACP] skip outbound without request context: id=%s", msg.id)
            return

        if ctx.response_mode == "jsonrpc":
            is_final = await self._send_jsonrpc_message(msg, ctx)
            if is_final:
                await self._clear_request_context(str(msg.id))
            return

        response = self._message_to_e2a_response(msg, ctx)
        if response is None:
            return
        await self._write_response(response)
        if response.is_final:
            await self._clear_request_context(str(msg.id))

    async def _handle_raw_line(self, line: str) -> None:
        data = json.loads(line)
        if self._is_jsonrpc_request(data):
            await self._handle_jsonrpc_request(data)
            return
        if self._is_jsonrpc_response(data):
            await self._handle_jsonrpc_response(data)
            return

        env = self._parse_envelope(data)
        msg = self._envelope_to_message(env)
        self._request_ctx[msg.id] = _AcpRequestContext(
            jsonrpc_id=env.jsonrpc_id,
            method=env.method,
            response_mode="e2a",
            session_id=msg.session_id,
        )

        await self._dispatch_message(msg)

    async def _handle_jsonrpc_response(self, data: dict[str, Any]) -> None:
        from jiuwenclaw.e2a.adapters import build_acp_tool_response_message

        jsonrpc_id = str(data.get("id") or "").strip()
        if not jsonrpc_id:
            return

        pending = self._pending_client_rpc_session_by_id.pop(jsonrpc_id, None)
        session_id = pending[0] if isinstance(pending, tuple) else None
        msg = build_acp_tool_response_message(
            jsonrpc_id=jsonrpc_id,
            response_data=data,
            session_id=session_id,
            channel_id=self.channel_id,
        )
        await self._dispatch_message(msg)

    def _parse_envelope(self, data: dict[str, Any]) -> E2AEnvelope:
        env = E2AEnvelope.from_dict(dict(data))
        if not env.request_id:
            env.request_id = f"acp_{uuid.uuid4().hex[:12]}"
        if not env.channel:
            env.channel = self.channel_id
        return env

    def _envelope_to_message(self, env: E2AEnvelope) -> Message:
        method = str(env.method or "").strip()
        params = dict(env.params or {})
        session_id = (
            env.session_id
            or params.get("session_id")
            or self.config.default_session_id
        )

        req_method = self._parse_req_method(method)
        if method == "session/prompt":
            text = self._extract_prompt_text(params)
            params.setdefault("content", text)
            params.setdefault("query", text)
            req_method = ReqMethod.CHAT_SEND
        if req_method is None:
            raise ValueError(f"unsupported ACP/E2A method: {method or '<empty>'}")

        if req_method == ReqMethod.CHAT_SEND:
            params.setdefault("query", params.get("content", ""))

        return Message(
            id=str(env.request_id),
            type="req",
            channel_id=self.channel_id,
            session_id=str(session_id),
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=req_method,
            is_stream=bool(env.is_stream or req_method == ReqMethod.CHAT_SEND),
            metadata={
                "acp": {
                    "jsonrpc_id": env.jsonrpc_id,
                    "method": method,
                    **dict(self.config.metadata or {}),
                }
            },
        )

    def _message_to_e2a_response(
        self,
        msg: Message,
        ctx: _AcpRequestContext,
    ) -> E2AResponse | None:
        payload = dict(msg.payload or {})
        ts = utc_now_iso()
        sequence = ctx.sequence

        if msg.type == "event" and msg.event_type == EventType.CHAT_DELTA:
            ctx.sequence += 1
            source_chunk_type = payload.get("source_chunk_type")
            delta_kind = "reasoning" if source_chunk_type == "llm_reasoning" else "text"
            return E2AResponse(
                response_id=f"{msg.id}:{sequence}",
                request_id=msg.id,
                jsonrpc_id=ctx.jsonrpc_id,
                sequence=sequence,
                is_final=False,
                status=E2A_RESPONSE_STATUS_IN_PROGRESS,
                response_kind=E2A_RESPONSE_KIND_E2A_CHUNK,
                timestamp=ts,
                provenance=self._provenance("chat.delta"),
                channel=self.channel_id,
                session_id=msg.session_id,
                is_stream=True,
                body={
                    "event_type": "chat.delta",
                    "delta_kind": delta_kind,
                    "delta": str(payload.get("content", "") or ""),
                    "payload": payload,
                },
            )

        if msg.type == "event" and msg.event_type == EventType.CHAT_ERROR:
            ctx.sequence += 1
            return E2AResponse(
                response_id=f"{msg.id}:{sequence}",
                request_id=msg.id,
                jsonrpc_id=ctx.jsonrpc_id,
                sequence=sequence,
                is_final=True,
                status=E2A_RESPONSE_STATUS_FAILED,
                response_kind=E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR,
                timestamp=ts,
                provenance=self._provenance("chat.error"),
                channel=self.channel_id,
                session_id=msg.session_id,
                body={
                    "code": -32603,
                    "message": str(payload.get("error") or payload.get("content") or "Agent error"),
                },
            )

        if msg.type == "event" and msg.event_type == EventType.CHAT_FINAL:
            ctx.sequence += 1
            result_body = dict(payload)
            result_body.setdefault("session_id", msg.session_id)
            return E2AResponse(
                response_id=f"{msg.id}:{sequence}",
                request_id=msg.id,
                jsonrpc_id=ctx.jsonrpc_id,
                sequence=sequence,
                is_final=True,
                status=E2A_RESPONSE_STATUS_SUCCEEDED,
                response_kind=E2A_RESPONSE_KIND_ACP_PROMPT_RESULT,
                timestamp=ts,
                provenance=self._provenance("chat.final"),
                channel=self.channel_id,
                session_id=msg.session_id,
                body=result_body,
            )

        if msg.type == "event":
            update = self._build_acp_session_update(msg, payload, ctx)
            if update is not None:
                ctx.sequence += 1
                return E2AResponse(
                    response_id=f"{msg.id}:{sequence}",
                    request_id=msg.id,
                    jsonrpc_id=ctx.jsonrpc_id,
                    sequence=sequence,
                    is_final=False,
                    status=E2A_RESPONSE_STATUS_IN_PROGRESS,
                    response_kind=E2A_RESPONSE_KIND_ACP_SESSION_UPDATE,
                    timestamp=ts,
                    provenance=self._provenance(str(msg.event_type.value if msg.event_type else "session.update")),
                    channel=self.channel_id,
                    session_id=msg.session_id,
                    is_stream=True,
                    body={
                        "sessionId": str(msg.session_id or ctx.session_id or ""),
                        "update": update,
                    },
                )

        if msg.type == "res" and msg.ok:
            if payload.get("accepted") is True:
                return None
            ctx.sequence += 1
            result_body = dict(payload)
            result_body.setdefault("session_id", msg.session_id)
            return E2AResponse(
                response_id=f"{msg.id}:{sequence}",
                request_id=msg.id,
                jsonrpc_id=ctx.jsonrpc_id,
                sequence=sequence,
                is_final=True,
                status=E2A_RESPONSE_STATUS_SUCCEEDED,
                response_kind=E2A_RESPONSE_KIND_ACP_PROMPT_RESULT,
                timestamp=ts,
                provenance=self._provenance("response.ok"),
                channel=self.channel_id,
                session_id=msg.session_id,
                body=result_body,
            )

        if msg.type == "event":
            # 辅助事件先忽略，避免把 processing_status/tool_call/todo 等中间态误判为最终失败。
            return None

        ctx.sequence += 1
        error_text = str(payload.get("error") or payload.get("content") or "request failed")
        return E2AResponse(
            response_id=f"{msg.id}:{sequence}",
            request_id=msg.id,
            jsonrpc_id=ctx.jsonrpc_id,
            sequence=sequence,
            is_final=True,
            status=E2A_RESPONSE_STATUS_FAILED,
            response_kind=E2A_RESPONSE_KIND_ACP_JSONRPC_ERROR,
            timestamp=ts,
            provenance=self._provenance("response.error"),
            channel=self.channel_id,
            session_id=msg.session_id,
            body={"code": -32603, "message": error_text},
        )

    async def _write_response(self, response: E2AResponse) -> None:
        line = json.dumps(response.to_dict(), ensure_ascii=False)
        _ACP_STDOUT.buffer.write((line + "\n").encode("utf-8"))
        _ACP_STDOUT.buffer.flush()

    async def _write_jsonrpc_result(self, rpc_id: str | int | None, result: Any) -> None:
        payload = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
        _ACP_STDOUT.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        _ACP_STDOUT.buffer.flush()

    async def _write_jsonrpc_error(
        self,
        rpc_id: str | int | None,
        code: int,
        message: str,
    ) -> None:
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": code,
                "message": message,
            },
        }
        _ACP_STDOUT.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        _ACP_STDOUT.buffer.flush()

    async def _write_jsonrpc_notification(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        _ACP_STDOUT.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        _ACP_STDOUT.buffer.flush()

    async def _handle_jsonrpc_request(self, data: dict[str, Any]) -> None:
        rpc_id = data.get("id")
        method = str(data.get("method") or "").strip()
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        try:
            if method == "initialize":
                await self._write_jsonrpc_result(rpc_id, self._initialize_result())
                await self._notify_agent_initialize(params)
                return
            if method == "session/new":
                await self._handle_jsonrpc_session_new(rpc_id, params)
                return
            if method == "session/prompt":
                await self._handle_jsonrpc_session_prompt(rpc_id, params)
                return
            if method == "session/cancel":
                await self._handle_jsonrpc_session_cancel(rpc_id, params)
                return
            if method == "session/list":
                await self._write_jsonrpc_result(rpc_id, {"sessions": []})
                return
            if method == "session/load":
                await self._write_jsonrpc_result(rpc_id, None)
                return
            await self._write_jsonrpc_error(rpc_id, -32601, f"Method not found: {method}")
        except ValueError as exc:
            await self._write_jsonrpc_error(rpc_id, -32602, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ACP] jsonrpc request failed: %s", exc)
            await self._write_jsonrpc_error(rpc_id, -32603, str(exc))

    async def _notify_agent_initialize(self, params: dict[str, Any]) -> None:
        msg = Message(
            id=f"acp_init_{uuid.uuid4().hex[:12]}",
            type="req",
            channel_id=self.channel_id,
            session_id=self.config.default_session_id,
            params=dict(params),
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.INITIALIZE,
            is_stream=False,
            metadata={"acp": {"method": "initialize"}},
        )
        try:
            await self._dispatch_message(msg)
        except Exception:
            logger.debug("[ACP] failed to forward initialize to gateway", exc_info=True)

    def _initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": _ACP_PROTOCOL_VERSION,
            "agentInfo": {
                "name": "jiuwenclaw",
                "title": "JiuwenClaw",
                "version": __version__,
            },
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
                "sessionCapabilities": {
                    "list": {},
                },
                "fs": {
                    "readTextFile": True,
                    "writeTextFile": True,
                },
                "terminal": {
                    "create": True,
                    "output": True,
                    "waitForExit": True,
                    "release": True,
                },
            },
            "authMethods": [],
        }

    async def _handle_jsonrpc_session_new(
        self,
        rpc_id: str | int | None,
        params: dict[str, Any],
    ) -> None:
        session_id = str(params.get("sessionId") or f"acp_{uuid.uuid4().hex[:12]}").strip()
        self._session_ctx[session_id] = dict(params)
        await self._write_jsonrpc_result(
            rpc_id,
            {
                "sessionId": session_id,
            },
        )

    async def _handle_jsonrpc_session_prompt(
        self,
        rpc_id: str | int | None,
        params: dict[str, Any],
    ) -> None:
        session_id = str(params.get("sessionId") or "").strip()
        if not session_id:
            raise ValueError("sessionId is required")

        rpc_params = dict(params)
        prompt = rpc_params.get("prompt")
        if not isinstance(prompt, list) or not prompt:
            text = self._extract_prompt_text(rpc_params)
            if not text:
                raise ValueError("prompt is required")
            rpc_params["prompt"] = [{"type": "text", "text": text}]
        rpc_params["session_id"] = session_id
        session_ctx = self._session_ctx.get(session_id)
        if isinstance(session_ctx, dict):
            for key, value in session_ctx.items():
                rpc_params.setdefault(key, value)

        env = envelope_from_acp_jsonrpc(
            method="session/prompt",
            params=rpc_params,
            jsonrpc_id=rpc_id,
            session_id=session_id,
            channel=self.channel_id,
        )
        env.request_id = f"acp_{uuid.uuid4().hex[:12]}"
        env.is_stream = True

        msg = self._envelope_to_message(env)
        self._request_ctx[msg.id] = _AcpRequestContext(
            jsonrpc_id=rpc_id,
            method=env.method,
            response_mode="jsonrpc",
            session_id=session_id,
        )
        self._active_prompt_request_by_session[session_id] = msg.id
        await self._dispatch_message(msg)

    async def _handle_jsonrpc_session_cancel(
        self,
        rpc_id: str | int | None,
        params: dict[str, Any],
    ) -> None:
        session_id = str(params.get("sessionId") or "").strip()
        if not session_id:
            raise ValueError("sessionId is required")

        msg = Message(
            id=f"acp_cancel_{uuid.uuid4().hex[:12]}",
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params={"session_id": session_id},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_CANCEL,
            is_stream=False,
            metadata={"acp": {"jsonrpc_id": rpc_id, "method": "session/cancel"}},
        )
        await self._dispatch_message(msg)
        await self._finalize_session_prompts(session_id, stop_reason="cancelled")
        await self._write_jsonrpc_result(rpc_id, None)

    async def _dispatch_message(self, msg: Message) -> None:
        handled = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(msg)
            if asyncio.iscoroutine(result):
                result = await result
            handled = bool(result)
        elif self._gateway_url:
            await self._send_to_gateway(msg)
            handled = True

        if not handled:
            publish = getattr(self.bus, "publish_user_messages", None)
            if callable(publish):
                await publish(msg)

    async def _clear_request_context(self, request_id: str) -> None:
        ctx = self._request_ctx.pop(str(request_id), None)
        if ctx is None:
            return
        if ctx.session_id and self._active_prompt_request_by_session.get(ctx.session_id) == str(request_id):
            self._active_prompt_request_by_session.pop(ctx.session_id, None)
        task = ctx.idle_finalize_task
        if task is not None:
            ctx.idle_finalize_task = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _schedule_idle_finalize(self, request_id: str, ctx: _AcpRequestContext) -> None:
        task = ctx.idle_finalize_task
        if task is not None:
            task.cancel()
        new_task = asyncio.create_task(
            self._idle_finalize_after_timeout(str(request_id)),
            name=f"acp-idle-finalize-{request_id}",
        )
        ctx.idle_finalize_task = new_task

    async def _idle_finalize_after_timeout(self, request_id: str) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(_PROMPT_IDLE_FINALIZE_SECONDS)
            ctx = self._request_ctx.get(str(request_id))
            if ctx is None or ctx.response_mode != "jsonrpc":
                return
            # Guard: verify this task is still the active idle_finalize_task
            # to prevent a superseded task from finalizing after being replaced.
            if ctx.idle_finalize_task is not current_task:
                return
            await self._write_jsonrpc_result(
                ctx.jsonrpc_id,
                {
                    "stopReason": "end_turn",
                },
            )
            await self._clear_request_context(str(request_id))
        except asyncio.CancelledError:
            return

    async def _finalize_session_prompts(self, session_id: str, *, stop_reason: str) -> None:
        matched_ids = [
            request_id
            for request_id, ctx in self._request_ctx.items()
            if ctx.response_mode == "jsonrpc" and str(ctx.session_id or "") == session_id
        ]
        for request_id in matched_ids:
            ctx = self._request_ctx.get(request_id)
            if ctx is None:
                continue
            await self._write_jsonrpc_result(
                ctx.jsonrpc_id,
                {
                    "stopReason": stop_reason,
                },
            )
            await self._clear_request_context(request_id)

    async def _send_jsonrpc_message(
        self,
        msg: Message,
        ctx: _AcpRequestContext,
    ) -> bool:
        payload = dict(msg.payload or {})
        session_id = str(msg.session_id or ctx.session_id or "")

        if msg.type == "event" and msg.event_type == EventType.CHAT_DELTA:
            text = str(payload.get("content", "") or "")
            if not text:
                return False
            update = self._build_acp_session_update(msg, payload, ctx)
            if update is None:
                return False
            await self._write_acp_session_update(session_id, update)
            # 如果 CHAT_DELTA 携带 usage，也发送 usage_update
            usage_update = build_acp_usage_update(payload)
            if usage_update is not None:
                await self._write_acp_session_update(session_id, usage_update)
            return False

        if msg.type == "event" and msg.event_type in (
            EventType.CHAT_TOOL_CALL,
            EventType.CHAT_TOOL_RESULT,
            EventType.CHAT_SUBTASK_UPDATE,
        ):
            update = self._build_acp_session_update(msg, payload, ctx)
            if update is None:
                return False
            task = ctx.idle_finalize_task
            if task is not None:
                task.cancel()
                ctx.idle_finalize_task = None
            await self._write_acp_session_update(session_id, update)
            return False

        if msg.type == "event" and msg.event_type == EventType.CHAT_FINAL:
            # ACP fallback: defer end_turn until processing stops.
            update = build_acp_final_text_update(payload, ctx)
            if update is not None:
                await self._write_acp_session_update(session_id, update)
            usage_update = build_acp_usage_update(payload)
            if usage_update is not None:
                await self._write_acp_session_update(session_id, usage_update)
            self._schedule_idle_finalize(str(msg.id), ctx)
            return False

        if msg.type == "event" and msg.event_type == EventType.CHAT_ERROR:
            task = ctx.idle_finalize_task
            if task is not None:
                task.cancel()
                ctx.idle_finalize_task = None
            await self._write_jsonrpc_error(
                ctx.jsonrpc_id,
                -32603,
                str(payload.get("error") or payload.get("content") or "Agent error"),
            )
            return True

        if msg.type == "event" and msg.event_type == EventType.CHAT_INTERRUPT_RESULT:
            task = ctx.idle_finalize_task
            if task is not None:
                task.cancel()
                ctx.idle_finalize_task = None
            await self._write_jsonrpc_result(
                ctx.jsonrpc_id,
                {
                    "stopReason": "cancelled",
                },
            )
            return True

        if msg.type == "event":
            if msg.event_type == EventType.CHAT_PROCESSING_STATUS:
                update = self._build_acp_session_update(msg, payload, ctx)
                if update is not None:
                    await self._write_acp_session_update(session_id, update)
                if payload.get("is_processing") is False:
                    task = ctx.idle_finalize_task
                    if task is not None:
                        task.cancel()
                        ctx.idle_finalize_task = None
                    await self._write_jsonrpc_result(
                        ctx.jsonrpc_id,
                        {
                            "stopReason": "end_turn",
                        },
                    )
                    return True
                return False
            return False

        if msg.type == "res" and msg.ok:
            if payload.get("accepted") is True:
                return False
            task = ctx.idle_finalize_task
            if task is not None:
                task.cancel()
                ctx.idle_finalize_task = None
            await self._write_jsonrpc_result(
                ctx.jsonrpc_id,
                {
                    "stopReason": "end_turn",
                },
            )
            return True

        await self._write_jsonrpc_error(
            ctx.jsonrpc_id,
            -32603,
            str(payload.get("error") or payload.get("content") or "request failed"),
        )
        return True

    async def _write_acp_session_update(self, session_id: str, update: dict[str, Any]) -> None:
        await self._write_jsonrpc_notification(
            "session/update",
            {
                "sessionId": session_id,
                "update": update,
            },
        )

    def _build_acp_session_update(
        self,
        msg: Message,
        payload: dict[str, Any],
        ctx: _AcpRequestContext,
    ) -> dict[str, Any] | None:
        return build_acp_session_update(msg, payload, ctx)


    @staticmethod
    def _is_jsonrpc_request(data: Any) -> bool:
        return (
            isinstance(data, dict)
            and data.get("jsonrpc") == "2.0"
            and isinstance(data.get("method"), str)
        )

    @staticmethod
    def _is_jsonrpc_response(data: Any) -> bool:
        return (
            isinstance(data, dict)
            and data.get("jsonrpc") == "2.0"
            and "id" in data
            and not isinstance(data.get("method"), str)
            and ("result" in data or "error" in data)
        )

    @staticmethod
    def _extract_prompt_text(params: dict[str, Any]) -> str:
        prompt = params.get("prompt")
        if isinstance(prompt, list):
            texts: list[str] = []
            for item in prompt:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str) and text:
                        texts.append(text)
            if texts:
                return "\n".join(texts)
        for key in ("content", "query", "text"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _parse_req_method(method: str) -> ReqMethod | None:
        for item in ReqMethod:
            if item.value == method:
                return item
        return None

    @staticmethod
    def _provenance(kind: str) -> E2AProvenance:
        return E2AProvenance(
            source_protocol=E2A_SOURCE_PROTOCOL_E2A,
            converter="jiuwenclaw.channel.acp_channel:AcpChannel",
            converted_at=utc_now_iso(),
            details={"kind": kind},
        )

    async def _ensure_gateway_connection(self) -> None:
        if self._gateway_ws is not None:
            return

        try:
            from websockets.legacy.client import connect as ws_connect
        except Exception:  # pragma: no cover
            from websockets import connect as ws_connect

        last_exc: BaseException | None = None
        for attempt in range(1, _ACP_GATEWAY_CONNECT_MAX_ATTEMPTS + 1):
            try:
                self._gateway_ws = await ws_connect(
                    self._gateway_url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=30,
                )
                self._gateway_reader_task = asyncio.create_task(
                    self._gateway_reader_loop(),
                    name="acp-gateway-reader",
                )
                if attempt > 1:
                    logger.info("[ACP] gateway connected after %d attempts: %s", attempt, self._gateway_url)
                return
            except BaseException as exc:
                last_exc = exc
                self._gateway_ws = None
                self._gateway_reader_task = None
                if attempt >= _ACP_GATEWAY_CONNECT_MAX_ATTEMPTS:
                    break
                delay = min(
                    _ACP_GATEWAY_CONNECT_BASE_DELAY_SEC * (2 ** (attempt - 1)),
                    2.0,
                )
                logger.warning(
                    "[ACP] gateway connect attempt %d/%d failed (%s); retry in %.2fs",
                    attempt,
                    _ACP_GATEWAY_CONNECT_MAX_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError(
            f"ACP gateway connect failed after {_ACP_GATEWAY_CONNECT_MAX_ATTEMPTS} attempts: {self._gateway_url}"
        ) from last_exc

    async def _close_gateway_connection(self) -> None:
        reader_task = self._gateway_reader_task
        self._gateway_reader_task = None
        if reader_task is not None:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

        ws = self._gateway_ws
        self._gateway_ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                logger.debug("[ACP] gateway websocket close ignored", exc_info=True)

    async def _send_to_gateway(self, msg: Message) -> None:
        await self._ensure_gateway_connection()
        params = dict(msg.params or {})
        if msg.session_id:
            params.setdefault("session_id", msg.session_id)
        if msg.mode is not None:
            params.setdefault("mode", msg.mode.to_runtime_mode())

        req_method = getattr(msg.req_method, "value", None)
        if not isinstance(req_method, str) or not req_method:
            raise ValueError("gateway forward requires req_method")

        frame = {
            "type": "req",
            "id": str(msg.id),
            "method": req_method,
            "params": params,
        }
        await self._gateway_ws.send(json.dumps(frame, ensure_ascii=False))

    async def _gateway_reader_loop(self) -> None:
        ws = self._gateway_ws
        if ws is None:
            return

        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.debug("[ACP] skip invalid gateway frame: %s", raw)
                    continue
                await self._handle_gateway_frame(data)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ACP] gateway reader failed: %s", exc)
        finally:
            if self._gateway_ws is ws:
                self._gateway_ws = None

    async def _handle_gateway_frame(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return

        if self._is_jsonrpc_request(data):
            await self._handle_gateway_jsonrpc_request(data)
            return

        frame_type = str(data.get("type") or "").strip()
        if frame_type == "res":
            msg = self._message_from_gateway_response(data)
        elif frame_type == "event":
            msg = self._message_from_gateway_event(data)
        else:
            msg = None

        if msg is not None:
            await self.send(msg)

    def set_pending_client_rpc_session_for_test(self, jsonrpc_id: str, session_id: str) -> None:
        """Public test helper to seed ACP client RPC session mappings."""
        self._pending_client_rpc_session_by_id[jsonrpc_id] = (session_id, time.time())

    def get_pending_client_rpc_session_for_test(self, jsonrpc_id: str) -> str | None:
        """Public test helper to inspect ACP client RPC session mappings."""
        entry = self._pending_client_rpc_session_by_id.get(jsonrpc_id)
        return entry[0] if isinstance(entry, tuple) else None

    async def handle_gateway_frame_for_test(self, data: dict[str, Any]) -> None:
        """Public test helper that delegates to gateway frame handling."""
        await self._handle_gateway_frame(data)

    async def _handle_gateway_jsonrpc_request(self, data: dict[str, Any]) -> None:
        jsonrpc_id = str(data.get("id") or "").strip()
        params = data.get("params") if isinstance(data.get("params"), dict) else {}
        session_id = str(params.get("sessionId") or params.get("session_id") or "").strip()
        if jsonrpc_id and session_id:
            self._pending_client_rpc_session_by_id[jsonrpc_id] = (session_id, time.time())
        self._sweep_stale_pending()
        _ACP_STDOUT.buffer.write((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))
        _ACP_STDOUT.buffer.flush()

    def _message_from_gateway_response(self, data: dict[str, Any]) -> Message | None:
        request_id = str(data.get("id") or "").strip()
        if not request_id:
            return None

        ctx = self._request_ctx.get(request_id)
        if ctx is None:
            return None

        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        if not bool(data.get("ok", False)):
            payload = {
                **dict(payload),
                "error": str(data.get("error") or payload.get("error") or "request failed"),
            }

        return Message(
            id=request_id,
            type="res",
            channel_id=self.channel_id,
            session_id=str(ctx.session_id or ""),
            params={},
            timestamp=time.time(),
            ok=bool(data.get("ok", False)),
            payload=dict(payload),
        )

    def _message_from_gateway_event(self, data: dict[str, Any]) -> Message | None:
        event_name = str(data.get("event") or "").strip()
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "").strip()
        request_id = self._active_prompt_request_by_session.get(session_id)
        if not request_id:
            return None

        event_type = self._parse_event_type(event_name)
        if event_type is None:
            return None

        return Message(
            id=request_id,
            type="event",
            channel_id=self.channel_id,
            session_id=session_id,
            params={},
            timestamp=time.time(),
            ok=True,
            payload=dict(payload),
            event_type=event_type,
        )

    @staticmethod
    def _parse_event_type(event_name: str) -> EventType | None:
        for item in EventType:
            if item.value == event_name:
                return item
        # ACP 兜底：AgentServer 会发送 chat.reasoning 事件，但 EventType 枚举中没有
        # 这里将其映射为 CHAT_DELTA，让后续处理能正确识别
        if event_name == "chat.reasoning":
            return EventType.CHAT_DELTA
        return None


def _load_acp_channel_config() -> AcpChannelConfig:
    from jiuwenclaw.config import get_config

    try:
        full_cfg = get_config()
        channels_cfg = full_cfg.get("channels") if isinstance(full_cfg, dict) else None
        acp_conf = channels_cfg.get("acp") if isinstance(channels_cfg, dict) else None
    except Exception:  # noqa: BLE001
        acp_conf = None

    acp_conf = acp_conf if isinstance(acp_conf, dict) else {}
    return AcpChannelConfig(
        enabled=bool(acp_conf.get("enabled", True)),
        channel_id=str(acp_conf.get("channel_id") or "acp").strip() or "acp",
        default_session_id=str(acp_conf.get("default_session_id") or "acp_cli_session").strip()
        or "acp_cli_session",
        metadata=acp_conf.get("metadata") if isinstance(acp_conf.get("metadata"), dict) else {},
    )


def load_acp_channel_config() -> AcpChannelConfig:
    return _load_acp_channel_config()


async def _run(gateway_url: str) -> None:
    acp_channel = AcpChannel(_load_acp_channel_config(), router=None, gateway_url=gateway_url)
    logger.info("[ACP] started: AcpChannel(stdio) -> Gateway(%s) -> AgentServer", gateway_url)
    try:
        await acp_channel.start()
    finally:
        await acp_channel.stop()


def main() -> None:
    # Keep ACP stdio protocol frames on the original stdout while redirecting
    # incidental process logs away from the protocol stream.
    sys.stdout = sys.stderr

    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-acp",
        description="Start JiuwenClaw ACP stdio entrypoint.",
    )
    parser.add_argument(
        "--gateway-url",
        "-g",
        default=None,
        metavar="URL",
        help="Gateway WebSocket URL (default: GATEWAY_URL or ws://WEB_HOST:WEB_PORT/WEB_PATH).",
    )
    parser.add_argument(
        "--agent-server-url",
        "-u",
        default=None,
        metavar="URL",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    gateway_host = os.getenv("GATEWAY_HOST", "127.0.0.1")
    gateway_port = os.getenv("GATEWAY_PORT", "19001")
    gateway_url = (
        getattr(args, "gateway_url", None)
        or getattr(args, "agent_server_url", None)
        or os.getenv("GATEWAY_URL")
        or f"ws://{gateway_host}:{gateway_port}/acp"
    )

    asyncio.run(_run(gateway_url))


if __name__ == "__main__":
    main()
