# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
ACP 输出工具：AgentServer 向 IDE 发送请求。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, List, TYPE_CHECKING

from jiuwenclaw.e2a.constants import (
    E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_SOURCE_PROTOCOL_E2A,
    E2A_WIRE_SERVER_PUSH_KEY,
)
from jiuwenclaw.e2a.models import (
    E2A_PROTOCOL_VERSION,
    E2AProvenance,
    E2AResponse,
    IdentityOrigin,
    utc_now_iso,
)

if TYPE_CHECKING:
    from openjiuwen.core.foundation.tool import Tool

logger = logging.getLogger(__name__)

_ACP_REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass
class AcpOutputRequest:
    jsonrpc_id: str
    method: str
    params: dict[str, Any]
    future: asyncio.Future[dict[str, Any]]
    request_id: str


class AcpOutputManager:
    _instance: AcpOutputManager | None = None
    _pending: dict[str, AcpOutputRequest]
    _send_push_callback: Any
    _initialized: bool

    def __new__(cls) -> AcpOutputManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            self._pending: dict[str, AcpOutputRequest] = {}
            self._send_push_callback = None
            self._initialized = True

    def set_send_push_callback(self, callback: Any) -> None:
        self._send_push_callback = callback

    async def send_jsonrpc_request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        channel_id: str = "acp",
        session_id: str | None = None,
        timeout: float = _ACP_REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        if self._send_push_callback is None:
            raise RuntimeError("ACP output send_push callback not set")

        jsonrpc_id = uuid.uuid4().hex[:8]
        request_id = f"acp_out_{uuid.uuid4().hex[:12]}"

        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_event_loop().create_future()
        )
        acp_req = AcpOutputRequest(
            jsonrpc_id=jsonrpc_id,
            method=method,
            params=params,
            future=future,
            request_id=request_id,
        )
        self._pending[jsonrpc_id] = acp_req

        ts = utc_now_iso()
        prov = E2AProvenance(
            source_protocol=E2A_SOURCE_PROTOCOL_E2A,
            converter="jiuwenclaw.agentserver.tools.acp_output:send_jsonrpc_request",
            converted_at=ts,
            details={"kind": "acp_output_request", "acp_method": method},
        )

        e2a_response = E2AResponse(
            protocol_version=E2A_PROTOCOL_VERSION,
            response_id=f"acp_out_resp_{uuid.uuid4().hex[:12]}",
            request_id=request_id,
            sequence=0,
            is_final=False,
            status=E2A_RESPONSE_STATUS_IN_PROGRESS,
            response_kind=E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
            timestamp=ts,
            provenance=prov,
            body={
                "jsonrpc": "2.0",
                "id": jsonrpc_id,
                "method": method,
                "params": params,
            },
            jsonrpc_id=jsonrpc_id,
            session_id=session_id,
            channel=channel_id,
            identity_origin=IdentityOrigin.AGENT,
            metadata={E2A_WIRE_SERVER_PUSH_KEY: True},
        )

        push_msg = e2a_response.to_dict()

        try:
            self._send_push_callback(push_msg)
        except Exception as exc:
            self._pending.pop(jsonrpc_id, None)
            raise RuntimeError(f"Failed to send ACP output request: {exc}") from exc

        logger.info(
            "[AcpOutput] sent E2A request: jsonrpc_id=%s method=%s request_id=%s",
            jsonrpc_id,
            method,
            request_id,
        )

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(jsonrpc_id, None)
            logger.warning(
                "[AcpOutput] request timed out: jsonrpc_id=%s method=%s timeout=%.1fs",
                jsonrpc_id,
                method,
                timeout,
            )
            raise

    def handle_response(self, jsonrpc_id: str, response: dict[str, Any]) -> None:
        req = self._pending.pop(jsonrpc_id, None)
        if req is None:
            logger.warning(
                "[AcpOutput] no pending request for jsonrpc_id=%s", jsonrpc_id
            )
            return

        req.future.set_result(response)
        logger.info(
            "[AcpOutput] response received: jsonrpc_id=%s method=%s",
            jsonrpc_id,
            req.method,
        )

    def cancel_all(self) -> None:
        for jsonrpc_id, req in list(self._pending.items()):
            req.future.cancel()
            self._pending.pop(jsonrpc_id, None)
            logger.info(
                "[AcpOutput] cancelled pending request: jsonrpc_id=%s method=%s",
                jsonrpc_id,
                req.method,
            )


def get_acp_output_manager() -> AcpOutputManager:
    return AcpOutputManager()


class AcpOutputError(Exception):
    def __init__(self, method: str, code: int, message: str, data: Any = None):
        self.method = method
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{method}] error {code}: {message}")


