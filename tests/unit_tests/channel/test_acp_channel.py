import json
from argparse import Namespace
import sys
import time
import types
from collections import deque

import pytest

from jiuwenclaw.channel.acp_channel import AcpChannel, AcpChannelConfig
from jiuwenclaw.schema.message import EventType, Message, ReqMethod


class DummyBus:
    @staticmethod
    async def publish_user_messages(msg):
        return None


class FakeStdinBuffer:
    def __init__(self, lines):
        self.lines = deque([(line + "\n").encode("utf-8") for line in lines])

    def readline(self):
        if self.lines:
            return self.lines.popleft()
        return b""


class FakeStdin:
    def __init__(self, lines):
        self.buffer = FakeStdinBuffer(lines)


class FakeStdoutBuffer:
    def __init__(self):
        self.parts = []

    def write(self, data):
        self.parts.append(data)

    @staticmethod
    def flush():
        return None

    def json_lines(self):
        raw = b"".join(self.parts).decode("utf-8")
        return [json.loads(line) for line in raw.splitlines() if line.strip()]


class FakeStdout:
    def __init__(self):
        self.buffer = FakeStdoutBuffer()


def json_line(payload):
    return json.dumps(payload, ensure_ascii=False)


def _import_acp_channel_entry(monkeypatch: pytest.MonkeyPatch):
    import jiuwenclaw.channel.acp_channel as existing_module

    fake_config_module = types.ModuleType("jiuwenclaw.config")

    def get_default_config():
        return {}

    fake_config_module.get_config = get_default_config
    monkeypatch.setitem(sys.modules, "jiuwenclaw.config", fake_config_module)
    return existing_module


def test_load_acp_channel_config_uses_defaults(monkeypatch: pytest.MonkeyPatch):
    module = _import_acp_channel_entry(monkeypatch)

    conf = module.load_acp_channel_config()

    assert conf.enabled is True
    assert conf.channel_id == "acp"
    assert conf.default_session_id == "acp_cli_session"
    assert conf.metadata == {}


def test_load_acp_channel_config_reads_channels_acp(monkeypatch: pytest.MonkeyPatch):
    module = _import_acp_channel_entry(monkeypatch)

    fake_config_module = types.ModuleType("jiuwenclaw.config")

    def get_custom_config():
        return {
            "channels": {
                "acp": {
                    "enabled": True,
                    "channel_id": "acp_custom",
                    "default_session_id": "sess_custom",
                    "metadata": {"source": "ut"},
                }
            }
        }

    fake_config_module.get_config = get_custom_config
    monkeypatch.setitem(sys.modules, "jiuwenclaw.config", fake_config_module)

    conf = module.load_acp_channel_config()

    assert conf.enabled is True
    assert conf.channel_id == "acp_custom"
    assert conf.default_session_id == "sess_custom"
    assert conf.metadata == {"source": "ut"}


def test_main_passes_explicit_agent_server_url(monkeypatch: pytest.MonkeyPatch):
    import jiuwenclaw.channel.acp_channel as module

    captured = {}
    original_stdout = sys.stdout

    def parse_args(_self):
        return Namespace(agent_server_url="ws://127.0.0.1:19001")

    def fake_run(url):
        captured["url"] = url
        return url

    def fake_asyncio_run(result):
        captured["result"] = result
        return result

    monkeypatch.setattr(
        "argparse.ArgumentParser.parse_args",
        parse_args,
    )
    monkeypatch.setattr(module, "_run", fake_run)
    monkeypatch.setattr("asyncio.run", fake_asyncio_run)

    try:
        module.main()
        assert sys.stdout is sys.stderr
    finally:
        sys.stdout = original_stdout

    assert captured["url"] == "ws://127.0.0.1:19001"
    assert captured["result"] == "ws://127.0.0.1:19001"


