import type { AppSnapshot } from "../app-state.js";
import { renderHistoryEntry } from "./components/messages/index.js";
import { renderTodoList } from "./components/todo-list.js";
import { APP_SCREEN_KEY_BINDINGS } from "./keymap.js";
import { padToWidth } from "./rendering/text.js";
import { palette } from "./theme.js";
import { buildWelcomeLines } from "./welcome.js";

export interface ScreenLayoutOptions {
  width: number;
  terminalRows: number;
  questionLines: string[];
  editorLines: string[];
  showFullThinking: boolean;
  showToolDetails: boolean;
  showShortcutHelp: boolean;
  transientNotice: string | null;
}

function formatSubtaskStatus(status: string): string {
  switch (status) {
    case "starting":
      return "starting";
    case "tool_call":
      return "tool";
    case "tool_result":
      return "result";
    case "completed":
      return "done";
    case "error":
      return "error";
    default:
      return status;
  }
}

function buildStatusLines(
  snapshot: AppSnapshot,
  width: number,
  transientNotice: string | null,
): string[] {
  const left = [
    `status:${snapshot.connectionStatus}`,
    `mode:${snapshot.mode}`,
    `view:${snapshot.transcriptMode}`,
  ];
  if (snapshot.transcriptFoldMode !== "none") left.push(`fold:${snapshot.transcriptFoldMode}`);

  const right = snapshot.lastError
    ? `error:${snapshot.lastError}`
    : snapshot.isPaused
      ? "paused"
      : snapshot.isProcessing
        ? "running"
        : "ready";

  const lines = transientNotice ? [padToWidth(palette.status.warning(transientNotice), width)] : [];
  const content = `${left.join(" | ")} | ${right}`;
  lines.push(padToWidth(palette.text.dim(content), width));
  const leadSubtask = snapshot.activeSubtasks[0];
  if (leadSubtask) {
    const parts = [
      `subtask ${leadSubtask.index}/${leadSubtask.total || "?"}`,
      formatSubtaskStatus(leadSubtask.status),
      leadSubtask.description || leadSubtask.task_id,
    ];
    if (leadSubtask.tool_name) parts.push(leadSubtask.tool_name);
    if (leadSubtask.message) parts.push(leadSubtask.message);
    if (snapshot.activeSubtasks.length > 1)
      parts.push(`+${snapshot.activeSubtasks.length - 1} more`);
    lines.push(padToWidth(palette.text.dim(parts.join(" | ")), width));
  } else if (snapshot.evolutionStatus === "running") {
    lines.push(padToWidth(palette.text.dim("evolution | running"), width));
  }
  if (snapshot.contextCompression) {
    const { beforeCompressed, afterCompressed, rate } = snapshot.contextCompression;
    const parts = ["context"];
    if (beforeCompressed !== null && afterCompressed !== null) {
      parts.push(`${beforeCompressed} -> ${afterCompressed}`);
    }
    if (rate) {
      parts.push(`${rate.toFixed(1)}%`);
    }
    lines.push(padToWidth(palette.text.dim(parts.join(" | ")), width));
  }
  return lines;
}

function buildShortcutLines(width: number): string[] {
  const lines = [
    padToWidth(palette.text.secondary("Shortcuts"), width),
    ...APP_SCREEN_KEY_BINDINGS.map((binding) =>
      padToWidth(palette.text.dim(`${binding.label} | ${binding.description}`), width),
    ),
    padToWidth(palette.text.dim("/help | show slash commands"), width),
    " ".repeat(width),
  ];
  return lines;
}

function buildTranscriptLines(
  snapshot: AppSnapshot,
  width: number,
  showFullThinking: boolean,
  showToolDetails: boolean,
): string[] {
  let displayEntries =
    snapshot.transcriptMode === "compact"
      ? snapshot.entries
          .filter((entry) => entry.kind !== "system")
          .map((entry) =>
            entry.kind === "tool_group" ? { ...entry, tools: entry.tools.slice(-1) } : entry,
          )
      : snapshot.entries;

  if (snapshot.transcriptFoldMode === "all") {
    displayEntries = displayEntries.filter(
      (entry) =>
        entry.kind === "user" ||
        entry.kind === "assistant" ||
        entry.kind === "thinking" ||
        entry.kind === "error",
    );
  } else if (snapshot.transcriptFoldMode === "thinking") {
    displayEntries = displayEntries.filter((entry) => entry.kind !== "thinking");
  } else if (snapshot.transcriptFoldMode === "tools") {
    displayEntries = displayEntries.filter(
      (entry) => entry.kind !== "system" && entry.kind !== "info",
    );
  }

  const allLines: string[] = [];
  if (displayEntries.length === 0) {
    allLines.push(...buildWelcomeLines(width));
  }
  for (const entry of displayEntries) {
    const collapsed = entry.kind === "tool_group" && snapshot.collapsedToolGroupIds.has(entry.id);
    allLines.push(
      ...renderHistoryEntry(entry, width, {
        compact: snapshot.transcriptMode === "compact",
        collapsed,
        thinkingExpanded: showFullThinking,
        toolDetailsExpanded: showToolDetails,
      }),
    );
    allLines.push(" ".repeat(width));
  }
  return allLines;
}

export function buildAppScreenLines(snapshot: AppSnapshot, options: ScreenLayoutOptions): string[] {
  const statusLines = buildStatusLines(snapshot, options.width, options.transientNotice);
  const shortcutLines = options.showShortcutHelp ? buildShortcutLines(options.width) : [];

  const transcriptLines = buildTranscriptLines(
    snapshot,
    options.width,
    options.showFullThinking,
    options.showToolDetails,
  );
  const todoLines = renderTodoList(snapshot.todos, options.width);

  return [
    ...transcriptLines,
    ...todoLines,
    ...options.questionLines,
    ...options.editorLines,
    ...statusLines,
    ...shortcutLines,
  ];
}
