import type { Component } from "@mariozechner/pi-tui";
import type { HistoryItem, ToolCallDisplay } from "../../../core/types.js";
import { palette } from "../../theme.js";
import {
  prefixedLines,
  renderIndentedBlock,
  renderWrappedText,
  summarize,
} from "../../rendering/text.js";

function summarizePath(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.slice(-3).join("/");
}

function summarizeToolArguments(name: string, args: unknown): string | undefined {
  if (!args || typeof args !== "object") {
    return typeof args === "string" ? summarize(args, 72) : undefined;
  }
  const obj = args as Record<string, unknown>;
  const normalized = name.toLowerCase();
  if (normalized.includes("read") || normalized.includes("view")) {
    return summarizePath(obj.path ?? obj.file_path ?? obj.file);
  }
  if (normalized.includes("edit") || normalized.includes("write") || normalized.includes("patch")) {
    return summarizePath(obj.path ?? obj.file_path ?? obj.file) ?? "content change";
  }
  if (normalized.includes("search") || normalized.includes("grep") || normalized.includes("glob")) {
    const pattern =
      typeof obj.pattern === "string"
        ? obj.pattern
        : typeof obj.query === "string"
          ? obj.query
          : undefined;
    const root = summarizePath(obj.path ?? obj.cwd ?? obj.root);
    return [pattern, root].filter(Boolean).join(" in ") || undefined;
  }
  if (
    normalized.includes("exec") ||
    normalized.includes("bash") ||
    normalized.includes("shell") ||
    normalized.includes("command")
  ) {
    return typeof obj.cmd === "string"
      ? summarize(obj.cmd, 72)
      : typeof obj.command === "string"
        ? summarize(obj.command, 72)
        : undefined;
  }
  return summarize(JSON.stringify(args), 72);
}

export function summarizeToolResultByKind(name: string, result: string): string | undefined {
  const normalized = name.toLowerCase();
  const lines = result.split("\n").filter(Boolean).length;
  if (normalized.includes("read") || normalized.includes("view")) return `${lines} lines loaded`;
  if (normalized.includes("search") || normalized.includes("grep")) return `${lines} matches`;
  if (normalized.includes("edit") || normalized.includes("write") || normalized.includes("patch"))
    return "edit applied";
  if (
    normalized.includes("exec") ||
    normalized.includes("bash") ||
    normalized.includes("shell") ||
    normalized.includes("command")
  ) {
    return summarize(result.split("\n")[0] ?? "", 88);
  }
  return undefined;
}

function countLogicalLines(text: string): number {
  if (!text) return 0;
  return text.split("\n").length;
}

function isCodeLikePath(path: string | undefined): boolean {
  if (!path) return false;
  return /\.(ts|tsx|js|jsx|py|rs|go|java|c|cc|cpp|h|hpp|json|yaml|yml|toml|md|sh|sql|css|scss|html)$/i.test(
    path,
  );
}

function getToolFilePath(args: Record<string, unknown>): string | undefined {
  const value = args.path ?? args.file_path ?? args.file ?? args.dir_path;
  return typeof value === "string" && value ? (summarizePath(value) ?? value) : undefined;
}

function isReadTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized === "read" || normalized === "read_file";
}

function isListTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized === "ls" || normalized === "list_files";
}

function isWriteTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized === "write" || normalized === "write_file";
}

function isEditTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized === "edit" || normalized === "edit_file";
}

function renderDiffRow(
  width: number,
  sign: " " | "+" | "-",
  text: string,
  colorFn: (value: string) => string,
): string[] {
  return renderWrappedText(width, `${sign} ${text}`, colorFn);
}

function renderSimpleDiff(width: number, beforeText: string, afterText: string): string[] {
  const beforeLines = beforeText.split("\n");
  const afterLines = afterText.split("\n");
  const total = Math.max(beforeLines.length, afterLines.length);
  const lines: string[] = [];
  for (let i = 0; i < total; i++) {
    const before = beforeLines[i];
    const after = afterLines[i];
    if (before === after && before !== undefined) {
      lines.push(...renderDiffRow(width, " ", before, palette.diff.context));
      continue;
    }
    if (before !== undefined) {
      lines.push(...renderDiffRow(width, "-", before, palette.diff.remove));
    }
    if (after !== undefined) {
      lines.push(...renderDiffRow(width, "+", after, palette.diff.add));
    }
  }
  return lines;
}

