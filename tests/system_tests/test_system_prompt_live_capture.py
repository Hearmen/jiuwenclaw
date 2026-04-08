#!/usr/bin/env python3
"""System test: live capture the final system prompt sent to the LLM.

Walks the full runtime path:
    create_instance() → process_message_impl() → Runner.run_agent()
    → rails before_model_call → LLM_INPUT callback captures messages

Reads real ~/.jiuwenclaw/config/config.yaml for model configuration.
Outputs:
    tests/system_tests/output/system_prompt_live_capture.json
    tests/system_tests/output/system_prompt_live_capture.txt
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import LLMCallEvents

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.interface import build_user_prompt
from jiuwenclaw.config import get_config
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod

OUTPUT_DIR = Path(__file__).parent / "output"
logger = logging.getLogger(__name__)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_json_safe(item) for item in value]
        return str(value)


def _extract_system_messages(messages: list[dict[str, Any]]) -> list[str]:
    results: list[str] = []
    for item in messages:
        if item.get("role") != "system":
            continue
        content = item.get("content", "")
        if isinstance(content, str):
            results.append(content)
        else:
            results.append(json.dumps(content, ensure_ascii=False, indent=2))
    return results


class PromptCapture:
    """Callback handler that captures LLM_INPUT events."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def on_llm_input(
        self,
        *,
        model_name: str | None = None,
        model_provider: Any = None,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: Any = None,
        top_p: Any = None,
        max_tokens: Any = None,
    ) -> None:
        payload_messages = _json_safe(messages or [])
        self.events.append(
            {
                "model_name": model_name,
                "model_provider": str(model_provider) if model_provider is not None else None,
                "messages": payload_messages,
                "system_messages": _extract_system_messages(payload_messages),
                "tools": _json_safe(tools or []),
                "tool_count": len(tools or []),
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
            }
        )


@pytest.fixture()
async def prompt_capture():
    """Register LLM_INPUT callback, yield capture, then clean up."""
    capture = PromptCapture()
    callback = capture.on_llm_input
    Runner.callback_framework.register_sync(
        LLMCallEvents.LLM_INPUT,
        callback,
        namespace="system_test_prompt_capture",
        priority=1000,
    )
    yield capture
    await Runner.callback_framework.unregister(LLMCallEvents.LLM_INPUT, callback)


def _write_outputs(result: dict[str, Any]) -> None:
    """Write JSON and plain-text output files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_path = OUTPUT_DIR / "system_prompt_live_capture.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    txt_path = OUTPUT_DIR / "system_prompt_live_capture.txt"
    lines: list[str] = []
    events = result.get("captured_events") or []
    for i, event in enumerate(events, 1):
        lines.append(f"{'=' * 80}")
        lines.append(f"Model Call #{i}")
        lines.append(f"Model: {event.get('model_name')}")
        lines.append(f"Provider: {event.get('model_provider')}")
        lines.append(f"Tool count: {event.get('tool_count', 0)}")
        lines.append(f"{'=' * 80}")
        lines.append("")
        for j, sys_msg in enumerate(event.get("system_messages") or [], 1):
            lines.append(f"--- System Message #{j} ---")
            lines.append(sys_msg)
            lines.append("")
    txt_path.write_text("\n".join(lines), encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM_TESTS", "").lower() not in ("1", "true", "yes"),
    reason="需要真实大模型，设置 RUN_LIVE_LLM_TESTS=1 手动运行",
)
async def test_live_capture_system_prompt(prompt_capture: PromptCapture):
    """Live-capture the full system prompt through the real runtime path."""
    config_base = get_config()

    query = "只回复 PONG。不要调用任何工具。不要解释。"
    lang = config_base.get("preferred_language", "zh")
    channel = "web"
    mode = "agent"
    session_id = f"{channel}_prompt_capture_{uuid.uuid4().hex[:8]}"
    request_id = f"prompt-capture-{uuid.uuid4().hex[:8]}"

    request = AgentRequest(
        request_id=request_id,
        channel_id=channel,
        session_id=session_id,
        req_method=ReqMethod.CHAT_SEND,
        params={"query": query, "mode": mode, "files": {}},
        is_stream=False,
        metadata={"source": "system_test_live_capture"},
    )

    inputs = {
        "conversation_id": session_id,
        "query": build_user_prompt(query, files={}, channel=channel, language=lang),
        "channel": channel,
        "language": lang,
    }

    result: dict[str, Any] = {
        "request": {
            "request_id": request_id,
            "session_id": session_id,
            "channel_id": channel,
            "mode": mode,
            "lang": lang,
            "query": query,
        },
        "captured_events": [],
        "response": None,
        "error": None,
    }

    async def _noop_checkpoint():
        pass

    async def _noop_load_user_rails(self_):
        pass

    with mock.patch.object(
        JiuWenClawDeepAdapter, "set_checkpoint", staticmethod(_noop_checkpoint)
    ), mock.patch.object(
        JiuWenClawDeepAdapter, "load_user_rails", _noop_load_user_rails
    ), mock.patch.dict("os.environ", {"BROWSER_RUNTIME_MCP_ENABLED": "true"}):
        adapter = JiuWenClawDeepAdapter()
        try:
            await adapter.create_instance()
            response = await adapter.process_message_impl(request, inputs)
            result["response"] = {
                "ok": response.ok,
                "payload": _json_safe(response.payload),
            }
        except Exception as exc:
            result["error"] = repr(exc)

    result["captured_events"] = prompt_capture.events
    _write_outputs(result)

    # Assertions
    assert result["error"] is None, f"运行时出错: {result['error']}"
    assert len(prompt_capture.events) >= 1, "未捕获到任何 LLM_INPUT 事件"
    first_event = prompt_capture.events[0]
    assert len(first_event["system_messages"]) > 0, "system messages 为空"

    logger.info("捕获到 %d 个 model call 事件", len(prompt_capture.events))
    logger.info("第一个事件的 system message 长度: %d 字符", sum(len(m) for m in first_event['system_messages']))
    logger.info("输出文件: %s", OUTPUT_DIR / 'system_prompt_live_capture.txt')
