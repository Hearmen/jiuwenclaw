import type { AppSnapshot } from "../app-state.js";
import type { HistoryItem, ToolCallDisplay } from "../core/types.js";
import { buildTranscriptEntries } from "../core/transcript-timeline.js";

function isTodoTool(tool: ToolCallDisplay): boolean {
  const normalized = tool.name.trim().toLowerCase();
  return normalized === "todo" || normalized.startsWith("todo_");
}

function filterTodoToolEntry(entry: HistoryItem): HistoryItem | null {
  if (entry.kind !== "tool_group" && entry.kind !== "collapsed_tool_group") {
    return entry;
  }
  const tools = entry.tools.filter((tool) => !isTodoTool(tool));
  if (tools.length === 0) {
    return null;
  }
  return tools.length === entry.tools.length ? entry : { ...entry, tools };
}

export interface SelectedTranscriptEntries {
  entries: HistoryItem[];
  latestThinkingId?: string;
}

export function selectTranscriptEntries(snapshot: AppSnapshot): SelectedTranscriptEntries {
  let entries =
    snapshot.transcriptMode === "compact"
      ? buildTranscriptEntries(snapshot.entries, snapshot.toolExecutions)
          .filter((entry) => entry.kind !== "system")
          .map((entry) =>
            entry.kind === "collapsed_tool_group"
              ? { ...entry, tools: entry.tools.slice(-1) }
              : entry,
          )
      : buildTranscriptEntries(snapshot.entries, snapshot.toolExecutions);

  if (snapshot.transcriptFoldMode === "all") {
    entries = entries.filter(
      (entry) =>
        entry.kind === "user" ||
        entry.kind === "assistant" ||
        entry.kind === "thinking" ||
        entry.kind === "error",
    );
  } else if (snapshot.transcriptFoldMode === "thinking") {
    entries = entries.filter((entry) => entry.kind !== "thinking");
  } else if (snapshot.transcriptFoldMode === "tools") {
    entries = entries.filter((entry) => entry.kind !== "system" && entry.kind !== "info");
  }

  entries = entries
    .map((entry) => filterTodoToolEntry(entry))
    .filter((entry): entry is HistoryItem => entry !== null);

  const latestUserIndex =
    [...entries]
      .map((entry, index) => ({ entry, index }))
      .reverse()
      .find(({ entry }) => entry.kind === "user")?.index ?? -1;
  const latestThinkingId =
    [...entries]
      .slice(latestUserIndex + 1)
      .reverse()
      .find((entry) => entry.kind === "thinking")?.id ?? undefined;

  if (snapshot.transcriptMode === "compact") {
    entries = entries.filter((entry) => entry.kind !== "thinking");
  }

  return { entries, latestThinkingId };
}
