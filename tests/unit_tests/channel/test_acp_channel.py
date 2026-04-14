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


class AcpChannelHarness(AcpChannel):
    async def send_jsonrpc_message_for_test(self, msg, ctx):
        return await self._send_jsonrpc_message(msg, ctx)


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
    channel = AcpChannelHarness(AcpChannelConfig(enabled=True), DummyBus())

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
        # 发送思考过程 (CHAT_DELTA with reasoning)
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
        # 发送最终回复 (CHAT_DELTA with text)
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
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
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
    assert len(responses) == 4
    thought_update = responses[0].get("params")
    message_chunk = responses[1].get("params")
    idle_update = responses[2].get("params")
    result = responses[3].get("result")

    assert isinstance(thought_update, dict)
    assert thought_update.get("sessionId") == "sess-2"
    update_one = thought_update.get("update")
    assert isinstance(update_one, dict)
    assert update_one.get("sessionUpdate") == "agent_thought_chunk"

    assert isinstance(message_chunk, dict)
    update_two = message_chunk.get("update")
    assert isinstance(update_two, dict)
    assert update_two.get("sessionUpdate") == "agent_message_chunk"

    assert isinstance(idle_update, dict)
    update_three = idle_update.get("update")
    assert isinstance(update_three, dict)
    assert update_three.get("sessionUpdate") == "session_info_update"
    assert update_three.get("status") == "idle"

    assert isinstance(result, dict)
    assert responses[3].get("id") == 3
    assert result.get("stopReason") == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_accepts_text_param(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 301,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-text",
                        "text": "hello from text",
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
        # 发送最终回复 (CHAT_DELTA)
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
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    assert len(seen) == 1
    assert seen[0].req_method == ReqMethod.CHAT_SEND
    assert seen[0].session_id == "sess-text"
    assert seen[0].params.get("query") == "hello from text"

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 3
    message_chunk = responses[0].get("params")
    idle_update = responses[1].get("params")
    result = responses[2].get("result")

    assert isinstance(message_chunk, dict)
    update_one = message_chunk.get("update")
    assert isinstance(update_one, dict)
    assert update_one.get("sessionUpdate") == "agent_message_chunk"

    assert isinstance(idle_update, dict)
    update_two = idle_update.get("update")
    assert isinstance(update_two, dict)
    assert update_two.get("sessionUpdate") == "session_info_update"
    assert update_two.get("status") == "idle"

    assert isinstance(result, dict)
    assert responses[2].get("id") == 301
    assert result.get("stopReason") == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_final_text_as_agent_message_chunk(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 302,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-final-only",
                        "text": "hello from final only",
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
                payload={"content": "final answer from chat.final"},
                event_type=EventType.CHAT_FINAL,
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
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 3
    assert responses[0]["method"] == "session/update"
    assert responses[0]["params"]["sessionId"] == "sess-final-only"
    assert responses[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert responses[0]["params"]["update"]["content"] == {
        "type": "text",
        "text": "final answer from chat.final",
    }
    assert responses[1]["params"]["update"] == {
        "sessionUpdate": "session_info_update",
        "status": "idle",
    }
    assert responses[2] == {
        "jsonrpc": "2.0",
        "id": 302,
        "result": {"stopReason": "end_turn"},
    }


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
        # 发送最终回复 (CHAT_DELTA)
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
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
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
async def test_jsonrpc_session_prompt_auto_finalizes_after_idle_after_chat_final(monkeypatch):
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
                payload={"content": "final answer"},
                event_type=EventType.CHAT_FINAL,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 2
    # 第一个响应是 agent_message_chunk
    assert responses[0].get("method") == "session/update"
    message_chunk = responses[0].get("params")
    assert isinstance(message_chunk, dict)
    assert message_chunk.get("update").get("sessionUpdate") == "agent_message_chunk"
    
    # 第二个响应是 idle finalize 触发的 end_turn
    result = responses[1].get("result")
    assert isinstance(result, dict)
    assert responses[1].get("id") == 12
    assert result.get("stopReason") == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_does_not_auto_finalize_from_delta_only(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 121,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-delta-only",
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
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._STDIN_EOF_GRACE_SECONDS", 0.05)

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
        # delta-only 场景不会自动 end_turn；显式停止通道，避免测试等待未完成请求而超时。
        await channel.stop()

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 1
    update = responses[0]["params"]["update"]
    assert update["sessionUpdate"] == "agent_message_chunk"
    assert isinstance(update.get("messageId"), str)
    assert update["content"] == {"type": "text", "text": "partial answer"}


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
            # 注意：cancel 会立即触发 finalize，不需要等待 is_processing=false

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    prompt_result = next((item for item in responses if item.get("id") == 20), None)
    cancel_result = next((item for item in responses if item.get("id") == 21), None)

    assert isinstance(prompt_result, dict)
    assert isinstance(prompt_result.get("result"), dict)
    assert prompt_result["result"].get("stopReason") == "cancelled"
    assert cancel_result == {"jsonrpc": "2.0", "id": 21, "result": None}


@pytest.mark.asyncio
async def test_jsonrpc_response_is_forwarded_as_acp_tool_response(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": "tool-1",
                    "result": {"content": "from client"},
                }
            )
        ]
    )
    fake_stdout = FakeStdout()
    channel = AcpChannelHarness(AcpChannelConfig(enabled=True), DummyBus())
    seen = []

    channel.set_pending_client_rpc_session_for_test("tool-1", "sess-tool")

    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)

    async def _on_message(msg):
        seen.append(msg)

    channel.on_message(_on_message)
    await channel.start()

    assert len(seen) == 1
    msg = seen[0]
    assert msg.req_method == ReqMethod.ACP_TOOL_RESPONSE
    assert msg.session_id == "sess-tool"
    assert msg.params["jsonrpc_id"] == "tool-1"
    assert msg.params["response"]["result"] == {"content": "from client"}


