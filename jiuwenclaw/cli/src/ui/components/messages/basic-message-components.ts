import { truncateToWidth, type Component } from "@mariozechner/pi-tui";
import type { HistoryItem } from "../../../core/types.js";
import { chalk, palette } from "../../theme.js";
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
    return prefixedLines(lines, width, "> ", palette.text.user, "│ ");
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
    const lines = renderMarkdownLines(width, body, 0, 0);
    if (this.entry.streaming && lines.length > 0) {
      const lastIndex = lines.length - 1;
      lines[lastIndex] = truncateToWidth(
        lines[lastIndex] + palette.status.warning(" …"),
        Math.max(1, width),
      );
    }
    return lines;
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
      return renderWrappedText(width, "thinking", (value) =>
        chalk.italic(palette.text.thinking(value)),
      );
    }
    const lines = renderWrappedText(width, "thinking", (value) =>
      chalk.italic(palette.text.thinking(value)),
    );
    const wrapped = renderStyledMarkdownLines(
      Math.max(1, width - 2),
      this.entry.content,
      {
        color: palette.text.dim,
        italic: true,
      },
      0,
      0,
    );
    return [...lines, ...prefixedLines(wrapped, width, "│ ", palette.text.dim, "│ ")];
  }
}

export class SystemMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "system" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    return renderWrappedText(width, `· ${this.entry.content}`, palette.text.system);
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
      "  ",
    );
  }
}

export class InfoMessageComponent implements Component {
  constructor(private readonly entry: Extract<HistoryItem, { kind: "info" }>) {}

  invalidate(): void {}

  render(width: number): string[] {
    const meta = this.entry.meta;
    const lines: string[] = [];
    const innerWidth = Math.max(1, width);
    const title = meta?.title ?? this.entry.content;
    lines.push(...renderWrappedText(innerWidth, `· ${title}`, palette.text.info));
    for (const item of meta?.items ?? []) {
      const value = item.value ? `: ${item.value}` : "";
      lines.push(
        ...renderWrappedText(innerWidth, `  ${item.label}${value}`, palette.text.assistant),
      );
      if (item.description) {
        lines.push(...renderWrappedText(innerWidth, `    ${item.description}`, palette.text.dim));
      }
    }
    return lines;
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
        ? ""
        : this.entry.kind === "user"
          ? "> "
          : this.entry.kind === "error"
            ? "! "
            : "· ";
    const color =
      this.entry.kind === "error"
        ? palette.status.error
        : this.entry.kind === "assistant"
          ? palette.text.assistant
          : this.entry.kind === "user"
            ? palette.text.user
            : this.entry.kind === "thinking"
              ? palette.text.thinking
              : palette.text.dim;
    return renderWrappedText(width, `${prefix}${content}`, color);
  }
}
