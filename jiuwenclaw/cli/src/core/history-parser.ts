import { normalizeFinalContent } from "./final-content.js";
import type { EventFrame } from "./protocol.js";
import type { HistoryItem, InfoMeta, JsonValue, MediaItem, ToolCallDisplay } from "./types.js";

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : undefined;
}

function pickFirstString(input: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
    }
  }
  return undefined;
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

function normalizeHistoryRole(rawRole: unknown): "user" | "assistant" | "tool" | "system" {
  if (typeof rawRole !== "string") return "assistant";
  const role = rawRole.trim().toLowerCase();
  if (role === "user" || role === "human") return "user";
  if (role === "assistant" || role === "ai" || role === "bot") return "assistant";
  if (role === "tool" || role === "tool_call" || role === "tool_result") return "tool";
  if (role === "system") return "system";
  return "assistant";
}

function recordTimestampIso(record: Record<string, unknown>): string {
  const ts = record.timestamp;
  if (typeof ts === "number" && Number.isFinite(ts)) {
    const millis = ts > 1_000_000_000_000 ? ts : ts * 1000;
    const d = new Date(millis);
    if (!Number.isNaN(d.getTime())) {
      return d.toISOString();
    }
  }
  if (typeof ts === "string") {
    const parsed = Date.parse(ts);
    if (!Number.isNaN(parsed)) {
      return new Date(parsed).toISOString();
    }
  }
  return new Date().toISOString();
}

function buildEventPayloadForRecord(record: Record<string, unknown>): Record<string, unknown> {
  const eventPayload = asRecord(record.event_payload);
  const base = eventPayload ? { ...eventPayload } : {};
  if (typeof record.content === "string" && typeof base.content !== "string") {
    base.content = record.content;
  }
  if (typeof record.request_id === "string" && typeof base.request_id !== "string") {
    base.request_id = record.request_id;
  }
  if (typeof record.session_id === "string" && typeof base.session_id !== "string") {
    base.session_id = record.session_id;
  }
  return base;
}

function isHistoryDonePayload(payload: Record<string, unknown>): boolean {
  const status = typeof payload.status === "string" ? payload.status.trim().toLowerCase() : "";
  if (status === "done") {
    return true;
  }
  const content = typeof payload.content === "string" ? payload.content.trim().toLowerCase() : "";
  return content === "done";
}

function summarizePath(value: string): string {
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.length <= 3 ? value : parts.slice(-3).join("/");
}

function inferMediaType(
  explicitType: string | undefined,
  mimeType: string | undefined,
): MediaItem["type"] {
  const normalizedType = explicitType?.trim().toLowerCase();
  const normalizedMime = mimeType?.trim().toLowerCase();
  if (normalizedType === "image" || normalizedMime?.startsWith("image/")) return "image";
  if (normalizedType === "audio" || normalizedMime?.startsWith("audio/")) return "audio";
  if (normalizedType === "video" || normalizedMime?.startsWith("video/")) return "video";
  return "document";
}

export function extractMediaItems(payload: Record<string, unknown>): MediaItem[] {
  const files = Array.isArray(payload.files) ? payload.files : [];
  const mediaItems = Array.isArray(payload.media_items) ? payload.media_items : [];
  const rawItems = [...mediaItems, ...files].filter((item): item is Record<string, unknown> =>
    Boolean(item && typeof item === "object"),
  );

  return rawItems.map((item, index) => {
    const mimeType =
      pickFirstString(item, ["mimeType", "mime_type"]) ??
      (Array.isArray(payload.files) ? "application/octet-stream" : "application/octet-stream");
    const filename =
      pickFirstString(item, ["filename", "file_name", "name", "title"]) ??
      pickFirstString(item, ["path", "url"]) ??
      `item-${index + 1}`;
    const url = pickFirstString(item, ["url", "path", "fullPath", "full_path"]);
    const base64Data = pickFirstString(item, ["base64Data", "base64_data", "data"]);
    return {
      type: inferMediaType(pickFirstString(item, ["type"]), mimeType),
      mimeType,
      filename,
      ...(base64Data ? { base64Data } : {}),
      ...(url ? { url } : {}),
    };
  });
}

function buildAttachmentItems(payload: Record<string, unknown>): NonNullable<InfoMeta["items"]> {
  return extractMediaItems(payload).map((item) => {
    return {
      label: summarizePath(item.filename),
      value: item.url ? summarizePath(item.url) : undefined,
      description: item.type === "document" ? item.mimeType : item.type,
    };
  });
}

