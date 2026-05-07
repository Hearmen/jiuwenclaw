# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawStreamEventRail — Stream event emission, pause checks, context fix.

Migrated from JiuClawReActAgent:
  - _emit_tool_call / _emit_tool_result / _emit_todo_updated / _emit_context_compression
  - _fix_incomplete_tool_context
  - Pause checkpoint logic
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import tiktoken
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ToolMessage,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import BaseAgent
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.schema.task import TodoStatus
from openjiuwen.harness.tools import TodoListTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenclaw.common.utils import logger

_TODO_TOOL_NAMES = frozenset(["todo_create", "todo_list", "todo_modify"])


def _structured_tool_result_payload(result: Any) -> Any | None:
    if isinstance(result, (dict, list)):
        return result
    return None


class JiuClawStreamEventRail(DeepAgentRail):
    """Emit frontend stream events and enforce pause/abort checkpoints.

    Pause/abort state is owned by this Rail (not DeepAgent) so that
    interface.py can call rail.pause() / rail.resume() / rail.abort()
    without requiring changes to DeepAgent.
    """

    priority = 80

    def __init__(self) -> None:
        super().__init__()
        self._deep_agent: Optional[Any] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._abort_requested = False
        self._conversation_id: str = ""
        self._stream_tasks: set[asyncio.Task] = set()

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    # -- pause / resume / abort API for interface.py --

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._abort_requested = False
        self._pause_event.set()

    def abort(self) -> None:
        self._abort_requested = True
        self._pause_event.set()

    def reset_abort(self) -> None:
        self._abort_requested = False

    # ------------------------------------------------------------------
    # before_invoke (Outer event on DeepAgent): capture conversation_id
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, InvokeInputs):
            self._conversation_id = ctx.inputs.conversation_id or ""

    # ------------------------------------------------------------------
    # before_model_call: pause check + context fix + compression info
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._pause_event.wait()
        if self._abort_requested:
            raise asyncio.CancelledError("Agent abort requested")

        if ctx.context is not None:
            await self._fix_incomplete_tool_context(ctx.context)

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._emit_context_compression(ctx)

    # ------------------------------------------------------------------
    # before_tool_call: pause check + emit tool_call event
    # ------------------------------------------------------------------

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        await self._pause_event.wait()
        if self._abort_requested:
            raise asyncio.CancelledError("Agent abort requested")

        session = ctx.session
        if session is not None and isinstance(ctx.inputs, ToolCallInputs):
            tc = ctx.inputs.tool_call
            await self._emit_tool_call(session, tc)
            await self._emit_tool_update(session, tc, status="in_progress")

    # ------------------------------------------------------------------
    # after_tool_call: emit tool_result + todo.updated
    # ------------------------------------------------------------------

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None or not isinstance(ctx.inputs, ToolCallInputs):
            return

        await self._emit_tool_result(session, ctx.inputs.tool_call, ctx.inputs.tool_result)

        tool_name = ctx.inputs.tool_name
        if tool_name in _TODO_TOOL_NAMES and self._conversation_id:
            await self._emit_todo_updated(ctx.agent, session, self._conversation_id)

    # ------------------------------------------------------------------
    # on_model_exception: attempt context repair
    # ------------------------------------------------------------------

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        if ctx.context is not None:
            logger.info("[StreamEventRail] Attempting context repair after model exception")
            await self._fix_incomplete_tool_context(ctx.context)

    # ------------------------------------------------------------------
    # Private helpers (migrated from JiuClawReActAgent)
    # ------------------------------------------------------------------

    @staticmethod
    async def _emit_tool_call(session: Session, tool_call: Any) -> None:
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_call",
                    index=0,
                    payload={
                        "tool_call": {
                            "name": getattr(tool_call, "name", ""),
                            "arguments": getattr(tool_call, "arguments", {}),
                            "tool_call_id": getattr(tool_call, "id", ""),
                        }
                    },
                )
            )
        except Exception:
            logger.debug("tool_call emit failed", exc_info=True)

    @staticmethod
    async def _emit_tool_result(session: Session, tool_call: Any, result: Any) -> None:
        try:
            raw_output = _structured_tool_result_payload(result)
            tool_result_payload = {
                "tool_name": getattr(tool_call, "name", "") if tool_call else "",
                "tool_call_id": getattr(tool_call, "id", "") if tool_call else "",
                "result": str(result)[:60000] if result is not None else "",
            }
            if raw_output is not None:
                tool_result_payload["raw_output"] = raw_output
            await session.write_stream(
                OutputSchema(
                    type="tool_result",
                    index=0,
                    payload={
                        "tool_result": tool_result_payload
                    },
                )
            )
        except Exception:
            logger.debug("tool_result emit failed", exc_info=True)

    @staticmethod
    async def _emit_tool_update(session: Session, tool_call: Any, *, status: str) -> None:
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_update",
                    index=0,
                    payload={
                        "tool_update": {
                            "tool_name": getattr(tool_call, "name", "") if tool_call else "",
                            "tool_call_id": getattr(tool_call, "id", "") if tool_call else "",
                            "arguments": getattr(tool_call, "arguments", {}) if tool_call else {},
                            "status": str(status or "").strip() or "in_progress",
                        }
                    },
                )
            )
        except Exception:
            logger.debug("tool_update emit failed", exc_info=True)

    async def _emit_todo_updated(
        self, agent: BaseAgent, session: Session, session_id: str
    ) -> None:
        """Emit todo list update event to frontend.

        Loads current todos using TodoListTool, maps internal status to
        frontend-compatible values, and emits a 'todo.updated' stream event.

        Args:
            agent: The agent instance to access ability_manager for tool lookup.
            session: Session object for writing stream events.
            session_id: Session ID used to locate the todo JSON file.
        """
        todo_tool = self._get_todo_tool(agent)
        if todo_tool is None:
            logger.debug("[StreamEventRail] TodoListTool not available")
            return

        try:
            todos_data = await todo_tool.load_todos(session_id)
        except Exception as exc:
            logger.debug(
                "[StreamEventRail] Failed to load todos: %s", exc
            )
            return

        if not todos_data:
            return

        todos = self._format_todos_for_frontend(todos_data)

        try:
            await session.write_stream(
                OutputSchema(
                    type="todo.updated",
                    index=0,
                    payload={"todos": todos},
                )
            )
        except Exception:
            logger.debug("todo.updated emit failed", exc_info=True)

    def _get_todo_tool(self, agent: BaseAgent) -> TodoListTool | None:
        """Get TodoListTool from agent's ability_manager or create new instance.

        First attempts to retrieve the registered tool from the agent's
        ability_manager and Runner's resource_mgr. If not found, falls back
        to creating a new TodoListTool instance with rail's workspace config.

        Args:
            agent: The agent instance to access ability_manager.

        Returns:
            TodoListTool instance or None if unavailable.
        """
        # Try to get registered tool from agent's ability_manager
        try:
            tool_card = agent.ability_manager.get("todo_list")
            registered_tool = Runner.resource_mgr.get_tool(tool_card.id)
            if isinstance(registered_tool, TodoListTool):
                return registered_tool
        except Exception:
            pass

        # Fallback: create new tool instance
        try:
            language = getattr(
                getattr(self._deep_agent, "system_prompt_builder", None),
                "language", "cn",
            ) or "cn"
            agent_id = self._deep_agent.card.id if self._deep_agent else None 
            return TodoListTool(
                operation=self.sys_operation,
                workspace=str(self.workspace.get_node_path(WorkspaceNode.TODO)),
                language=language,
                agent_id=agent_id
            )
        except Exception as exc:
            logger.debug(
                "[StreamEventRail] Failed to create TodoListTool: %s", exc
            )
            return None

    @staticmethod
    def _format_todos_for_frontend(
        todos_data: List[Any],
    ) -> List[dict[str, Any]]:
        """Format todo items for frontend compatibility.

        Maps internal TodoStatus values to frontend-compatible status strings.
        Cancelled status is mapped to 'pending' for frontend compatibility.

        Args:
            todos_data: List of TodoItem objects from TodoListTool.

        Returns:
            List of formatted todo dictionaries.
        """
        status_mapping = {
            TodoStatus.PENDING: "pending",
            TodoStatus.IN_PROGRESS: "in_progress",
            TodoStatus.COMPLETED: "completed",
            TodoStatus.CANCELLED: "pending",
        }

        return [
            {
                "id": item.id,
                "content": item.content,
                "activeForm": item.activeForm,
                "status": status_mapping.get(item.status, item.status.value),
            }
            for item in todos_data
        ]

    @staticmethod
    async def _emit_context_compression(ctx: AgentCallbackContext) -> None:
        """Emit context compression stats based on raw_total_tokens and current context tokens."""
        _model_token = {

            "glm-5": 200000,
            "glm-4-long": 200000,
            "glm-4": 128000,
            "glm-4-9b-chat-1m": 1048576,

            # OpenAI GPT
            "gpt-5.4": 1100000,
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-4-turbo": 128000,
            "gpt-3.5-turbo": 16384,

            # DeepSeek
            "deepseek-v3": 128000,
            "deepseek-chat": 65536,

            # Anthropic Claude
            "claude-opus-4.6": 1000000,
            "claude-sonnet-4.6": 1000000,
            "claude-haiku-4.6": 200000,

            # Google Gemini
            "gemini-3.1-pro": 2000000,
            "gemini-2.5-pro": 1000000,
            "gemini-2.5-flash": 1000000,

            # Meta Llama (开源)
            "llama-4-maverick": 1000000,
            "llama-4-scout": 10000000,
        }
        session = ctx.session
        if session is None:
            return

        context = ctx.context
        if context is None:
            return

        model_name = None
        try:
            agent = ctx.agent
            if agent is not None:
                config = getattr(agent, '_config', None)
                if config is not None:
                    model_name = getattr(config, 'model_name', None)
        except Exception:
            logger.debug("Failed to get model_name from ctx.agent", exc_info=True)

        try:
            # raw_total_tokens: model max context window
            raw_total_tokens = _model_token.get(model_name, 0)

            # current_context_tokens: actual usage from usage_metadata
            response = ctx.inputs.response
            usage_metadata = {}
            if response and hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage_metadata = response.usage_metadata.model_dump()
            current_context_tokens = usage_metadata.get("total_tokens", 0) if isinstance(usage_metadata, dict) else 0

            if raw_total_tokens != 0:
                rate = current_context_tokens / raw_total_tokens * 100
            else:
                rate = 0

            await session.write_stream(
                OutputSchema(
                    type="context.compressed",
                    index=0,
                    payload={
                        "rate": rate,
                        "before_compressed": raw_total_tokens,
                        "after_compressed": current_context_tokens,
                    },
                )
            )
        except Exception:
            logger.debug("context_compression emit failed", exc_info=True)

    def _ensure_json_arguments(self, arguments: Any) -> str:
        """Ensure tool call arguments are valid JSON string.

        If arguments is a dict, convert to JSON string. If arguments is a string,
        attempt multi-stage repair (json_repair, rule-based quote fixing) before
        returning valid JSON. If all repair attempts fail, return empty JSON object.

        Args:
            arguments: The arguments value from tool_call.

        Returns:
            Valid JSON string (e.g., '{"key": "value"}').
        """
        import json
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False)
        if isinstance(arguments, str):
            _arguments = arguments.strip()
            if not _arguments:
                return "{}"

            # First attempt: direct parsing
            try:
                json.loads(_arguments)
                return arguments
            except json.JSONDecodeError:
                pass

            # Second attempt: json_repair library
            try:
                import json_repair
                repaired = json_repair.loads(_arguments)
                if isinstance(repaired, dict):
                    logger.info(
                        "[_ensure_json_arguments] stage=json_repair outcome=success."
                    )
                    return json.dumps(repaired, ensure_ascii=False)
                # json_repair returned non-dict (e.g., list, str, int)
                logger.warning(
                    "[_ensure_json_arguments] stage=json_repair outcome=failed."
                )
            except Exception as exc:
                logger.warning(
                    "[_ensure_json_arguments] stage=json_repair, error=%s",
                    str(exc),
                )

            # Third attempt: rule-based quote fixing
            fixed = self._fix_missing_quotes(_arguments)
            if fixed != _arguments:
                try:
                    result = json.loads(fixed)
                    logger.info(
                        "[_ensure_json_arguments] stage=rule_fix outcome=success"
                    )
                    return json.dumps(result, ensure_ascii=False)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "[_ensure_json_arguments] stage=rule_fix outcome=failed, error=%s",
                        str(exc),
                    )
            else:
                # rule_fix made no structural change
                logger.warning(
                    "[_ensure_json_arguments] stage=rule_fix outcome=failed"
                )

            logger.warning(
                "[_ensure_json_arguments] outcome=failed_all_stages"
            )
            return "{}"
        return "{}"

    @staticmethod
    def _fix_missing_quotes(json_str: str) -> str:
        """Attempt to fix missing quotes in JSON string.

        Common repair scenarios:
        1. Missing end quote: {"query": hello} -> {"query": "hello"}
        2. Missing key quote: {query: "hello"} -> {"query": "hello"}
        3. Windows path without quotes: {"path": D:/work/file.txt} -> {"path": "D:/work/file.txt"}

        Args:
            json_str: Possibly malformed JSON string

        Returns:
            Repaired JSON string, or original if no repair possible
        """
        import re

        s = json_str.strip()

        # Pattern 1: Fix Windows paths (D:/path, C:/path)
        s = re.sub(
            r':\s+([A-Za-z]:/[^\{\[]*?)(?=\s*[,\}\]])',
            lambda m: f': "{m.group(1)}"',
            s
        )

        # Pattern 2: Fix missing end quote for string values (non-path)
        # Match ": value" where value is unquoted string
        s = re.sub(
            r':\s+(?!"|true|false|null|\d+|{|\[|:|"|[A-Za-z]:/)([^\s,\}\[\]""]+?)(?=\s*[,}\]])',
            lambda m: f': "{m.group(1)}"',
            s
        )

        # Pattern 3: Fix missing key quotes ({key: value} -> {"key": value})
        s = re.sub(
            r'{\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
            r'{"\1":',
            s
        )

        return s

    async def _fix_incomplete_tool_context(self, context: Any) -> None:
        """Fix incomplete context: ensure assistant messages with tool_calls have matching tool messages."""
        try:
            messages = context.get_messages()
            len_messages = len(messages)
            if len_messages == 0:
                return

            messages = context.pop_messages(size=len_messages)
            tool_message_cache: dict = {}
            tool_id_cache: list = []

            for i in range(len_messages):
                if isinstance(messages[i], AssistantMessage):
                    if not tool_id_cache:
                        tool_calls = getattr(messages[i], "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                arguments = getattr(tc, "arguments", '{}')
                                arguments = self._ensure_json_arguments(arguments)
                                if hasattr(tc, "arguments"):
                                    tc.arguments = arguments
                                tool_id_cache.append({
                                        "tool_call_id": getattr(tc, "id", ""),
                                        "tool_name": getattr(tc, "name", ""),
                                })
                        await context.add_messages(messages[i])
                    else:
                        logger.info("Fixed incomplete tool context with placeholder messages")
                        for tc_info in tool_id_cache:
                            tool_name = tc_info["tool_name"]
                            tool_call_id = tc_info["tool_call_id"]
                            if tool_call_id in tool_message_cache:
                                await context.add_messages(tool_message_cache[tool_call_id])
                            else:
                                await context.add_messages(ToolMessage(
                                        content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                        tool_call_id=tool_call_id,
                                ))
                        tool_id_cache = []
                        tool_calls = getattr(messages[i], "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                arguments = getattr(tc, "arguments", {})
                                arguments = self._ensure_json_arguments(arguments)
                                if hasattr(tc, "arguments"):
                                    tc.arguments = arguments
                                tool_id_cache.append({
                                        "tool_call_id": getattr(tc, "id", ""),
                                        "tool_name": getattr(tc, "name", ""),
                                })
                        await context.add_messages(messages[i])
                elif isinstance(messages[i], ToolMessage):
                    if not tool_id_cache:
                        tool_message_cache[messages[i].tool_call_id] = messages[i]
                        continue
                    if messages[i].tool_call_id == tool_id_cache[0]["tool_call_id"]:
                        await context.add_messages(messages[i])
                        tool_id_cache.pop(0)
                    else:
                        tool_message_cache[messages[i].tool_call_id] = messages[i]
                        continue
                else:
                    logger.info("Fixed incomplete tool context with placeholder messages")
                    for tc_info in tool_id_cache:
                        tool_name = tc_info["tool_name"]
                        tool_call_id = tc_info["tool_call_id"]
                        if tool_call_id in tool_message_cache:
                            await context.add_messages(tool_message_cache[tool_call_id])
                        else:
                            await context.add_messages(ToolMessage(
                                    content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                    tool_call_id=tool_call_id,
                            ))
                    tool_id_cache = []
                    await context.add_messages(messages[i])

            if tool_id_cache:
                for tc_info in tool_id_cache:
                    tool_name = tc_info["tool_name"]
                    tool_call_id = tc_info["tool_call_id"]
                    if tool_call_id in tool_message_cache:
                        await context.add_messages(tool_message_cache[tool_call_id])
                    else:
                        await context.add_messages(ToolMessage(
                                content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                tool_call_id=tool_call_id,
                        ))
        except Exception as e:
            logger.warning("Failed to fix incomplete tool context: %s", e)
