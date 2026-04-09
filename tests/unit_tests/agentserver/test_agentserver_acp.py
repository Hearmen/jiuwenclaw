import asyncio
import json

import pytest

from jiuwenclaw.agentserver import agent_ws_server as agent_ws_server_module
from jiuwenclaw.agentserver.agent_manager import ACP_DEFAULT_CAPABILITIES
from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_deep_module
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class FakeAgentManager:
    def __init__(self, *, capabilities=None, session_id="sess-created"):
        self.capabilities = capabilities
        self.session_id = session_id
        self.initialize_calls = []
        self.create_session_calls = []

    async def initialize(self, channel_id="", extra_config=None):
        self.initialize_calls.append(
            {"channel_id": channel_id, "extra_config": extra_config}
        )
        return self.capabilities

    async def create_session(self, channel_id=""):
        self.create_session_calls.append(channel_id)
        return self.session_id


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


class DeepAdapterHarness(interface_deep_module.JiuWenClawDeepAdapter):
    def build_context_engineering_rail_for_test(self, config):
        return self._build_context_engineering_rail(config)


def fake_encode_agent_response_for_wire(resp, response_id):
    return {
        "response_id": response_id,
        "payload": resp.payload,
        "ok": resp.ok,
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

    assert fake_manager.create_session_calls == ["acp"]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-create",
            "payload": {"sessionId": "acp_session_001"},
            "ok": True,
        }
    ]


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
