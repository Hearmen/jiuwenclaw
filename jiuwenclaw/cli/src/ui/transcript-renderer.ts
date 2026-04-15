import type { AppSnapshot } from "../app-state.js";
import { renderHistoryEntry } from "./components/messages/index.js";
import { shouldEmphasizeAssistantTransition } from "./components/messages/presentation-rules.js";
import { selectTranscriptEntries } from "./transcript-entry-selection.js";
import { buildWelcomeLines } from "./welcome.js";

export function buildTranscriptLines(
  snapshot: AppSnapshot,
  width: number,
  showFullThinking: boolean,
  showToolDetails: boolean,
  animationPhase: number,
): string[] {
  const { entries: displayEntries, latestThinkingId } = selectTranscriptEntries(snapshot);

  const allLines: string[] = [];
  if (displayEntries.length === 0) {
    allLines.push(...buildWelcomeLines(width));
  }

  for (const [index, entry] of displayEntries.entries()) {
    const nextEntry = displayEntries[index + 1];
    const collapsed =
      (entry.kind === "tool_group" || entry.kind === "collapsed_tool_group") &&
      snapshot.collapsedToolGroupIds.has(entry.id);
    const rendered = renderHistoryEntry(entry, width, {
      compact: snapshot.transcriptMode === "compact",
      collapsed,
      thinkingExpanded: showFullThinking,
      activeThinkingId: snapshot.isProcessing ? latestThinkingId : undefined,
      toolDetailsExpanded: showToolDetails,
      animationPhase,
    });
    allLines.push(...rendered.lines);

    if (rendered.gapAfter) {
      allLines.push(" ".repeat(width));
    }
    if (
      shouldEmphasizeAssistantTransition(entry, nextEntry, snapshot.transcriptMode === "compact")
    ) {
      allLines.push(" ".repeat(width));
    }
  }

  return allLines;
}
