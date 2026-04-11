import { truncateToWidth, type Component } from "@mariozechner/pi-tui";
import type { HistoryItem } from "../../../core/types.js";
import { palette } from "../../theme.js";
import {
  prefixedLines,
  renderMarkdownLines,
  renderStyledMarkdownLines,
  renderWrappedText,
  summarize,
} from "../../rendering/text.js";
import { summarizeToolResultByKind } from "../tools/index.js";

export class UserMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "user" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    const lines = renderStyledMarkdownLines(
      Math.max(1, width - 2),
      this.entry.content,
      {
        bgColor: palette.surface.user,
        color: (value: string) => value,
      },
      1,
      1,
    );
    return prefixedLines(lines, width, "> ", palette.text.accent);
  }
}

export class AssistantMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "assistant" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    const body = this.entry.content.trim();
    if (!body) {
      return [];
    }
    const lines = renderMarkdownLines(Math.max(1, width - 2), body, 1, 0);
    if (this.entry.streaming && lines.length > 0) {
      const lastIndex = lines.length - 1;
      lines[lastIndex] = truncateToWidth(
        lines[lastIndex] + palette.status.warning(" [streaming]"),
        Math.max(1, width - 2),
      );
    }
    return prefixedLines(lines, width, "• ", palette.text.dim);
  }
}

export class ThinkingMessageComponent implements Component {
  constructor(
    private readonly entry: Extract<HistoryItem, { kind: "thinking" }>,
    private readonly expanded: boolean,
  ) {}

  invalidate(): void {}

  render(width: number): string[] {
    if (!this.expanded) {
      const lines = renderStyledMarkdownLines(
        Math.max(1, width - 2),
        "Thinking...",
        {
          color: palette.text.dim,
          italic: true,
        },
        1,
        0,
      );
      return prefixedLines(lines, width, "• ", palette.status.success);
    }
    const wrapped = renderStyledMarkdownLines(
      Math.max(1, width - 2),
      this.entry.content,
      {
        color: palette.text.dim,
        italic: true,
      },
      1,
      0,
    );
    return prefixedLines(wrapped, width, "• ", palette.status.success);
  }
}

export class SystemMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "system" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    return prefixedLines(
      renderWrappedText(Math.max(1, width - 2), this.entry.content, palette.text.dim),
      width,
      "· ",
      palette.text.dim,
    );
  }
}

export class ErrorMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "error" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    return prefixedLines(
      renderWrappedText(Math.max(1, width - 2), this.entry.content, palette.status.error),
      width,
      "! ",
      palette.status.error,
    );
  }
}

export class InfoMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "info" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    const meta = this.entry.meta;
    const lines: string[] = [];
    const innerWidth = Math.max(1, width - 2);
    const title = meta?.title ?? this.entry.content;
    lines.push(...renderWrappedText(innerWidth, title, palette.status.info));
    for (const item of meta?.items ?? []) {
      const value = item.value ? `: ${item.value}` : "";
      lines.push(...renderWrappedText(innerWidth, `  ${item.label}${value}`, palette.text.primary));
      if (item.description) {
        lines.push(...renderWrappedText(innerWidth, `    ${item.description}`, palette.text.dim));
      }
    }
    return prefixedLines(lines, width, "· ", palette.text.dim);
  }
}

export class CompactMessageComponent implements Component {
  constructor(private readonly entry: HistoryItem) {}

  invalidate(): void {}

  render(width: number): string[] {
    if (this.entry.kind === "tool_group") {
      const lastTool = this.entry.tools[this.entry.tools.length - 1];
      const summary = lastTool
        ? `${lastTool.name}${lastTool.result ? ` - ${lastTool.summary ?? summarizeToolResultByKind(lastTool.name, lastTool.result) ?? summarize(lastTool.result, 72)}` : ""}`
        : "tool activity";
      return renderWrappedText(width, `[tool] ${summary}`, palette.text.dim);
    }

    const content =
      this.entry.kind === "assistant" || this.entry.kind === "thinking"
        ? summarize(this.entry.content, 120)
        : this.entry.content;
    const prefix =
      this.entry.kind === "assistant" || this.entry.kind === "thinking"
        ? "[a] "
        : this.entry.kind === "user"
          ? "> "
          : this.entry.kind === "error"
            ? "! "
            : "- ";
    const color =
      this.entry.kind === "error"
        ? palette.status.error
        : this.entry.kind === "assistant"
          ? palette.text.primary
          : palette.text.dim;
    return renderWrappedText(width, `${prefix}${content}`, color);
  }
}
