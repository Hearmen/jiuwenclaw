from __future__ import annotations

import uuid
from typing import Any, Protocol

from jiuwenclaw.schema.message import EventType, Message


class AcpSessionUpdateState(Protocol):
    assistant_message_id: str | None
    thought_message_id: str | None


def _ensure_assistant_message_id(state: AcpSessionUpdateState) -> str:
    if not state.assistant_message_id:
        state.assistant_message_id = f"msg_{uuid.uuid4().hex[:12]}"
    return str(state.assistant_message_id)


def _ensure_thought_message_id(state: AcpSessionUpdateState) -> str:
    if not state.thought_message_id:
        state.thought_message_id = f"thought_{uuid.uuid4().hex[:12]}"
    return str(state.thought_message_id)


def build_acp_session_update(
    msg: Message,
    payload: dict[str, Any],
    state: AcpSessionUpdateState,
) -> dict[str, Any] | None:
    event_type = msg.event_type
    if event_type == EventType.CHAT_DELTA:
        text = str(payload.get("content", "") or "")
        if not text:
            return None
        source_chunk_type = str(payload.get("source_chunk_type") or "")
        payload_event_type = str(payload.get("event_type") or "")
        is_reasoning = (
            source_chunk_type == "llm_reasoning"
            or payload_event_type == "chat.reasoning"
        )

        if is_reasoning:
            return {
                "sessionUpdate": "agent_thought_chunk",
                "messageId": _ensure_thought_message_id(state),
                "content": {"type": "text", "text": text},
            }

        return {
            "sessionUpdate": "agent_message_chunk",
            "messageId": _ensure_assistant_message_id(state),
            "content": {"type": "text", "text": text},
        }

    if event_type == EventType.CHAT_TOOL_CALL:
        tool_call = payload.get("tool_call")
        if not isinstance(tool_call, dict):
            return None
        tool_call_id = str(
            tool_call.get("tool_call_id")
            or tool_call.get("toolCallId")
            or tool_call.get("id")
            or ""
        )
        return {
            "sessionUpdate": "tool_call",
            "toolCall": {
                "id": tool_call_id,
                "name": str(tool_call.get("name") or ""),
                "arguments": tool_call.get("arguments", {}),
            },
        }

    if event_type == EventType.CHAT_TOOL_RESULT:
        tool_call_id = str(payload.get("tool_call_id") or payload.get("toolCallId") or "")
        tool_name = str(payload.get("tool_name") or payload.get("name") or "")
        result: Any = payload.get("result")
        if result is None:
            result = payload.get("content", "")
        update = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_call_id,
            "result": result,
        }
        if tool_name:
            update["toolName"] = tool_name
        return update

    if event_type == EventType.CHAT_SUBTASK_UPDATE:
        return {
            "sessionUpdate": "plan",
            "plan": dict(payload),
        }

    if event_type == EventType.CHAT_PROCESSING_STATUS:
        return {
            "sessionUpdate": "session_info_update",
            "status": "processing" if bool(payload.get("is_processing", True)) else "idle",
        }

    return None


def build_acp_final_text_update(
    payload: dict[str, Any],
    state: AcpSessionUpdateState,
) -> dict[str, Any] | None:
    text = str(payload.get("content", "") or "")
    if not text:
        return None

    if str(payload.get("event_type") or "") == "chat.reasoning":
        return {
            "sessionUpdate": "agent_thought_chunk",
            "messageId": _ensure_thought_message_id(state),
            "content": {"type": "text", "text": text},
        }

    if state.assistant_message_id:
        return None

    return {
        "sessionUpdate": "agent_message_chunk",
        "messageId": _ensure_assistant_message_id(state),
        "content": {"type": "text", "text": text},
    }


def build_acp_usage_update(payload: dict[str, Any]) -> dict[str, Any] | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict) or not usage:
        return None
    return {
        "sessionUpdate": "usage_update",
        "usage": dict(usage),
    }