@pytest.mark.asyncio
async def test_gateway_jsonrpc_request_is_written_to_stdout(monkeypatch):
    fake_stdout = FakeStdout()
    channel = AcpChannelHarness(AcpChannelConfig(enabled=True), DummyBus())

    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)

    await channel.handle_gateway_frame_for_test(
        {
            "jsonrpc": "2.0",
            "id": "tool-2",
            "method": "fs/read_text_file",
            "params": {"path": "workspace/demo.txt", "sessionId": "sess-tool"},
        }
    )

    responses = fake_stdout.buffer.json_lines()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": "tool-2",
            "method": "fs/read_text_file",
            "params": {"path": "workspace/demo.txt", "sessionId": "sess-tool"},
        }
    ]
    assert channel.get_pending_client_rpc_session_for_test("tool-2") == "sess-tool"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_tool_call_update(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 30,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-tool-call",
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

    async def _on_message(msg):
        # 发送工具调用
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={
                    "tool_call": {
                        "name": "read_file",
                        "arguments": {"path": "demo.txt"},
                        "tool_call_id": "tool-call-1",
                    }
                },
                event_type=EventType.CHAT_TOOL_CALL,
            )
        )
        # 发送最终回复 (CHAT_DELTA)
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
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 4
    assert responses[0]["params"]["update"] == {
        "sessionUpdate": "tool_call",
        "toolCall": {
            "id": "tool-call-1",
            "name": "read_file",
            "arguments": {"path": "demo.txt"},
        },
    }
    
    message_chunk = responses[1]["params"]["update"]
    assert message_chunk["sessionUpdate"] == "agent_message_chunk"
    
    idle_update = responses[2]["params"]["update"]
    assert idle_update["sessionUpdate"] == "session_info_update"
    assert idle_update["status"] == "idle"
    
    assert responses[3]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_tool_result_update(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 31,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-tool-result",
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

    async def _on_message(msg):
        # 发送工具结果
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={
                    "tool_name": "read_file",
                    "tool_call_id": "tool-call-2",
                    "result": "file contents",
                },
                event_type=EventType.CHAT_TOOL_RESULT,
            )
        )
        # 发送最终回复 (CHAT_DELTA)
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
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 4
    assert responses[0]["params"]["update"] == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": "tool-call-2",
        "toolName": "read_file",
        "result": "file contents",
    }
    
    message_chunk = responses[1]["params"]["update"]
    assert message_chunk["sessionUpdate"] == "agent_message_chunk"
    
    idle_update = responses[2]["params"]["update"]
    assert idle_update["sessionUpdate"] == "session_info_update"
    assert idle_update["status"] == "idle"
    
    assert responses[3]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_plan_update(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 32,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-plan",
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

    async def _on_message(msg):
        # 发送子任务更新
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={
                    "session_id": msg.session_id,
                    "description": "并行执行两个任务",
                    "status": "running",
                    "index": 1,
                    "total": 2,
                    "result": "已启动后台会话",
                    "is_parallel": True,
                },
                event_type=EventType.CHAT_SUBTASK_UPDATE,
            )
        )
        # 发送最终回复 (CHAT_DELTA)
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
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 4
    update = responses[0]["params"]["update"]
    assert update["sessionUpdate"] == "plan"
    assert update["plan"]["description"] == "并行执行两个任务"
    assert update["plan"]["is_parallel"] is True
    
    message_chunk = responses[1]["params"]["update"]
    assert message_chunk["sessionUpdate"] == "agent_message_chunk"
    
    idle_update = responses[2]["params"]["update"]
    assert idle_update["sessionUpdate"] == "session_info_update"
    assert idle_update["status"] == "idle"
    
    assert responses[3]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_processing_status_update(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 33,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-processing",
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
                payload={"is_processing": True},
                event_type=EventType.CHAT_PROCESSING_STATUS,
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
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 3
    assert responses[0]["params"]["update"] == {
        "sessionUpdate": "session_info_update",
        "status": "processing",
    }
    assert responses[1]["params"]["update"] == {
        "sessionUpdate": "session_info_update",
        "status": "idle",
    }
    assert responses[2]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_jsonrpc_session_prompt_emits_usage_update_before_result(monkeypatch):
    fake_stdin = FakeStdin(
        [
            json_line(
                {
                    "jsonrpc": "2.0",
                    "id": 34,
                    "method": "session/prompt",
                    "params": {
                        "sessionId": "sess-usage",
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

    async def _on_message(msg):
        # 发送 usage 信息 (通过 CHAT_DELTA 携带 usage)
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={
                    "content": "done",
                    "usage": {
                        "inputTokens": 12,
                        "outputTokens": 34,
                    },
                },
                event_type=EventType.CHAT_DELTA,
            )
        )
        # 发送 is_processing=false 触发最终响应
        await channel.send(
            Message(
                id=msg.id,
                type="event",
                channel_id="acp",
                session_id=msg.session_id,
                params={},
                timestamp=time.time(),
                ok=True,
                payload={"is_processing": False},
                event_type=EventType.CHAT_PROCESSING_STATUS,
            )
        )

    channel.on_message(_on_message)
    await channel.start()

    responses = fake_stdout.buffer.json_lines()
    assert len(responses) == 4
    assert responses[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert responses[1]["params"]["update"] == {
        "sessionUpdate": "usage_update",
        "usage": {
            "inputTokens": 12,
            "outputTokens": 34,
        },
    }
    
    idle_update = responses[2]["params"]["update"]
    assert idle_update["sessionUpdate"] == "session_info_update"
    assert idle_update["status"] == "idle"
    
    assert responses[3] == {
        "jsonrpc": "2.0",
        "id": 34,
        "result": {"stopReason": "end_turn"},
    }


@pytest.mark.asyncio
async def test_processing_status_true_does_not_schedule_idle_finalize(monkeypatch):
    fake_stdout = FakeStdout()
    channel = AcpChannelHarness(AcpChannelConfig(enabled=True), DummyBus())
    ctx = types.SimpleNamespace(
        jsonrpc_id=35,
        method="session/prompt",
        response_mode="jsonrpc",
        session_id="sess-processing-only",
        assistant_message_id=None,
        thought_message_id=None,
        sequence=0,
        idle_finalize_task=None,
    )

    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)

    is_final = await channel.send_jsonrpc_message_for_test(
        Message(
            id="req-processing-only",
            type="event",
            channel_id="acp",
            session_id="sess-processing-only",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={"is_processing": True},
            event_type=EventType.CHAT_PROCESSING_STATUS,
        ),
        ctx,
    )

    responses = fake_stdout.buffer.json_lines()
    assert is_final is False
    assert responses == [
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess-processing-only",
                "update": {
                    "sessionUpdate": "session_info_update",
                    "status": "processing",
                },
            },
        }
    ]
    assert ctx.idle_finalize_task is None


@pytest.mark.asyncio
async def test_tool_events_cancel_idle_finalize_instead_of_scheduling(monkeypatch):
    import asyncio

    fake_stdout = FakeStdout()
    channel = AcpChannelHarness(AcpChannelConfig(enabled=True), DummyBus())

    sentinel_future = asyncio.get_event_loop().create_future()
    sentinel_task = asyncio.ensure_future(sentinel_future)
    ctx = types.SimpleNamespace(
        jsonrpc_id=36,
        method="session/prompt",
        response_mode="jsonrpc",
        session_id="sess-tool-idle",
        assistant_message_id=None,
        thought_message_id=None,
        sequence=0,
        idle_finalize_task=sentinel_task,
    )

    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("jiuwenclaw.channel.acp_channel._ACP_STDOUT", fake_stdout)

    is_final = await channel.send_jsonrpc_message_for_test(
        Message(
            id="req-tool-idle",
            type="event",
            channel_id="acp",
            session_id="sess-tool-idle",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={
                "tool_call": {
                    "name": "terminal_create",
                    "arguments": {"cmd": "ls"},
                    "tool_call_id": "tc-idle-1",
                }
            },
            event_type=EventType.CHAT_TOOL_CALL,
        ),
        ctx,
    )

    assert is_final is False
    assert ctx.idle_finalize_task is None
    assert sentinel_task.cancelled()
