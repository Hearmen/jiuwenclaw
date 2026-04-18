import asyncio
import json
import types

import pytest

from jiuwenclaw.agentserver import agent_ws_server as agent_ws_server_module
from jiuwenclaw.agentserver.agent_manager import ACP_DEFAULT_CAPABILITIES
from jiuwenclaw.agentserver.tools import acp_output_tools
from jiuwenclaw.agentserver.tools.acp_output_tools import AcpOutputRequest, get_acp_output_manager
from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_deep_module
from jiuwenclaw.agentserver.stream_utils import parse_stream_chunk
from jiuwenclaw.agentserver.deep_agent.interface_deep import _build_context_engineering_rail
from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class FakeAgentManager:
    def __init__(self, *, capabilities=None, session_id="sess-created", client_capabilities=None):
        self.capabilities = capabilities
        self.session_id = session_id
        self.client_capabilities = client_capabilities or {}
        self.initialize_calls = []
        self.create_session_calls = []

    async def initialize(self, channel_id="", extra_config=None):
        self.initialize_calls.append(
            {"channel_id": channel_id, "extra_config": extra_config}
        )
        return self.capabilities

    async def create_session(self, channel_id="", session_id=None):
        self.create_session_calls.append({"channel_id": channel_id, "session_id": session_id})
        return session_id or self.session_id

    def get_client_capabilities(self, channel_id=""):
        return dict(self.client_capabilities)


class FakeJiuClawContextEngineeringRail:
    def __init__(self, *, processors=None, preset=None):
        self.processors = processors
        self.preset = preset


class AgentWebSocketServerHarness(agent_ws_server_module.AgentWebSocketServer):
    def set_agent_manager_for_test(self, agent_manager):
        self._agent_manager = agent_manager

    async def handle_initialize_for_test(self, ws, request, send_lock):
        await self._handle_initialize(ws, request, send_lock)

    async def handle_session_create_for_test(self, ws, request, send_lock):
        await self._handle_session_create(ws, request, send_lock)

    async def handle_message_for_test(self, ws, raw, send_lock):
        await self._handle_message(ws, raw, send_lock)


class DeepAdapterHarness(interface_deep_module.JiuWenClawDeepAdapter):
    def build_context_engineering_rail_for_test(self, config):
        return _build_context_engineering_rail(config, "agent.plan")


def fake_encode_agent_response_for_wire(resp, response_id):
    return {
        "response_id": response_id,
        "payload": resp.payload,
        "ok": resp.ok,
    }


@pytest.fixture(autouse=True)
def _reset_acp_output_manager():
    mgr = get_acp_output_manager()
    mgr.reset_state()
    mgr.set_send_push_callback(None)
    yield
    mgr.reset_state()
    mgr.set_send_push_callback(None)


def test_interface_deep_parse_stream_chunk_preserves_tool_update():
    parsed = parse_stream_chunk(
        types.SimpleNamespace(
            type="tool_update",
            payload={
                "tool_update": {
                    "tool_call_id": "call-1",
                    "tool_name": "read_file",
                    "status": "in_progress",
                }
            },
        )
    )

    assert parsed == {
        "event_type": "chat.tool_update",
        "tool_call_id": "call-1",
        "tool_name": "read_file",
        "status": "in_progress",
    }


