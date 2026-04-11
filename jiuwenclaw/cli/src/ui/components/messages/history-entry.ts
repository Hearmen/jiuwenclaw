import type { HistoryItem } from "../../../core/types.js";
import {
  AssistantMessageComponent,
  CompactMessageComponent,
  ErrorMessageComponent,
  InfoMessageComponent,
  SystemMessageComponent,
  ThinkingMessageComponent,
  UserMessageComponent,
} from "./basic-message-components.js";
import { ToolGroupMessageComponent } from "../tools/index.js";

export interface MessageRenderOptions {
  compact: boolean;
  collapsed: boolean;
  thinkingExpanded: boolean;
  toolDetailsExpanded: boolean;
}

export function renderHistoryEntry(
  entry: HistoryItem,
  width: number,
  options: MessageRenderOptions,
): string[] {
  if (options.compact) {
    return new CompactMessageComponent(entry).render(width);
  }

  switch (entry.kind) {
    case "user":
      return new UserMessageComponent(entry).render(width);
    case "assistant":
      return new AssistantMessageComponent(entry).render(width);
    case "thinking":
      return new ThinkingMessageComponent(entry, options.thinkingExpanded).render(width);
    case "tool_group":
      return new ToolGroupMessageComponent(
        entry,
        options.collapsed,
        options.toolDetailsExpanded,
      ).render(width);
    case "system":
      return new SystemMessageComponent(entry).render(width);
    case "error":
      return new ErrorMessageComponent(entry).render(width);
    case "info":
      return new InfoMessageComponent(entry).render(width);
  }
}