function renderAddedLines(width: number, text: string): string[] {
  return text
    .split("\n")
    .filter((line) => line.length > 0)
    .flatMap((line) => renderDiffRow(width, "+", line, palette.diff.add));
}

function extractTrailingBracketNotices(text: string): { mainLines: string[]; notices: string[] } {
  const lines = text.split("\n");
  const notices: string[] = [];
  let end = lines.length;
  while (end > 0) {
    const current = lines[end - 1]?.trim() ?? "";
    if (current.startsWith("[") && current.endsWith("]")) {
      notices.unshift(current);
      end -= 1;
      continue;
    }
    break;
  }
  return {
    mainLines: lines.slice(0, end).filter((line) => line.length > 0),
    notices,
  };
}

export class ToolGroupMessageComponent implements Component {
  constructor(
    private readonly entry: Extract<HistoryItem, { kind: "tool_group" }>,
    private readonly collapsed: boolean,
    private readonly showDetails: boolean,
  ) {}

  invalidate(): void {}

  private renderSessionTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const description =
      typeof args.description === "string" && args.description
        ? args.description
        : (tool.description ?? "会话任务");
    const index = typeof args.index === "number" ? args.index : undefined;
    const total = typeof args.total === "number" ? args.total : undefined;
    const status = tool.isError ? "error" : tool.status === "running" ? "running" : "completed";

    const lines: string[] = [];
    const headerParts = ["[session]"];
    if (index !== undefined && total !== undefined) {
      headerParts.push(`${index}/${total}`);
    }
    headerParts.push(status);
    headerParts.push(description);
    lines.push(
      ...renderWrappedText(
        width,
        headerParts.join(" | "),
        tool.isError ? palette.status.error : palette.status.info,
      ),
    );

    if (tool.result) {
      if (!this.showDetails) {
        lines.push(
          ...renderWrappedText(
            width,
            `  ${tool.summary ?? summarize(tool.result, 120)}`,
            tool.isError ? palette.status.error : palette.text.dim,
          ),
        );
        lines.push(...renderIndentedBlock(width, "ctrl+g to expand", palette.text.dim));
        return lines;
      }
      const previewLines = tool.result.split("\n").filter(Boolean);
      const shown = previewLines.slice(0, 6);
      for (const line of shown) {
        lines.push(
          ...renderWrappedText(
            width,
            `  ${line}`,
            tool.isError ? palette.status.error : palette.text.primary,
          ),
        );
      }
      if (previewLines.length > shown.length) {
        lines.push(
          ...renderWrappedText(
            width,
            `  ... ${previewLines.length - shown.length} more lines`,
            palette.text.dim,
          ),
        );
      }
    }