@pytest.mark.asyncio
async def test_handle_initialize_uses_agent_manager_capabilities(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(capabilities={"protocolVersion": "9.9.9"})
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-init",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={
            "protocolVersion": "0.1.0",
            "clientCapabilities": {"fs": {"readTextFile": True}},
        },
    )

    await server.handle_initialize_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.initialize_calls == [
        {
            "channel_id": "acp",
            "extra_config": {
                "protocol_version": "0.1.0",
                "client_capabilities": {"fs": {"readTextFile": True}},
            },
        }
    ]
    assert fake_ws.sent == [
        {
            "response_id": "req-init",
            "payload": {"protocolVersion": "9.9.9"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_initialize_falls_back_to_default_capabilities(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(capabilities=None)
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-init-default",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={},
    )

    await server.handle_initialize_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-init-default",
            "payload": ACP_DEFAULT_CAPABILITIES,
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_create_returns_session_id(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(session_id="acp_session_001")
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-session-create",
        channel_id="acp",
        req_method=ReqMethod.SESSION_CREATE,
        params={},
    )

    await server.handle_session_create_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.create_session_calls == [{"channel_id": "acp", "session_id": None}]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-create",
            "payload": {"sessionId": "acp_session_001"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_create_returns_explicit_session_id(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(session_id="unused-default")
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-session-create-explicit",
        channel_id="acp",
        req_method=ReqMethod.SESSION_CREATE,
        params={"session_id": "sess_explicit_001"},
    )

    await server.handle_session_create_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.create_session_calls == [
        {"channel_id": "acp", "session_id": "sess_explicit_001"}
    ]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-create-explicit",
            "payload": {"sessionId": "sess_explicit_001"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_acp_tool_response_completes_pending_future(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_ws = FakeWebSocket()
    mgr = get_acp_output_manager()
    future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
    mgr.add_pending_request(AcpOutputRequest(
        jsonrpc_id="42",
        method="fs/read_text_file",
        params={"path": "workspace/demo.txt"},
        future=future,
        request_id="req-pending",
    ))

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-acp-tool-response",
        channel_id="acp",
        req_method=ReqMethod.ACP_TOOL_RESPONSE,
        params={
            "jsonrpc_id": "42",
            "response": {
                "jsonrpc": "2.0",
                "id": "42",
                "result": {"content": "hello"},
            },
        },
    )

    await server.handle_acp_tool_response_for_test(fake_ws, request, asyncio.Lock())

    assert future.done() is True
    assert future.result() == {
        "jsonrpc": "2.0",
        "id": "42",
        "result": {"content": "hello"},
    }
    assert fake_ws.sent == [
        {
            "response_id": "req-acp-tool-response",
            "payload": {"accepted": True},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_acp_tool_response_unknown_id_is_soft_ignored(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-acp-tool-response-unknown",
        channel_id="acp",
        req_method=ReqMethod.ACP_TOOL_RESPONSE,
        params={
            "jsonrpc_id": "unknown-42",
            "response": {
                "jsonrpc": "2.0",
                "id": "unknown-42",
                "result": {"content": "late"},
            },
        },
    )

    await server.handle_acp_tool_response_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-acp-tool-response-unknown",
            "payload": {
                "accepted": False,
                "ignored": True,
                "reason": "unknown_or_late_response",
                "jsonrpc_id": "unknown-42",
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_message_uses_ws_scoped_acp_client_capabilities(monkeypatch):
    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(
        capabilities=ACP_DEFAULT_CAPABILITIES,
        client_capabilities={"fs": {"readTextFile": True}},
    )
    server.set_agent_manager_for_test(fake_manager)

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    init_request_a = AgentRequest(
        request_id="req-init-a",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={"clientCapabilities": {"fs": {"readTextFile": True}}},
    )
    init_request_b = AgentRequest(
        request_id="req-init-b",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={"clientCapabilities": {"terminal": {"create": True}}},
    )
    await server.handle_initialize_for_test(ws_a, init_request_a, asyncio.Lock())
    await server.handle_initialize_for_test(ws_b, init_request_b, asyncio.Lock())

    captured = {}

    async def fake_handle_session_create(ws, request, send_lock):
        captured[id(ws)] = dict(request.metadata or {})

    monkeypatch.setattr(server, "_handle_session_create", fake_handle_session_create)

    env = e2a_from_agent_fields(
        request_id="req-session-create",
        channel_id="acp",
        session_id="sess-b",
        req_method=ReqMethod.SESSION_CREATE,
        params={"session_id": "sess-b"},
        is_stream=False,
        timestamp=0.0,
    )
    await server.handle_message_for_test(ws_b, json.dumps(env.to_dict(), ensure_ascii=False), asyncio.Lock())

    assert captured[id(ws_b)]["acp_client_capabilities"] == {"terminal": {"create": True}}


@pytest.mark.asyncio
async def test_wait_for_terminal_exit_returns_soft_timeout(monkeypatch):
    mgr = get_acp_output_manager()
    captured: dict[str, object] = {}

    async def _fake_send_jsonrpc_request(
        method,
        params,
        *,
        channel_id="acp",
        session_id=None,
        timeout=0.0,
    ):
        captured["method"] = method
        captured["params"] = params
        captured["channel_id"] = channel_id
        captured["session_id"] = session_id
        captured["timeout"] = timeout
        raise asyncio.TimeoutError

    monkeypatch.setattr(mgr, "send_jsonrpc_request", _fake_send_jsonrpc_request)
    monkeypatch.setattr(acp_output_tools, "_ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS", 123.0)

    result = await acp_output_tools.wait_for_terminal_exit("term-soft-timeout", session_id="sess-soft")

    assert captured == {
        "method": "terminal/wait_for_exit",
        "params": {"terminalId": "term-soft-timeout"},
        "channel_id": "acp",
        "session_id": "sess-soft",
        "timeout": 123.0,
    }
    assert result == {
        "exitCode": None,
        "signal": None,
        "timedOut": True,
        "running": True,
        "shouldRetry": True,
    }


@pytest.mark.asyncio
async def test_wait_for_terminal_exit_completed_result_sets_should_retry_false(monkeypatch):
    mgr = get_acp_output_manager()

    async def _fake_send_jsonrpc_request(
        method,
        params,
        *,
        channel_id="acp",
        session_id=None,
        timeout=0.0,
    ):
        return {
            "jsonrpc": "2.0",
            "id": "ok-1",
            "result": {"exitCode": 0, "signal": None},
        }

    monkeypatch.setattr(mgr, "send_jsonrpc_request", _fake_send_jsonrpc_request)

    result = await acp_output_tools.wait_for_terminal_exit("term-done", session_id="sess-done")

    assert result == {
        "exitCode": 0,
        "signal": None,
        "timedOut": False,
        "running": False,
        "shouldRetry": False,
    }


def test_build_context_engineering_rail_uses_summary_offloader_config(monkeypatch):
    monkeypatch.setattr(
        interface_deep_module,
        "JiuClawContextEngineeringRail",
        FakeJiuClawContextEngineeringRail,
    )
    adapter = DeepAdapterHarness()

    rail = adapter.build_context_engineering_rail_for_test(
        {
            "context_engine_config": {
                "message_summary_offloader_config": {
                    "tokens_threshold": 5000,
                    "keep_last_round": False,
                },
                "dialogue_compressor_config": {"tokens_threshold": 100000},
            }
        }
    )

    assert isinstance(rail, FakeJiuClawContextEngineeringRail)
    assert rail.preset is True
    assert rail.processors == [
        (
            "MessageSummaryOffloader",
            {
                "tokens_threshold": 5000,
                "keep_last_round": False,
            },
        ),
        ("DialogueCompressor", {"tokens_threshold": 100000}),
    ]


def test_build_context_engineering_rail_prefers_summary_offloader_config(monkeypatch):
    monkeypatch.setattr(
        interface_deep_module,
        "JiuClawContextEngineeringRail",
        FakeJiuClawContextEngineeringRail,
    )
    adapter = DeepAdapterHarness()

    rail = adapter.build_context_engineering_rail_for_test(
        {
            "context_engine_config": {
                "message_summary_offloader_config": {
                    "tokens_threshold": 6000,
                },
                "message_offloader_config": {
                    "tokens_threshold": 5000,
                },
            }
        }
    )

    assert isinstance(rail, FakeJiuClawContextEngineeringRail)
    assert rail.processors == [
        ("MessageSummaryOffloader", {"tokens_threshold": 6000}),
    ]
