import json
import time

import pytest

from jiuwenclaw.app_gateway import GatewayServer, GatewayServerConfig
from jiuwenclaw.schema.message import EventType, Message, ReqMethod


class DummyBus:
    @staticmethod
    async def publish_user_messages(msg):
        return None


class FakeWebSocket:
    def __init__(self):
        self.sent_frames = []
        self.closed = False

    async def send(self, data):
        self.sent_frames.append(json.loads(data))

    async def close(self, code=None, reason=None):
        self.closed = True
        return code, reason


class GatewayServerProbe(GatewayServer):
    def bind_request_client(self, request_id: str, ws) -> None:
        self._request_to_client[request_id] = ws

    def bind_session_client(self, session_id: str, ws) -> None:
        self._session_to_client[session_id] = ws

    async def handle_raw_message_public(self, ws, raw: str) -> None:
        await self._handle_raw_message(ws, raw)


def build_server() -> GatewayServerProbe:
    config = GatewayServerConfig(
        enabled=True,
        host="127.0.0.1",
        port=19001,
        path="/acp",
        channel_id="acp",
    )
    return GatewayServerProbe(config, DummyBus())


@pytest.mark.asyncio
async def test_gateway_server_send_response_targets_request_client():
    server = build_server()
    ws = FakeWebSocket()
    server.bind_request_client("req-1", ws)

    await server.send(
        Message(
            id="req-1",
            type="res",
            channel_id="acp",
            session_id="sess-1",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={"accepted": True},
        )
    )

    assert len(ws.sent_frames) == 1
    frame = ws.sent_frames[0]
    assert frame == {
        "type": "res",
        "id": "req-1",
        "ok": True,
        "payload": {"accepted": True},
    }


@pytest.mark.asyncio
async def test_gateway_server_send_event_targets_session_client():
    server = build_server()
    ws = FakeWebSocket()
    server.bind_session_client("sess-2", ws)

    await server.send(
        Message(
            id="req-2",
            type="event",
            channel_id="acp",
            session_id="sess-2",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={"content": "hello"},
            event_type=EventType.CHAT_DELTA,
        )
    )

    assert len(ws.sent_frames) == 1
    frame = ws.sent_frames[0]
    assert frame == {
        "type": "event",
        "event": "chat.delta",
        "payload": {
            "content": "hello",
            "session_id": "sess-2",
        },
    }


@pytest.mark.asyncio
async def test_gateway_server_passthroughs_acp_output_request_as_raw_jsonrpc():
    server = build_server()
    ws = FakeWebSocket()
    server.bind_session_client("sess-tool", ws)

    await server.send(
        Message(
            id="req-tool",
            type="event",
            channel_id="acp",
            session_id="sess-tool",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "acp.output_request",
                "jsonrpc": {
                    "jsonrpc": "2.0",
                    "id": "tool-1",
                    "method": "fs/read_text_file",
                    "params": {"path": "workspace/demo.txt", "sessionId": "sess-tool"},
                },
            },
        )
    )

    assert ws.sent_frames == [
        {
            "jsonrpc": "2.0",
            "id": "tool-1",
            "method": "fs/read_text_file",
            "params": {"path": "workspace/demo.txt", "sessionId": "sess-tool"},
        }
    ]


@pytest.mark.asyncio
async def test_gateway_server_handle_raw_jsonrpc_response_forwards_acp_tool_response():
    server = build_server()
    ws = FakeWebSocket()
    server.bind_session_client("sess-tool", ws)
    seen = []

    async def on_message(msg):
        seen.append(msg)

    server.on_message(on_message)

    await server.send(
        Message(
            id="req-tool",
            type="event",
            channel_id="acp",
            session_id="sess-tool",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "event_type": "acp.output_request",
                "jsonrpc": {
                    "jsonrpc": "2.0",
                    "id": "tool-1",
                    "method": "fs/read_text_file",
                    "params": {"path": "workspace/demo.txt", "sessionId": "sess-tool"},
                },
            },
        )
    )

    await server.handle_raw_message_public(
        ws,
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "tool-1",
                "result": {"content": "hello"},
            },
            ensure_ascii=False,
        ),
    )

    assert len(seen) == 1
    msg = seen[0]
    assert msg.channel_id == "acp"
    assert msg.session_id == "sess-tool"
    assert msg.req_method == ReqMethod.ACP_TOOL_RESPONSE
    assert msg.params == {
        "jsonrpc_id": "tool-1",
        "response": {
            "jsonrpc": "2.0",
            "id": "tool-1",
            "result": {"content": "hello"},
        },
        "session_id": "sess-tool",
    }


@pytest.mark.asyncio
async def test_gateway_server_handle_raw_message_forwards_request():
    server = build_server()
    ws = FakeWebSocket()
    seen = []

    async def on_message(msg):
        seen.append(msg)

    server.on_message(on_message)

    await server.handle_raw_message_public(
        ws,
        json.dumps(
            {
                "type": "req",
                "id": "req-3",
                "method": "chat.send",
                "params": {
                    "session_id": "sess-3",
                    "content": "hello",
                    "mode": "agent",
                },
            },
            ensure_ascii=False,
        ),
    )

    assert len(seen) == 1
    msg = seen[0]
    assert msg.id == "req-3"
    assert msg.channel_id == "acp"
    assert msg.session_id == "sess-3"
    assert msg.req_method == ReqMethod.CHAT_SEND
    assert msg.params.get("content") == "hello"
    assert msg.mode.value == "agent"


@pytest.mark.asyncio
async def test_gateway_server_handle_raw_message_rejects_unknown_method():
    server = build_server()
    ws = FakeWebSocket()

    await server.handle_raw_message_public(
        ws,
        json.dumps(
            {
                "type": "req",
                "id": "req-4",
                "method": "unknown.method",
                "params": {"session_id": "sess-4"},
            },
            ensure_ascii=False,
        ),
    )

    assert len(ws.sent_frames) == 1
    frame = ws.sent_frames[0]
    assert frame["type"] == "res"
    assert frame["id"] == "req-4"
    assert frame["ok"] is False
    assert frame["error"] == "unknown method: unknown.method"