async def _acp_request(
    method: str,
    params: dict[str, Any],
    *,
    channel_id: str = "acp",
    session_id: str | None = None,
    timeout: float = _ACP_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    mgr = get_acp_output_manager()
    response = await mgr.send_jsonrpc_request(
        method, params, channel_id=channel_id, session_id=session_id, timeout=timeout
    )
    if "error" in response:
        err = response["error"]
        raise AcpOutputError(
            method=method,
            code=err.get("code", -32000),
            message=err.get("message", "Unknown error"),
            data=err.get("data"),
        )
    return response.get("result", {})


async def acp_read_text_file(
    path: str,
    *,
    offset: int | None = None,
    limit: int | None = None,
    **kwargs,
) -> dict[str, Any]:
    params: dict[str, Any] = {"path": path}
    if offset is not None:
        params["offset"] = offset
    if limit is not None:
        params["limit"] = limit
    return await _acp_request("fs/read_text_file", params, **kwargs)


async def acp_write_text_file(path: str, content: str, **kwargs) -> dict[str, Any]:
    return await _acp_request(
        "fs/write_text_file", {"path": path, "content": content}, **kwargs
    )


async def acp_terminal_create(
    cmd: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    **kwargs,
) -> dict[str, Any]:
    params: dict[str, Any] = {"cmd": cmd}
    if cwd is not None:
        params["cwd"] = cwd
    if env is not None:
        params["env"] = env
    return await _acp_request("terminal/create", params, **kwargs)


async def acp_terminal_send_text(
    terminal_id: str, text: str, **kwargs
) -> dict[str, Any]:
    return await _acp_request(
        "terminal/send_text", {"terminalId": terminal_id, "text": text}, **kwargs
    )


async def acp_terminal_read_output(terminal_id: str, **kwargs) -> dict[str, Any]:
    return await _acp_request("terminal/output", {"terminalId": terminal_id}, **kwargs)


async def acp_terminal_release(terminal_id: str, **kwargs) -> dict[str, Any]:
    return await _acp_request("terminal/release", {"terminalId": terminal_id}, **kwargs)


async def acp_terminal_wait_for_exit(terminal_id: str, **kwargs) -> dict[str, Any]:
    return await _acp_request(
        "terminal/wait_for_exit", {"terminalId": terminal_id}, **kwargs
    )


async def acp_terminal_kill(terminal_id: str, **kwargs) -> dict[str, Any]:
    return await _acp_request("terminal/kill", {"terminalId": terminal_id}, **kwargs)


def get_tools(session_id: str = "", request_id: str = "") -> List["Tool"]:
    from openjiuwen.core.foundation.tool import Tool, ToolCard, LocalFunction

    def make_tool(name: str, description: str, input_params: dict, func) -> Tool:
        card = ToolCard(
            id=f"{name}_{session_id}_{request_id}",
            name=name,
            description=description,
            input_params=input_params,
        )
        return LocalFunction(card=card, func=func)

    return [
        make_tool(
            name="read_text_file",
            description="请求 IDE 读取文件内容。当需要读取用户本地文件时使用此工具。",
            input_params={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要读取的文件路径"},
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（可选，从 1 开始）",
                    },
                    "limit": {"type": "integer", "description": "读取行数（可选）"},
                },
                "required": ["path"],
            },
            func=acp_read_text_file,
        ),
        make_tool(
            name="write_text_file",
            description="请求 IDE 写入文件。当需要写入文件到用户本地时使用此工具。",
            input_params={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要写入的文件路径"},
                    "content": {"type": "string", "description": "要写入的文件内容"},
                },
                "required": ["path", "content"],
            },
            func=acp_write_text_file,
        ),
        make_tool(
            name="create_terminal",
            description="请求 IDE 创建终端并执行命令。当需要在用户本地执行命令时使用此工具。",
            input_params={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "要执行的命令"},
                    "cwd": {"type": "string", "description": "工作目录（可选）"},
                },
                "required": ["cmd"],
            },
            func=acp_terminal_create,
        ),
        make_tool(
            name="send_text_to_terminal",
            description="向终端发送文本输入（如回答 yes/no 确认）。",
            input_params={
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "终端 ID"},
                    "text": {"type": "string", "description": "要发送的文本"},
                },
                "required": ["terminal_id", "text"],
            },
            func=acp_terminal_send_text,
        ),
        make_tool(
            name="read_terminal_output",
            description="读取终端输出内容。",
            input_params={
                "type": "object",
                "properties": {
                    "terminal_id": {"type": "string", "description": "终端 ID"},
                },
                "required": ["terminal_id"],
            },
            func=acp_terminal_read_output,
        ),
    ]