    return lines;
  }

  private renderReadTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const path = getToolFilePath(args);
    const offset = typeof args.offset === "number" ? args.offset : undefined;
    const limit = typeof args.limit === "number" ? args.limit : undefined;
    const lines: string[] = [];
    const header = `[read] ${path ?? tool.description ?? tool.name}${offset !== undefined || limit !== undefined ? `:${offset ?? 1}${limit !== undefined ? `-${(offset ?? 1) + limit - 1}` : ""}` : ""}`;
    lines.push(...renderWrappedText(width, header, palette.text.secondary));
    if (tool.result) {
      const { mainLines, notices } = extractTrailingBracketNotices(tool.result);
      const shown = mainLines.slice(0, this.showDetails ? 10 : 4);
      if (path) {
        const meta = `${countLogicalLines(tool.result)} lines${isCodeLikePath(path) ? " | code" : ""}`;
        lines.push(...renderIndentedBlock(width, meta, palette.text.dim));
      }
      if (this.showDetails) {
        for (const line of shown) {
          lines.push(...renderIndentedBlock(width, line, palette.text.primary));
        }
        if (mainLines.length > shown.length) {
          lines.push(
            ...renderIndentedBlock(
              width,
              `... ${mainLines.length - shown.length} more lines`,
              palette.text.dim,
            ),
          );
        }
      } else {
        lines.push(...renderIndentedBlock(width, "content hidden", palette.text.dim));
        lines.push(...renderIndentedBlock(width, "ctrl+g to expand", palette.text.dim));
      }
      for (const notice of notices) {
        lines.push(...renderIndentedBlock(width, notice, palette.status.warning));
      }
    }
    return lines;
  }

  private renderListTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const path = getToolFilePath(args) ?? ".";
    const limit = typeof args.limit === "number" ? args.limit : undefined;
    const lines: string[] = [];
    lines.push(
      ...renderWrappedText(
        width,
        `[list] ${path}${limit !== undefined ? ` (limit ${limit})` : ""}`,
        palette.text.secondary,
      ),
    );
    if (tool.result) {
      const { mainLines, notices } = extractTrailingBracketNotices(tool.result);
      const shown = mainLines.slice(0, this.showDetails ? 12 : 6);
      lines.push(...renderIndentedBlock(width, `${mainLines.length} entries`, palette.text.dim));
      for (const entry of shown) {
        lines.push(...renderIndentedBlock(width, entry, palette.text.primary));
      }
      if (mainLines.length > shown.length) {
        lines.push(
          ...renderIndentedBlock(
            width,
            `... ${mainLines.length - shown.length} more entries`,
            palette.text.dim,
          ),
        );
      }
      if (!this.showDetails && mainLines.length > 0) {
        lines.push(...renderIndentedBlock(width, "ctrl+g to expand", palette.text.dim));
      }
      for (const notice of notices) {
        lines.push(...renderIndentedBlock(width, notice, palette.status.warning));
      }
    }
    return lines;
  }

  private renderWriteTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const path = getToolFilePath(args);
    const content = typeof args.content === "string" ? args.content : "";
    const lines: string[] = [];
    lines.push(
      ...renderWrappedText(
        width,
        `[write] ${path ?? tool.description ?? tool.name}`,
        tool.isError ? palette.status.error : palette.text.secondary,
      ),
    );
    if (content) {
      lines.push(
        ...renderIndentedBlock(
          width,
          `${countLogicalLines(content)} lines | ${content.length} chars`,
          palette.text.dim,
        ),
      );
      const diffLines = renderAddedLines(width, content);
      const shown = this.showDetails ? diffLines : diffLines.slice(0, 6);
      lines.push(...shown);
      if (!this.showDetails && diffLines.length > shown.length) {
        lines.push(
          ...renderIndentedBlock(
            width,
            `... ${diffLines.length - shown.length} more diff lines`,
            palette.text.dim,
          ),
        );
        lines.push(...renderIndentedBlock(width, "ctrl+g to expand", palette.text.dim));
      }
    }
    if (tool.result) {
      lines.push(
        ...renderIndentedBlock(
          width,
          tool.summary ?? summarize(tool.result, 120),
          tool.isError ? palette.status.error : palette.status.success,
        ),
      );
    }
    return lines;
  }

  private renderEditTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const path = getToolFilePath(args);
    const lines: string[] = [];
    lines.push(
      ...renderWrappedText(
        width,
        `[edit] ${path ?? tool.description ?? tool.name}`,
        tool.isError ? palette.status.error : palette.text.secondary,
      ),
    );

    const oldString =
      typeof args.old_string === "string"
        ? args.old_string
        : typeof args.oldText === "string"
          ? args.oldText
          : undefined;
    const newString =
      typeof args.new_string === "string"
        ? args.new_string
        : typeof args.newText === "string"
          ? args.newText
          : undefined;

    if (oldString !== undefined && newString !== undefined) {
      const diffLines = renderSimpleDiff(width, oldString, newString);
      const shown = this.showDetails ? diffLines : diffLines.slice(0, 6);
      lines.push(...shown);
      if (!this.showDetails && diffLines.length > shown.length) {
        lines.push(
          ...renderIndentedBlock(
            width,
            `... ${diffLines.length - shown.length} more diff lines`,
            palette.text.dim,
          ),
        );
        lines.push(...renderIndentedBlock(width, "ctrl+g to expand", palette.text.dim));
      }
    } else if (Array.isArray(args.edits)) {
      lines.push(
        ...renderIndentedBlock(width, `${args.edits.length} edit block(s)`, palette.text.dim),
      );
      const shownEdits = args.edits.slice(0, this.showDetails ? 3 : 1);
      for (const edit of shownEdits) {
        if (!edit || typeof edit !== "object") continue;
        const item = edit as Record<string, unknown>;
        const before =
          typeof item.oldText === "string"
            ? item.oldText
            : typeof item.old_string === "string"
              ? item.old_string
              : "";
        const after =
          typeof item.newText === "string"
            ? item.newText
            : typeof item.new_string === "string"
              ? item.new_string
              : "";
        if (!before && !after) continue;
        const diffLines = renderSimpleDiff(width, before, after);
        lines.push(...diffLines.slice(0, this.showDetails ? 6 : 2));
      }
      if (args.edits.length > shownEdits.length) {
        lines.push(
          ...renderIndentedBlock(
            width,
            `... ${args.edits.length - shownEdits.length} more edit blocks`,
            palette.text.dim,
          ),
        );
      }
      if (!this.showDetails && args.edits.length > 0) {
        lines.push(...renderIndentedBlock(width, "ctrl+g to expand", palette.text.dim));
      }
    }

    if (tool.result) {
      lines.push(
        ...renderIndentedBlock(
          width,
          tool.summary ?? summarize(tool.result, 120),
          tool.isError ? palette.status.error : palette.status.success,
        ),
      );
    }
    return lines;
  }

  render(width: number): string[] {
    const lines: string[] = [];
    const innerWidth = Math.max(1, width - 2);
    const tools = this.collapsed ? this.entry.tools.slice(-1) : this.entry.tools;
    for (const tool of tools) {
      if (tool.name === "session") {
        lines.push(
          ...prefixedLines(this.renderSessionTool(tool, innerWidth), width, "· ", palette.text.dim),
        );
        continue;
      }
      if (isReadTool(tool.name)) {
        lines.push(
          ...prefixedLines(this.renderReadTool(tool, innerWidth), width, "· ", palette.text.dim),
        );
        continue;
      }
      if (isListTool(tool.name)) {
        lines.push(
          ...prefixedLines(this.renderListTool(tool, innerWidth), width, "· ", palette.text.dim),
        );
        continue;
      }
      if (isWriteTool(tool.name)) {
        lines.push(
          ...prefixedLines(this.renderWriteTool(tool, innerWidth), width, "· ", palette.text.dim),
        );
        continue;
      }
      if (isEditTool(tool.name)) {
        lines.push(
          ...prefixedLines(this.renderEditTool(tool, innerWidth), width, "· ", palette.text.dim),
        );
        continue;
      }
      const status = tool.status === "running" ? "[run]" : tool.isError ? "[err]" : "[ok]";
      const detail = tool.description ?? summarizeToolArguments(tool.name, tool.arguments);
      lines.push(
        ...prefixedLines(
          renderWrappedText(
            innerWidth,
            `${status} ${tool.name}${detail ? ` - ${detail}` : ""}`,
            tool.isError ? palette.status.error : palette.text.secondary,
          ),
          width,
          "· ",
          palette.text.dim,
        ),
      );
      if (!this.collapsed && tool.result) {
        const summary =
          tool.summary ??
          summarizeToolResultByKind(tool.name, tool.result) ??
          summarize(tool.result, 120);
        lines.push(
          ...prefixedLines(
            renderWrappedText(
              innerWidth,
              `  ${summary}`,
              tool.isError ? palette.status.error : palette.text.dim,
            ),
            width,
            "  ",
            (value) => value,
          ),
        );
        if (this.showDetails) {
          const preview = tool.result
            .split("\n")
            .slice(0, tool.isError ? 2 : 4)
            .join("\n");
          for (const line of preview.split("\n").filter(Boolean)) {
            lines.push(
              ...prefixedLines(
                renderWrappedText(
                  innerWidth,
                  `    ${line}`,
                  tool.isError ? palette.status.error : palette.text.primary,
                ),
                width,
                "  ",
                (value) => value,
              ),
            );
          }
        }
      }
    }
    return lines;
  }
}