function summarizeAttachmentHeadline(
  mediaItems: MediaItem[],
  effectiveEvent: "chat.media" | "chat.file",
): string {
  if (mediaItems.length === 0) {
    return effectiveEvent === "chat.file" ? "Attached file" : "Added media";
  }

  const counts = {
    image: mediaItems.filter((item) => item.type === "image").length,
    audio: mediaItems.filter((item) => item.type === "audio").length,
    video: mediaItems.filter((item) => item.type === "video").length,
    document: mediaItems.filter((item) => item.type === "document").length,
  };

  const parts: string[] = [];
  if (counts.image > 0) parts.push(`${counts.image} image${counts.image === 1 ? "" : "s"}`);
  if (counts.audio > 0) parts.push(`${counts.audio} audio`);
  if (counts.video > 0) parts.push(`${counts.video} video${counts.video === 1 ? "" : "s"}`);
  if (counts.document > 0) parts.push(`${counts.document} file${counts.document === 1 ? "" : "s"}`);
  return `${effectiveEvent === "chat.file" ? "Attached" : "Added"} ${parts.join(", ")}`;
}

export function createAttachmentInfoEntry(
  payload: Record<string, unknown>,
  sessionId: string,
  effectiveEvent: "chat.media" | "chat.file",
  at = new Date().toISOString(),
): Extract<HistoryItem, { kind: "info" }> | null {
  const mediaItems = extractMediaItems(payload);
  const items = buildAttachmentItems(payload);
  const content =
    pickFirstString(payload, ["content", "text", "message"]) ??
    summarizeAttachmentHeadline(mediaItems, effectiveEvent);

  if (items.length === 0 && !pickFirstString(payload, ["content", "text", "message"])) {
    return null;
  }

  return {
    kind: "info",
    id: pickFirstString(payload, ["id", "message_id", "msg_id"]) ?? `media-${Date.now()}`,
    sessionId,
    content,
    at,
    ...(mediaItems.length > 0 ? { mediaItems } : {}),
    meta: {
      view: "list",
      title: content,
      items,
    },
  };
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

  const outerPayload = frame.payload;
  if (isHistoryDonePayload(outerPayload)) {
    return null;
  }

  const record = asRecord(outerPayload.message) ?? outerPayload;
  const sessionId =
    pickFirstString(record, ["session_id"]) ?? asString(outerPayload.session_id) ?? "";
  const role = normalizeHistoryRole(record.role);
  const at = recordTimestampIso(record);
  const id =
    pickFirstString(record, ["id", "message_id", "msg_id"]) ??
    `hist-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

  if (role === "user") {
    const content = pickFirstString(record, ["content", "text", "body"]) ?? "";
    if (!content) return null;
    return { kind: "user", id, sessionId, content, at };
  }

  if (role !== "assistant") {
    return null;
  }

  const sourceChunkType = pickFirstString(record, ["source_chunk_type"]) ?? "";
  let eventType = pickFirstString(record, ["event_type", "type"]) ?? "";
  if (!eventType) {
    const rawContent = pickFirstString(record, ["content"]) ?? "";
    if (!rawContent) {
      return null;
    }
    eventType = "chat.final";
  }

  const payload = buildEventPayloadForRecord(record);

  if (eventType === "chat.tool_call") {
    return {
      kind: "tool_group",
      id,
      sessionId,
      requestId: pickFirstString(record, ["request_id"]) ?? asString(payload.request_id),
      tools: [createToolCallDisplay(payload)],
      at,
    };
  }

  if (eventType === "chat.tool_result") {
    return {
      kind: "tool_group",
      id,
      sessionId,
      requestId: pickFirstString(record, ["request_id"]) ?? asString(payload.request_id),
      tools: [applyToolResult(createToolCallDisplay(payload), payload)],
      at,
    };
  }

  if (eventType === "chat.media") {
    const mediaItems = extractMediaItems(payload);
    const content = pickFirstString(payload, ["content", "text", "message"]) ?? "";
    if (!content && mediaItems.length === 0) {
      return null;
    }
    return {
      kind: "assistant",
      id,
      sessionId,
      content,
      ...(mediaItems.length > 0 ? { mediaItems } : {}),
      requestId: pickFirstString(record, ["request_id"]) ?? asString(payload.request_id),
      at,
    };
  }

  if (eventType === "chat.file") {
    return createAttachmentInfoEntry(payload, sessionId, eventType, at);
  }

  if (
    eventType === "chat.reasoning" ||
    (eventType === "chat.delta" && sourceChunkType === "llm_reasoning")
  ) {
    const content = pickFirstString(payload, ["content"]) ?? "";
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
      requestId: pickFirstString(record, ["request_id"]) ?? asString(payload.request_id),
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

  const content =
    eventType === "chat.final"
      ? normalizeFinalContent(payload)
      : (pickFirstString(payload, ["content"]) ?? "");
  if (!content) return null;
  return {
    kind: "assistant",
    id,
    sessionId,
    content,
    requestId: pickFirstString(record, ["request_id"]) ?? asString(payload.request_id),
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