@pytest.mark.asyncio
async def test_jsonrpc_initialize_and_session_new(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json_line({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"sessionId": "sess-1"}}),
        ]
    )
    fake_stdout = FakeStdout()
    channel = AcpChannel(AcpChannelConfig(enabled=True), DummyBus())

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)

    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 2

    init_result = responses[0].get("result")
    assert isinstance(init_result, dict)
    assert responses[0].get("id") == 1
    assert init_result.get("protocolVersion") == 1
    agent_info = init_result.get("agentInfo")
    assert isinstance(agent_info, dict)
    assert agent_info.get("name") == "jiuwenclaw"

    new_result = responses[1].get("result")
    assert isinstance(new_result, dict)
    assert responses[1].get("id") == 2
    assert new_result.get("sessionId") == "sess-1"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_updates_and_final_result(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-2",
                        "prompt": [{"type": "text", "text": "hello"}],
                    },
                }
            )
        ]
    )
    fake_stdout = FakeStdout()
    channel = AcpChannel(AcpChannelConfig(enabled=True), DummyBus())
    seen = []

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)

    async def _on_message(msg):
        seen.append(msg)
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"content": "thinking", "source_chunk_type": "llm_reasoning"},
                event_type=EventType.CHAT_DELTA,
            )
        )
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"content": "final answer"},
                event_type=EventType.CHAT_FINAL,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    assert len(seen) == 1
    req = seen[0]
    assert req.req_method == ReqMethod.CHAT_SEND
    assert req.session_id == "sess-2"
    assert req.params.get("query") == "hello"

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 3
    thought_update = responses[0].get("params")
    final_chunk = responses[1].get("params")
    result = responses[2].get("result")

    assert isinstance(thought_update, dict)
    assert thought_update.get("sessionId") == "sess-2"
    update_one = thought_update.get("update")
    assert isinstance(update_one, dict)
    assert update_one.get("sessionUpdate") == "agent_thought_chunk"

    assert isinstance(final_chunk, dict)
    update_two = final_chunk.get("update")
    assert isinstance(update_two, dict)
    assert update_two.get("sessionUpdate") == "agent_message_chunk"

    assert isinstance(result, dict)
    assert responses[2].get("id") == 3
    assert result.get("stopReason") == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_merges_session_context(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 10,
                    "method": "session/new",
                    "params": {
                        "sessionId": "sess-ctx",
                        "cwd": "D:/workspace/demo",
                    },
                }
            ),
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-ctx",
                        "prompt": [{"type": "text", "text": "hello"}],
                    },
                }
            ),
        ]
    )
    fake_stdout = FakeStdout()
    channel = AcpChannel(AcpChannelConfig(enabled=True), DummyBus())
    seen = []

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._STDIN_EOF_GRACE_SECONDS", 0.01)

    async def _on_message(msg):
        seen.append(msg)
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"content": "done"},
                event_type=EventType.CHAT_FINAL,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    assert len(seen) == 1
    req = seen[0]
    assert req.session_id == "sess-ctx"
    assert req.params.get("cwd") == "D:/workspace/demo"
    assert req.params.get("query") == "hello"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_auto_finalizes_after_idle(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 12,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-idle",
                        "prompt": [{"type": "text", "text": "hello"}],
                    },
                }
            )
        ]
    )
    fake_stdout = FakeStdout()
    channel = AcpChannel(AcpChannelConfig(enabled=True), DummyBus())

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._PROMPT_IDLE_FINALIZE_SECONDS", 0.01)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._STDIN_EOF_GRACE_SECONDS", 0.01)

    async def _on_message(msg):
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"content": "partial answer"},
                event_type=EventType.CHAT_DELTA,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 2
    assert responses[0].get("method") == "session/update"
    result = responses[1].get("result")
    assert isinstance(result, dict)
    assert responses[1].get("id") == 12
    assert result.get("stopReason") == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_cancel_finalizes_active_prompt(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 20,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-cancel",
                        "prompt": [{"type": "text", "text": "hello"}],
                    },
                }
            ),
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 21,
                    "method": "session/cancel",
                    "params": {
                        "sessionId": "sess-cancel",
                    },
                }
            ),
        ]
    )
    fake_stdout = FakeStdout()
    channel = AcpChannel(AcpChannelConfig(enabled=True), DummyBus())

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._STDIN_EOF_GRACE_SECONDS", 0.01)

    async def _on_message(msg):
        if msg.req_method == ReqMethod.CHAT_SEND:
            await channel.send(
                Message(
                    id=msg.id,
                    type="event",
                    channel_id="acp",
                    session_id=msg.session_id,
                    params={},
                    timestamp=time.time(),
                    ok=True,
                    payload={"content": "still running"},
                    event_type=EventType.CHAT_DELTA,
                )
            )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    prompt_result = next((item for item in responses if item.get("id") == 20), None)
    cancel_result = next((item for item in responses if item.get("id") == 21), None)

    assert isinstance(prompt_result, dict)
    assert isinstance(prompt_result.get("result"), dict)
    assert prompt_result["result"].get("stopReason") == "cancelled"
    assert cancel_result == {"jsonrpc": "2.0", "id": 21, "result": None}
