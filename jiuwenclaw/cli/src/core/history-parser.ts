import type { EventFrame } from "./protocol.js";
import type { HistoryItem, JsonObject, JsonValue, ToolCallDisplay } from "./types.js";

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function toJsonObject(value: Record<string, unknown>): JsonObject {
  return value as unknown as JsonObject;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : undefined;
}

function parseArguments(raw: unknown): Record<string, unknown> | undefined {
  if (raw && typeof raw === "object") return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        return parsed as Record<string, unknown>;
      }
    } catch {
      // Ignore parse failures.
    }
  }
  return undefined;
}

function resolveToolPayload(
  payload: Record<string, unknown>,
  key: "tool_call" | "tool_result",
): Record<string, unknown> {
  return asRecord(payload[key]) ?? payload;
}

function resolveToolCallId(
  payload: Record<string, unknown>,
  fallback?: Record<string, unknown>,
): string | undefined {
  return (
    asString(payload.id) ??
    asString(payload.tool_call_id) ??
    asString(payload.toolCallId) ??
    asString(fallback?.tool_call_id) ??
    asString(fallback?.toolCallId)
  );
}

function resolveToolName(
  payload: Record<string, unknown>,
  fallback?: Record<string, unknown>,
): string {
  return (
    asString(payload.name) ??
    asString(payload.tool_name) ??
    asString(fallback?.tool_name) ??
    "unknown"
  );
}

export function createToolCallDisplay(payload: Record<string, unknown>): ToolCallDisplay {
  const toolPayload = resolveToolPayload(payload, "tool_call");
  return {
    callId: resolveToolCallId(toolPayload, payload) ?? `tool-${Date.now()}`,
    name: resolveToolName(toolPayload, payload),
    arguments: parseArguments(toolPayload.arguments),
    description: asString(toolPayload.description),
    formattedArgs: asString(toolPayload.formatted_args),
    status: "running",
  };
}

export function applyToolResult(
  tool: ToolCallDisplay,
  payload: Record<string, unknown>,
): ToolCallDisplay {
  const toolPayload = resolveToolPayload(payload, "tool_result");
  const result =
    typeof toolPayload.result === "string"
      ? toolPayload.result
      : toolPayload.data !== undefined
        ? stringifyJson(toolPayload.data as JsonValue)
        : typeof toolPayload.error === "string"
          ? toolPayload.error
          : payload.content !== undefined
            ? stringifyJson(payload.content as JsonValue)
            : undefined;
  const status = asString(toolPayload.status);
  const success = typeof toolPayload.success === "boolean" ? toolPayload.success : undefined;
  const isError =
    (success !== undefined ? !success : undefined) ??
    (status ? status === "error" : undefined) ??
    asBoolean(payload.is_error) ??
    false;
  return {
    ...tool,
    status: isError ? "error" : "completed",
    result,
    summary: asString(toolPayload.summary),
    isError,
  };
}

export function createSessionResultToolDisplay(
  payload: Record<string, unknown>,
  effectiveEvent = "chat.session_result",
): ToolCallDisplay {
  const sessionId = asString(payload.session_id) ?? "";
  const description = asString(payload.description) ?? "";
  const result = asString(payload.result) ?? "";
  const status = payload.status === "error" ? "error" : "completed";
  const callId = `session-${sessionId || "unknown"}-${typeof payload.index === "number" ? payload.index : Date.now()}`;
  const fullResult = description ? `描述: ${description}\n\n结果: ${result}` : result;

  return {
    callId,
    name: "session",
    arguments: {
      session_id: sessionId,
      description,
      event_type: effectiveEvent,
      status: typeof payload.status === "string" ? payload.status : undefined,
      index: typeof payload.index === "number" ? payload.index : undefined,
      total: typeof payload.total === "number" ? payload.total : undefined,
      is_parallel: payload.is_parallel === true,
    },
    description: description || "会话完成",
    formattedArgs: `会话任务：【${description || "未知任务"}】`,
    status,
    result: fullResult,
    summary: status === "error" ? "失败" : "完成",
    isError: status === "error",
  };
}

export function parseHistoryFrame(frame: EventFrame): HistoryItem | null {
  if (frame.event !== "history.message") return null;

  const payload = frame.payload;
  const sessionId = asString(payload.session_id) ?? "";
  const role = asString(payload.role) ?? "";
  const eventType = asString(payload.event_type) ?? asString(payload.type) ?? "";
  const sourceChunkType = asString(payload.source_chunk_type) ?? "";
  const at = asString(payload.at) ?? new Date().toISOString();
  const id = asString(payload.id) ?? `hist-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

  if (role === "user") {
    const content = asString(payload.content) ?? "";
    if (!content) return null;
    return { kind: "user", id, sessionId, content, at };
  }

  if (eventType === "chat.tool_call") {
    return {
      kind: "tool_group",
      id,
      sessionId,
      requestId: asString(payload.request_id),
      tools: [createToolCallDisplay(payload)],
      at,
    };
  }

  if (eventType === "chat.tool_result") {
    return {
      kind: "tool_group",
      id,
      sessionId,
      requestId: asString(payload.request_id),
      tools: [applyToolResult(createToolCallDisplay(payload), payload)],
      at,
    };
  }

  if (eventType === "chat.media" || eventType === "chat.file") {
    const name = asString(payload.file_name) ?? asString(payload.name);
    const label = name ? `[${eventType}: ${name}]` : `[${eventType}]`;
    return {
      kind: "system",
      id,
      sessionId,
      content: label,
      at,
      meta: {
        eventType,
        rawPayload: toJsonObject(payload),
        fileName: name,
        filePath: asString(payload.path),
      },
    };
  }

  if (
    eventType === "chat.reasoning" ||
    (eventType === "chat.delta" && sourceChunkType === "llm_reasoning")
  ) {
    const content = asString(payload.content) ?? "";
    if (!content) return null;
    return {
      kind: "thinking",
      id,
      sessionId,
      content,
      at,
    };
  }

  if (eventType === "chat.session_result" || eventType === "session_result") {
    return {
      kind: "tool_group",
      id,
      sessionId,
      requestId: asString(payload.request_id),
      tools: [createSessionResultToolDisplay(payload, eventType)],
      at,
    };
  }

  if (
    eventType === "context.compressed" ||
    eventType === "chat.subtask_update" ||
    eventType === "chat.evolution_status" ||
    eventType === "chat.processing_status" ||
    eventType === "chat.interrupt_result" ||
    eventType === "chat.ask_user_question"
  ) {
    return null;
  }

  const content = asString(payload.content) ?? "";
  if (!content) return null;
  return {
    kind: "assistant",
    id,
    sessionId,
    content,
    requestId: asString(payload.request_id),
    at,
  };
}

export function stringifyJson(value: JsonValue): string {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
