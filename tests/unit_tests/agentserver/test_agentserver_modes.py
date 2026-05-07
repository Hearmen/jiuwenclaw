import asyncio
import json

import pytest

from jiuwenclaw.server import agent_ws_server as agent_ws_server_module
from jiuwenclaw.common.schema.agent import AgentRequest, AgentResponseChunk


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class AgentWebSocketServerHarness(agent_ws_server_module.AgentWebSocketServer):
    async def handle_stream_for_test(self, ws, request, send_lock):
        await self._handle_stream(ws, request, send_lock)


def fake_encode_agent_chunk_for_wire(chunk, response_id, sequence):
    return {
        "response_id": response_id,
        "sequence": sequence,
        "payload": chunk.payload,
        "is_complete": chunk.is_complete,
    }


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        ("team", ("team", None, "team")),
        ("agent", ("agent", "plan", "agent.plan")),
        ("code", ("code", "normal", "code.normal")),
        ("agent.fast", ("agent", "fast", "agent.fast")),
        ("code.plan", ("code", "plan", "code.plan")),
        (None, ("agent", "plan", "agent.plan")),
    ],
)
def test_resolve_agent_request_mode_accepts_primary_and_dotted_modes(raw_mode, expected):
    assert agent_ws_server_module.resolve_agent_request_mode(raw_mode) == expected


def test_handle_stream_accepts_team_mode_without_sub_mode(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.seen_request = None

        async def process_message_stream(self, request):
            self.seen_request = request
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"event_type": "chat.done"},
                is_complete=True,
            )

    class FakeAgentManager:
        def __init__(self):
            self.agent = FakeAgent()
            self.calls = []

        async def get_agent(self, channel_id, mode, project_dir=None, sub_mode=None):
            self.calls.append(
                {
                    "channel_id": channel_id,
                    "mode": mode,
                    "project_dir": project_dir,
                    "sub_mode": sub_mode,
                }
            )
            return self.agent

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_chunk_for_wire",
        fake_encode_agent_chunk_for_wire,
    )

    async def run_case():
        server = AgentWebSocketServerHarness()
        fake_manager = FakeAgentManager()
        monkeypatch.setattr(server.get_agent_manager(), "get_agent", fake_manager.get_agent)
        fake_ws = FakeWebSocket()
        request = AgentRequest(
            request_id="req-team",
            channel_id="feishu",
            params={"mode": "team", "query": "hello"},
            is_stream=True,
        )

        await server.handle_stream_for_test(fake_ws, request, asyncio.Lock())
        return fake_manager, fake_ws, request

    fake_manager, fake_ws, request = asyncio.run(run_case())

    assert fake_manager.calls == [
        {
            "channel_id": "feishu",
            "mode": "team",
            "project_dir": None,
            "sub_mode": None,
        }
    ]
    assert fake_manager.agent.seen_request is request
    assert request.params["mode"] == "team"
    assert fake_ws.sent == [
        {
            "response_id": "req-team",
            "sequence": 0,
            "payload": {"event_type": "chat.done"},
            "is_complete": True,
        }
    ]
