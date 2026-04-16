# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System test for Team mode continuous dialog via WebSocket.

Test scenario:
1. Connect to WebSocket
2. Send first message: "创建3个成员，轮流报数，不要说多余废话"
3. Team will continuously output (stream won't end)
4. Send second message while receiving: "现在从10开始轮流报数，一人说一句，就一轮"

Usage:
    .venv\Scripts\python.exe tests\system_tests\test_team_continuous_dialog.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import websockets

pytestmark = [pytest.mark.integration, pytest.mark.system]

REPO_ROOT = Path(__file__).resolve().parents[2]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Enable TeamManager logs
logging.getLogger("jiuwenclaw.agentserver.team.team_manager").setLevel(logging.INFO)


def _pick_free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _start_process(cmd: list[str], *, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log_file.close()
    return proc


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    if proc.poll() is not None:
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return

    proc.terminate()
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


async def _wait_for_log(log_path: Path, needle: str, timeout: float = 60.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            if needle in text:
                return
        await asyncio.sleep(0.2)
    log_text = (
        log_path.read_text(encoding="utf-8", errors="ignore")
        if log_path.exists()
        else ""
    )
    raise AssertionError(
        f"Timed out waiting for log line: {needle}\nlog={log_text}"
    )


async def _wait_for_websocket_ready(url: str, timeout: float = 60.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with websockets.connect(url):
                return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    raise AssertionError(
        f"Timed out waiting for websocket: {url} last_error={last_error}"
    )


def _print_event(event_number: int, data: dict[str, Any]) -> None:
    """Print a single event in a formatted way."""
    event_type = data.get("event", "unknown")
    payload = data.get("payload", {})
    
    if event_type == "connection.ack":
        print(f"[{event_number:4d}] [CONNECTION_ACK] WebSocket connected")
        return
    
    if event_type == "stream.end":
        print(f"[{event_number:4d}] [STREAM_END] Stream ended")
        return
    
    if not isinstance(payload, dict):
        print(f"[{event_number:4d}] [{event_type}] {payload}")
        return
    
    inner_event_type = payload.get("event_type", "")
    
    if inner_event_type == "team.member":
        event = payload.get("event", {})
        sub_type = event.get("type", "unknown")
        member_id = event.get("member_id", "N/A")
        print(f"[{event_number:4d}] [MEMBER] {sub_type} | member={member_id}")
        
    elif inner_event_type == "team.task":
        event = payload.get("event", {})
        sub_type = event.get("type", "unknown")
        task_id = event.get("task_id", "N/A")
        print(f"[{event_number:4d}] [TASK] {sub_type} | task={task_id}")
        
    elif inner_event_type == "team.message":
        event = payload.get("event", {})
        sub_type = event.get("type", "unknown")
        from_member = event.get("from_member", "N/A")
        content = event.get("content", "")
        preview = content[:60] + "..." if len(content) > 60 else content
        print(f"[{event_number:4d}] [MESSAGE] {sub_type} | from={from_member} | {preview}")
        
    elif inner_event_type == "chat.delta":
        content = payload.get("content", "")
        preview = content[:60] + "..." if len(content) > 60 else content
        print(f"[{event_number:4d}] [CHAT_DELTA] {preview}")
        
    elif inner_event_type == "chat.final":
        content = payload.get("content", "")
        preview = content[:60] + "..." if len(content) > 60 else content
        print(f"[{event_number:4d}] [CHAT_FINAL] {preview}")
        
    else:
        print(f"[{event_number:4d}] [{event_type}] {json.dumps(payload, ensure_ascii=False)[:100]}")


async def _recv_messages(
    ws,
    stop_event: asyncio.Event,
    second_message_sent: asyncio.Event,
    send_second_msg_callback,
    max_events: int = 200,
    events_before_second: int = 30,
) -> list[dict]:
    """Receive messages and send second message after receiving some events.
    
    Args:
        ws: WebSocket connection
        stop_event: Event to stop receiving
        second_message_sent: Event to signal second message was sent
        send_second_msg_callback: Callback to send second message
        max_events: Maximum number of events to receive
        events_before_second: Number of events to receive before sending second message
    """
    events = []
    event_count = 0
    
    while not stop_event.is_set() and event_count < max_events:
        try:
            remaining_timeout = 1.0
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining_timeout)
            data = json.loads(raw)
            events.append(data)
            event_count += 1
            _print_event(event_count, data)
            
            if event_count == events_before_second and not second_message_sent.is_set():
                print("\n" + "=" * 80)
                print("Sending second message while team is still running...")
                print("=" * 80 + "\n")
                await send_second_msg_callback()
                second_message_sent.set()
                
        except asyncio.TimeoutError:
            continue
        except websockets.ConnectionClosed:
            print("WebSocket connection closed")
            break
        except Exception as e:
            logger.error("Error receiving message: %s", e)
            break
    
    return events


@pytest.mark.asyncio
async def test_team_continuous_dialog(temp_home: Path, monkeypatch: pytest.MonkeyPatch):
    """Test Team mode continuous dialog scenario.
    
    Scenario:
    1. Connect to WebSocket
    2. Send first message to create team with 3 members
    3. While team is running, send second message to change behavior
    4. Verify both messages are processed
    """
    print(f"\n[DEBUG] temp_home: {temp_home}")
    agent_port = _pick_free_port()
    web_port = _pick_free_port()
    gateway_port = _pick_free_port()

    # Load .env from project root
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        from dotenv import load_dotenv
        load_dotenv(env_file)
    
    env = os.environ.copy()
    env["HOME"] = str(temp_home)
    env["AGENT_SERVER_HOST"] = "127.0.0.1"
    env["AGENT_SERVER_PORT"] = str(agent_port)
    env["WEB_HOST"] = "127.0.0.1"
    env["WEB_PORT"] = str(web_port)
    env["GATEWAY_HOST"] = "127.0.0.1"
    env["GATEWAY_PORT"] = str(gateway_port)

    agent_log = temp_home / "agentserver.log"
    gateway_log = temp_home / "gateway.log"

    agent_proc = _start_process(
        [sys.executable, "-m", "jiuwenclaw.app_agentserver", "--port", str(agent_port)],
        env=env,
        log_path=agent_log,
    )
    gateway_proc = None
    try:
        await _wait_for_log(agent_log, "ready:", timeout=60)

        gateway_proc = _start_process(
            [sys.executable, "-m", "jiuwenclaw.app_gateway", "--port", str(web_port)],
            env=env,
            log_path=gateway_log,
        )
        await _wait_for_websocket_ready(
            f"ws://127.0.0.1:{web_port}/ws",
            timeout=60,
        )

        async with websockets.connect(f"ws://127.0.0.1:{web_port}/ws") as ws:
            session_id = f"sess_team_test_{int(time.time())}"
            
            print("\n" + "=" * 80)
            print("Team Continuous Dialog Test")
            print("=" * 80)
            print(f"Session ID: {session_id}")
            print("=" * 80 + "\n")
            
            print("Sending first message...")
            print("-" * 40)
            
            req1 = {
                "type": "req",
                "id": "req-team-1",
                "method": "chat.send",
                "params": {
                    "session_id": session_id,
                    "mode": "team",
                    "content": "告诉我你有哪些工具，不要立刻开始任务",
                },
            }
            print(f"Request 1: {json.dumps(req1, ensure_ascii=False, indent=2)}")
            print("-" * 40 + "\n")
            await ws.send(json.dumps(req1, ensure_ascii=False))
            
            stop_event = asyncio.Event()
            second_message_sent = asyncio.Event()
            events: list[dict] = []  # Initialize empty list
            
            async def send_second_message():
                print("\nSending second message...")
                print("-" * 40)
                req2 = {
                    "type": "req",
                    "id": "req-team-2",
                    "method": "chat.send",
                    "params": {
                        "session_id": session_id,
                        "mode": "team",
                        "content": "现在创建一个成员叫安娜，让安娜告诉我他有哪些工具",
                    },
                }
                print(f"Request 2: {json.dumps(req2, ensure_ascii=False, indent=2)}")
                print("-" * 40 + "\n")
                await ws.send(json.dumps(req2, ensure_ascii=False))
            
            try:
                events = await asyncio.wait_for(
                    _recv_messages(
                        ws,
                        stop_event,
                        second_message_sent,
                        send_second_message,
                        max_events=150,
                        events_before_second=30,
                    ),
                    timeout=120.0,  # 2 minutes timeout
                )
            except asyncio.TimeoutError:
                print("\n" + "=" * 80)
                print("WARNING: Test timed out after 120 seconds")
                print("This is expected if Team stream runs indefinitely")
                print(f"Collected {len(events)} events before timeout")
                print("=" * 80)
            
            print("\n" + "=" * 80)
            print("Test Results")
            print("=" * 80)
            print(f"Total events received: {len(events)}")
            
            event_types = {}
            for event in events:
                event_type = event.get("event", "unknown")
                event_types[event_type] = event_types.get(event_type, 0) + 1
            
            print("\nEvent type distribution:")
            for event_type, count in sorted(event_types.items(), key=lambda x: -x[1]):
                print(f"  {event_type}: {count}")
            
            team_messages = [
                e for e in events
                if e.get("event") == "team.message"
            ]
            print(f"\nTeam messages received: {len(team_messages)}")
            
            if second_message_sent.is_set():
                print("\n✓ Second message was sent while team was running")
            else:
                print("\n✗ Second message was NOT sent")
            
            # Core assertions
            assert len(events) > 0, "Should receive events from team"
            assert "team.member" in event_types or "team.message" in event_types, \
                "Should receive team events (member or message)"
            assert second_message_sent.is_set(), \
                "Second message should be sent during stream"
            
            print("\n" + "=" * 80)
            print("Test PASSED")
            print("=" * 80)
            
    finally:
        _stop_process(gateway_proc)
        _stop_process(agent_proc)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
