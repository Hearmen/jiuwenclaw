import { applyToolResult, createToolCallDisplay } from "./history-parser.js";
import type { HistoryItem } from "./types.js";

export function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

export function findLastIndex<T>(
  items: T[],
  predicate: (item: T, index: number) => boolean,
): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item !== undefined && predicate(item, index)) return index;
  }
  return -1;
}

export function isIgnorableHistoryRestoreError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("invalid page_idx or session history not found");
}

export function upsertToolGroup(
  entries: HistoryItem[],
  sessionId: string,
  requestId: string | undefined,
  toolPayload: Record<string, unknown>,
  isResult: boolean,
): HistoryItem[] {
  const nested =
    toolPayload[isResult ? "tool_result" : "tool_call"] &&
    typeof toolPayload[isResult ? "tool_result" : "tool_call"] === "object"
      ? (toolPayload[isResult ? "tool_result" : "tool_call"] as Record<string, unknown>)
      : toolPayload;
  const callId =
    typeof nested.id === "string"
      ? nested.id
      : typeof nested.tool_call_id === "string"
        ? nested.tool_call_id
        : typeof nested.toolCallId === "string"
          ? nested.toolCallId
          : typeof toolPayload.tool_call_id === "string"
            ? toolPayload.tool_call_id
            : undefined;
  const groupIndex = findLastIndex(
    entries,
    (item) =>
      item.kind === "tool_group" &&
      item.sessionId === sessionId &&
      ((Boolean(callId) && item.tools.some((tool) => tool.callId === callId)) ||
        (Boolean(requestId) && item.requestId === requestId)),
  );

  if (groupIndex === -1) {
    const baseTool = createToolCallDisplay(toolPayload);
    const nextTool = isResult ? applyToolResult(baseTool, toolPayload) : baseTool;
    return [
      ...entries,
      {
        kind: "tool_group",
        id: createId("tool-group"),
        sessionId,
        requestId,
        tools: [nextTool],
        at: new Date().toISOString(),
      },
    ];
  }

  return entries.map((item, index) => {
    if (index !== groupIndex || item.kind !== "tool_group") return item;
    const nextTools = [...item.tools];
    const toolIndex = callId ? nextTools.findIndex((tool) => tool.callId === callId) : -1;
    if (toolIndex === -1) {
      const baseTool = createToolCallDisplay(toolPayload);
      nextTools.push(isResult ? applyToolResult(baseTool, toolPayload) : baseTool);
    } else if (isResult) {
      nextTools[toolIndex] = applyToolResult(nextTools[toolIndex]!, toolPayload);
    }
    return { ...item, tools: nextTools };
  });
}
