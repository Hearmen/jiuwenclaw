import type { Component } from "@mariozechner/pi-tui";
import type { HistoryItem, ToolCallDisplay } from "../../../core/types.js";
import { palette } from "../../theme.js";
import { prefixedLines, renderWrappedText, summarize } from "../../rendering/text.js";

const TOOL_BODY_PREFIX = "│ ";
const TOOL_BODY_CONTINUATION = "│ ";
const TOOL_TAIL_PREFIX = "└ ";
const TOOL_TAIL_CONTINUATION = "  ";
const TOOL_EXPAND_HINT = "ctrl+o to expand";
const MAX_STRUCTURED_LINES_COLLAPSED = 6;
const MAX_STRUCTURED_LINES_EXPANDED = 18;

function summarizePath(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.slice(-3).join("/");
}

function normalizePythonLiteralToJson(value: string): string {
  return value
    .replace(/\bNone\b/g, "null")
    .replace(/\bTrue\b/g, "true")
    .replace(/\bFalse\b/g, "false")
    .replace(/'([^'\\]*(?:\\.[^'\\]*)*)'/g, (_match, content: string) => {
      const normalized = content.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      return `"${normalized}"`;
    });
}

function parseLiteralFragment(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed || trimmed === "None") {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    // Fall through.
  }
  try {
    return JSON.parse(normalizePythonLiteralToJson(trimmed));
  } catch {
    return trimmed;
  }
}

function parseProtocolWrapper(value: string): unknown | undefined {
  if (!value.includes("data=") && !value.includes("success=") && !value.includes("error=")) {
    return undefined;
  }

  let working = value.trim();
  let errorValue: unknown = undefined;
  const errorIndex = working.lastIndexOf(" error=");
  if (errorIndex >= 0) {
    errorValue = parseLiteralFragment(working.slice(errorIndex + " error=".length));
    working = working.slice(0, errorIndex);
  }

  let dataValue: unknown = undefined;
  const dataIndex = working.indexOf(" data=");
  if (dataIndex >= 0) {
    dataValue = parseLiteralFragment(working.slice(dataIndex + " data=".length));
    working = working.slice(0, dataIndex);
  }

  const successMatch = /\bsuccess=(True|False|true|false)\b/.exec(working);
  const successValue =
    successMatch?.[1] !== undefined ? successMatch[1].toLowerCase() === "true" : undefined;

  if (isPlainObject(dataValue)) {
    return {
      ...dataValue,
      ...(successValue !== undefined ? { success: successValue } : {}),
      ...(errorValue !== undefined ? { error: errorValue } : {}),
    };
  }
  if (Array.isArray(dataValue)) {
    return {
      items: dataValue,
      count: dataValue.length,
      ...(successValue !== undefined ? { success: successValue } : {}),
      ...(errorValue !== undefined ? { error: errorValue } : {}),
    };
  }
  if (dataValue !== undefined || successValue !== undefined || errorValue !== undefined) {
    return {
      ...(successValue !== undefined ? { success: successValue } : {}),
      ...(errorValue !== undefined ? { error: errorValue } : {}),
      ...(dataValue !== undefined ? { result: dataValue } : {}),
    };
  }
  return undefined;
}

function tryParseStructuredText(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) {
    return value;
  }
  const wrapped = parseProtocolWrapper(trimmed);
  if (wrapped !== undefined) {
    return wrapped;
  }
  if (!["{", "["].includes(trimmed[0] ?? "")) {
    return value;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    try {
      return JSON.parse(normalizePythonLiteralToJson(trimmed));
    } catch {
      return value;
    }
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isPrimitive(value: unknown): value is string | number | boolean | null {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

function formatLeafValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") {
    const compact = value.replace(/\s+/g, " ").trim();
    return compact ? summarize(compact, 96) : '""';
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return summarize(JSON.stringify(value), 96);
}

function formatStructuredValue(
  value: unknown,
  maxLines: number,
  maxDepth: number,
): { lines: string[]; truncated: number } {
  const output: string[] = [];

  const append = (line: string): boolean => {
    if (output.length >= maxLines) {
      return false;
    }
    output.push(line);
    return true;
  };

  const visit = (current: unknown, indent: string, label?: string, depth = 0): void => {
    if (output.length >= maxLines) return;
    const parsed = typeof current === "string" ? tryParseStructuredText(current) : current;

    if (isPrimitive(parsed)) {
      if (typeof parsed === "string" && parsed.includes("\n")) {
        const compactLines = parsed
          .split("\n")
          .map((line) => line.trimEnd())
          .filter((line) => line.length > 0);
        if (label) {
          if (!append(`${indent}${label}:`)) return;
        }
        const visible = compactLines.slice(0, Math.max(1, maxLines - output.length));
        for (const line of visible) {
          if (!append(`${indent}  ${summarize(line, 96)}`)) return;
        }
        return;
      }
      append(`${indent}${label ? `${label}: ` : ""}${formatLeafValue(parsed)}`);
      return;
    }

    if (depth >= maxDepth) {
      append(`${indent}${label ? `${label}: ` : ""}${Array.isArray(parsed) ? "[…]" : "{…}"}`);
      return;
    }

    if (Array.isArray(parsed)) {
      if (label) {
        if (!append(`${indent}${label}:`)) return;
      }
      if (parsed.length === 0) {
        append(`${indent}${label ? "  " : ""}[]`);
        return;
      }
      const childIndent = indent + (label ? "  " : "");
      for (const item of parsed) {
        if (output.length >= maxLines) return;
        if (isPrimitive(item)) {
          append(`${childIndent}- ${formatLeafValue(item)}`);
          continue;
        }
        if (!append(`${childIndent}-`)) return;
        visit(item, `${childIndent}  `, undefined, depth + 1);
      }
      return;
    }

    if (isPlainObject(parsed)) {
      const entries = Object.entries(parsed).filter(([, item]) => item !== undefined);
      if (label) {
        if (!append(`${indent}${label}:`)) return;
      }
      if (entries.length === 0) {
        append(`${indent}${label ? "  " : ""}{}`);
        return;
      }
      const childIndent = indent + (label ? "  " : "");
      for (const [key, item] of entries) {
        visit(item, childIndent, key, depth + 1);
        if (output.length >= maxLines) return;
      }
      return;
    }

    append(`${indent}${label ? `${label}: ` : ""}${summarize(String(parsed), 96)}`);
  };

  visit(value, "");
  const truncated = Math.max(0, output.length - maxLines);
  return { lines: output.slice(0, maxLines), truncated };
}

function summarizeStructuredPayload(value: string): string | undefined {
  const parsed = tryParseStructuredText(value);
  if (Array.isArray(parsed)) {
    return `${parsed.length} item${parsed.length === 1 ? "" : "s"}`;
  }
  if (isPlainObject(parsed)) {
    const keys = Object.keys(parsed);
    return `${keys.length} field${keys.length === 1 ? "" : "s"}`;
  }
  return undefined;
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
  if (typeof obj.path === "string") {
    return summarizePath(obj.path);
  }
  const keys = Object.keys(obj).filter((key) => obj[key] !== undefined);
  return keys.length > 0 ? `${keys.length} field${keys.length === 1 ? "" : "s"}` : undefined;
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
  return summarizeStructuredPayload(result);
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

function getStringArg(args: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function getNumericArg(args: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = args[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return undefined;
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

function isRunTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return (
    normalized === "bash" ||
    normalized === "shell" ||
    normalized === "sh" ||
    normalized === "powershell" ||
    normalized === "command" ||
    normalized === "exec" ||
    normalized === "run"
  );
}

function isGlobTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized === "glob" || normalized === "glob_files";
}

function isSearchTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return (
    normalized === "search" ||
    normalized === "grep" ||
    normalized === "rg" ||
    normalized === "ripgrep" ||
    normalized === "memory_search"
  );
}

function isMcpTool(name: string): boolean {
  const normalized = name.toLowerCase();
  return normalized.startsWith("mcp_") || normalized.startsWith("mcp.");
}

function isToolRunning(tool: ToolCallDisplay): boolean {
  return tool.status === "running" && !tool.result;
}

function toolPrefix(tool: ToolCallDisplay): string {
  if (tool.isError || tool.status === "error") return "! ";
  if (isToolRunning(tool)) return "◉ ";
  return "● ";
}

function toolPrefixColor(tool: ToolCallDisplay): (value: string) => string {
  if (tool.isError || tool.status === "error") return palette.status.error;
  if (isToolRunning(tool)) return palette.text.tool;
  return palette.status.success;
}

function toolLineColor(tool: ToolCallDisplay): (value: string) => string {
  if (tool.isError || tool.status === "error") return palette.status.error;
  if (isToolRunning(tool)) return palette.text.tool;
  return palette.text.assistant;
}

function toolStateColor(tool: ToolCallDisplay): (value: string) => string {
  if (tool.isError || tool.status === "error") return palette.status.error;
  if (isToolRunning(tool)) return palette.text.tool;
  return palette.status.success;
}

function renderToolTitle(width: number, tool: ToolCallDisplay, text: string): string[] {
  return prefixedLines(
    renderWrappedText(Math.max(1, width - 2), text, toolLineColor(tool)),
    width,
    toolPrefix(tool),
    toolPrefixColor(tool),
    "  ",
  );
}

function renderToolBranch(
  width: number,
  text: string,
  colorFn: (value: string) => string,
): string[] {
  return prefixedLines(
    renderWrappedText(Math.max(1, width - 2), text, colorFn),
    width,
    TOOL_BODY_PREFIX,
    palette.text.subtle,
    TOOL_BODY_CONTINUATION,
  );
}

function renderToolBranchAnsi(width: number, text: string): string[] {
  return renderToolBranch(width, text, (value) => value);
}

function renderToolTail(width: number, text: string, colorFn: (value: string) => string): string[] {
  return prefixedLines(
    renderWrappedText(Math.max(1, width - 2), text, colorFn),
    width,
    TOOL_TAIL_PREFIX,
    palette.text.subtle,
    TOOL_TAIL_CONTINUATION,
  );
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
  for (let i = 0; i < total; i += 1) {
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

function toolDisplayName(tool: ToolCallDisplay): string {
  const name = tool.name.toLowerCase();
  if (name in { bash: true, shell: true, sh: true, powershell: true, command: true, exec: true }) {
    return "Run";
  }
  if (name in { glob: true, glob_files: true }) {
    return "Glob";
  }
  if (name.startsWith("mcp_")) {
    return `Query ${name
      .split("_")
      .slice(1)
      .filter(Boolean)
      .map((part) => part[0]?.toUpperCase() + part.slice(1))
      .join(" ")}`;
  }
  if (name.startsWith("mcp.")) {
    return `Query ${name
      .split(".")
      .slice(1)
      .filter(Boolean)
      .map((part) => part[0]?.toUpperCase() + part.slice(1))
      .join(" ")}`;
  }
  if (name in { read: true, read_file: true, read_memory: true, memory_get: true, view: true }) {
    return "Read";
  }
  if (name in { ls: true, list_files: true }) {
    return "List";
  }
  if (name === "memory_search") {
    return "Search memories";
  }
  if (name in { write: true, write_file: true, write_memory: true }) {
    return "Write";
  }
  if (name in { edit: true, edit_file: true, edit_memory: true }) {
    return "Edit";
  }
  if (name === "session") {
    return "Run session";
  }
  return tool.name
    .split(/[_\-.]+/)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1))
    .join(" ");
}

function renderStructuredBranch(
  width: number,
  value: unknown,
  showDetails: boolean,
  colorFn: (value: string) => string,
): string[] {
  const maxLines = showDetails ? MAX_STRUCTURED_LINES_EXPANDED : MAX_STRUCTURED_LINES_COLLAPSED;
  const { lines } = formatStructuredValue(value, maxLines, showDetails ? 4 : 2);
  return lines.flatMap((line) => renderToolBranch(width, line, colorFn));
}

function renderSearchMatchBranch(width: number, line: string): string[] {
  const trimmed = line.trim();
  const match = /^(.+?):(\d+)(?::(\d+))?(?:(:|\s+-\s+|\s+)(.*))?$/.exec(trimmed);
  if (!match) {
    return renderToolBranch(width, line, palette.text.assistant);
  }
  const [, filePath, lineNumber, columnNumber, separatorRaw, remainderRaw] = match;
  if (!filePath || (!filePath.includes("/") && !filePath.includes("."))) {
    return renderToolBranch(width, line, palette.text.assistant);
  }
  const separator = separatorRaw ?? "";
  const remainder = remainderRaw ?? "";
  const location = `${filePath}:${lineNumber}${columnNumber ? `:${columnNumber}` : ""}`;
  const formatted = `${palette.text.tool(filePath)}${palette.text.dim(`:${lineNumber}${columnNumber ? `:${columnNumber}` : ""}`)}${separator}${remainder ? palette.text.assistant(remainder) : ""}`;
  return renderToolBranchAnsi(width, formatted || location);
}

function parseToolResultPayload(tool: ToolCallDisplay): Record<string, unknown> | undefined {
  if (!tool.result) return undefined;
  const parsed = tryParseStructuredText(tool.result);
  return isPlainObject(parsed) ? parsed : undefined;
}

function parseToolResultValue(tool: ToolCallDisplay): unknown {
  if (!tool.result) return undefined;
  return tryParseStructuredText(tool.result);
}

function getStringList(payload: Record<string, unknown>, ...keys: string[]): string[] {
  for (const key of keys) {
    const value = payload[key];
    if (Array.isArray(value)) {
      const list = value.filter(
        (item): item is string => typeof item === "string" && item.length > 0,
      );
      if (list.length > 0) {
        return list;
      }
    }
  }
  return [];
}

function getStringListFromValue(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

function nonEmptyLines(value: unknown): string[] {
  if (typeof value !== "string") return [];
  return value
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.trim().length > 0);
}

function renderPreviewLines(
  width: number,
  lines: string[],
  colorFn: (value: string) => string,
  expandedLimit: number,
  collapsedLimit: number,
  showDetails: boolean,
  noun: string,
): string[] {
  const limit = showDetails ? expandedLimit : collapsedLimit;
  const shown = lines.slice(0, limit);
  const rendered = shown.flatMap((line) => renderToolBranch(width, line, colorFn));
  if (lines.length > shown.length) {
    rendered.push(
      ...renderToolBranch(width, `+ ${lines.length - shown.length} more ${noun}`, palette.text.dim),
    );
  }
  if (!showDetails && lines.length > 0) {
    rendered.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
  }
  return rendered;
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
        : (tool.description ?? "session task");
    const index = typeof args.index === "number" ? args.index : undefined;
    const total = typeof args.total === "number" ? args.total : undefined;
    const label =
      index !== undefined && total !== undefined
        ? `Run session ${index}/${total} · ${description}`
        : `Run session · ${description}`;
    const lines = renderToolTitle(width, tool, label);

    if (tool.result) {
      const previewLines = tool.result.split("\n").filter(Boolean);
      lines.push(
        ...renderToolTail(
          width,
          tool.summary ??
            summarizeToolResultByKind(tool.name, tool.result) ??
            summarize(tool.result, 120),
          toolStateColor(tool),
        ),
      );
      if (this.showDetails) {
        for (const line of previewLines.slice(0, 6)) {
          lines.push(...renderToolBranch(width, line, palette.text.assistant));
        }
        if (previewLines.length > 6) {
          lines.push(
            ...renderToolBranch(width, `+ ${previewLines.length - 6} more lines`, palette.text.dim),
          );
        }
      } else if (previewLines.length > 0) {
        lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
      }
    }

    return lines;
  }

  private renderReadTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const payload = parseToolResultPayload(tool);
    const path = getToolFilePath(args) ?? getStringArg(payload ?? {}, "path", "file_path", "file");
    const offset = typeof args.offset === "number" ? args.offset : undefined;
    const limit = typeof args.limit === "number" ? args.limit : undefined;
    const rangeSuffix =
      offset !== undefined || limit !== undefined
        ? `:${offset ?? 1}${limit !== undefined ? `-${(offset ?? 1) + limit - 1}` : ""}`
        : "";
    const lines = renderToolTitle(
      width,
      tool,
      `Read ${path ?? tool.description ?? tool.name}${rangeSuffix}`,
    );

    if (tool.result) {
      const content = getStringArg(payload ?? {}, "content", "result", "data") ?? tool.result;
      const { mainLines, notices } = extractTrailingBracketNotices(content);
      const totalLines =
        getNumericArg(payload ?? {}, "totalLines", "total_lines") ?? countLogicalLines(content);
      const startLine = getNumericArg(payload ?? {}, "start_line", "startLine");
      const endLine = getNumericArg(payload ?? {}, "end_line", "endLine");
      const truncated = typeof payload?.truncated === "boolean" ? payload.truncated : undefined;
      if (path) {
        const lineRange =
          startLine !== undefined && endLine !== undefined ? ` · ${startLine}-${endLine}` : "";
        const meta = `${totalLines} lines${lineRange}${isCodeLikePath(path) ? " · code" : ""}`;
        lines.push(...renderToolBranch(width, meta, palette.text.dim));
      }
      if (this.showDetails) {
        for (const line of mainLines.slice(0, 10)) {
          lines.push(...renderToolBranch(width, line, palette.text.assistant));
        }
        if (mainLines.length > 10) {
          lines.push(
            ...renderToolBranch(width, `+ ${mainLines.length - 10} more lines`, palette.text.dim),
          );
        }
      } else {
        lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
      }
      if (truncated) {
        lines.push(...renderToolBranch(width, "result truncated", palette.status.warning));
      }
      for (const notice of notices) {
        lines.push(...renderToolBranch(width, notice, palette.status.warning));
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
    const lines = renderToolTitle(
      width,
      tool,
      `List ${path}${limit !== undefined ? ` (limit ${limit})` : ""}`,
    );

    if (tool.result) {
      const payload = parseToolResultPayload(tool);
      const files = getStringList(payload ?? {}, "files");
      const dirs = getStringList(payload ?? {}, "dirs").map((dir) => `${dir}/`);
      const payloadEntries = [...files, ...dirs];
      const { mainLines, notices } = extractTrailingBracketNotices(tool.result);
      const visibleEntries = payloadEntries.length > 0 ? payloadEntries : mainLines;
      const shown = visibleEntries.slice(0, this.showDetails ? 12 : 6);
      lines.push(
        ...renderToolTail(
          width,
          `${visibleEntries.length} entr${visibleEntries.length === 1 ? "y" : "ies"}`,
          palette.text.dim,
        ),
      );
      for (const entry of shown) {
        lines.push(...renderToolBranch(width, entry, palette.text.assistant));
      }
      if (visibleEntries.length > shown.length) {
        lines.push(
          ...renderToolBranch(
            width,
            `+ ${visibleEntries.length - shown.length} more entries`,
            palette.text.dim,
          ),
        );
      }
      if (!this.showDetails && visibleEntries.length > 0) {
        lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
      }
      for (const notice of notices) {
        lines.push(...renderToolBranch(width, notice, palette.status.warning));
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
    const lines = renderToolTitle(width, tool, `Write ${path ?? tool.description ?? tool.name}`);

    if (content) {
      lines.push(
        ...renderToolBranch(
          width,
          `${countLogicalLines(content)} lines · ${content.length} chars`,
          palette.text.dim,
        ),
      );
      const diffLines = renderAddedLines(Math.max(1, width - 2), content);
      const shown = this.showDetails ? diffLines : diffLines.slice(0, 6);
      lines.push(...shown.flatMap((line) => renderToolBranch(width, line, (value) => value)));
      if (!this.showDetails && diffLines.length > shown.length) {
        lines.push(
          ...renderToolBranch(
            width,
            `+ ${diffLines.length - shown.length} more diff lines`,
            palette.text.dim,
          ),
        );
        lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
      }
    }

    if (tool.result) {
      lines.push(
        ...renderToolTail(
          width,
          tool.summary ??
            summarizeToolResultByKind(tool.name, tool.result) ??
            summarize(tool.result, 120),
          toolStateColor(tool),
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
    const lines = renderToolTitle(width, tool, `Edit ${path ?? tool.description ?? tool.name}`);

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
      const diffLines = renderSimpleDiff(Math.max(1, width - 2), oldString, newString);
      const shown = this.showDetails ? diffLines : diffLines.slice(0, 6);
      lines.push(...shown.flatMap((line) => renderToolBranch(width, line, (value) => value)));
      if (!this.showDetails && diffLines.length > shown.length) {
        lines.push(
          ...renderToolBranch(
            width,
            `+ ${diffLines.length - shown.length} more diff lines`,
            palette.text.dim,
          ),
        );
        lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
      }
    } else if (Array.isArray(args.edits)) {
      lines.push(...renderToolTail(width, `${args.edits.length} edit block(s)`, palette.text.dim));
      const shownEdits = args.edits.slice(0, this.showDetails ? 3 : 1);
      for (const edit of shownEdits) {
        if (!isPlainObject(edit)) continue;
        const before =
          typeof edit.oldText === "string"
            ? edit.oldText
            : typeof edit.old_string === "string"
              ? edit.old_string
              : "";
        const after =
          typeof edit.newText === "string"
            ? edit.newText
            : typeof edit.new_string === "string"
              ? edit.new_string
              : "";
        if (!before && !after) continue;
        const diffLines = renderSimpleDiff(Math.max(1, width - 2), before, after);
        lines.push(
          ...diffLines
            .slice(0, this.showDetails ? 6 : 2)
            .flatMap((line) => renderToolBranch(width, line, (value) => value)),
        );
      }
      if (args.edits.length > shownEdits.length) {
        lines.push(
          ...renderToolBranch(
            width,
            `+ ${args.edits.length - shownEdits.length} more edit blocks`,
            palette.text.dim,
          ),
        );
      }
      if (!this.showDetails && args.edits.length > 0) {
        lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
      }
    }

    if (tool.result) {
      lines.push(
        ...renderToolTail(
          width,
          tool.summary ??
            summarizeToolResultByKind(tool.name, tool.result) ??
            summarize(tool.result, 120),
          toolStateColor(tool),
        ),
      );
    }

    return lines;
  }

  private renderRunTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const payload = parseToolResultPayload(tool);
    const command =
      getStringArg(args, "command", "cmd", "script", "input") ??
      getStringArg(payload ?? {}, "command", "cmd") ??
      tool.description ??
      tool.name;
    const lines = renderToolTitle(width, tool, `Run ${summarize(command, 120)}`);

    const cwd = getStringArg(args, "cwd", "path", "workdir");
    if (cwd) {
      lines.push(...renderToolBranch(width, `cwd: ${summarizePath(cwd) ?? cwd}`, palette.text.dim));
    }

    if (tool.result) {
      const exitCode =
        getNumericArg(payload ?? {}, "exit_code", "exitCode", "code") ??
        getNumericArg(args, "exit_code", "exitCode", "code");
      const stdoutLines =
        nonEmptyLines(payload?.stdout) ||
        nonEmptyLines(payload?.output) ||
        nonEmptyLines(payload?.content) ||
        nonEmptyLines(payload?.result);
      const stderrLines = nonEmptyLines(payload?.stderr);

      const summaryParts: string[] = [];
      if (exitCode !== undefined) {
        summaryParts.push(`exit ${exitCode}`);
      }
      if (stdoutLines.length > 0) {
        summaryParts.push(`${stdoutLines.length} line${stdoutLines.length === 1 ? "" : "s"}`);
      } else if (stderrLines.length > 0) {
        summaryParts.push(
          `${stderrLines.length} stderr line${stderrLines.length === 1 ? "" : "s"}`,
        );
      }

      lines.push(
        ...renderToolTail(
          width,
          tool.summary ?? (summaryParts.join(" | ") || summarize(tool.result, 120)),
          toolStateColor(tool),
        ),
      );

      if (stdoutLines.length > 0 || stderrLines.length > 0) {
        lines.push(
          ...renderPreviewLines(
            width,
            stdoutLines,
            palette.text.assistant,
            8,
            4,
            this.showDetails,
            "lines",
          ),
        );
        lines.push(
          ...renderPreviewLines(
            width,
            stderrLines,
            palette.status.warning,
            8,
            2,
            this.showDetails,
            "stderr lines",
          ),
        );
      } else if (payload && this.showDetails) {
        lines.push(
          ...renderStructuredBranch(
            width,
            payload,
            this.showDetails,
            tool.isError ? palette.status.error : palette.text.assistant,
          ),
        );
      } else if (!payload && this.showDetails) {
        lines.push(
          ...renderPreviewLines(
            width,
            nonEmptyLines(tool.result),
            tool.isError ? palette.status.error : palette.text.assistant,
            8,
            4,
            this.showDetails,
            "lines",
          ),
        );
      }
    }

    return lines;
  }

  private renderGlobTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const payload = parseToolResultPayload(tool);
    const parsedValue = parseToolResultValue(tool);
    const pattern =
      getStringArg(args, "glob", "pattern", "path", "file_path") ??
      getStringArg(payload ?? {}, "glob", "pattern") ??
      tool.description ??
      tool.name;
    const lines = renderToolTitle(width, tool, `Glob ${summarize(pattern, 120)}`);
    const root = getStringArg(args, "root", "cwd", "dir_path");
    if (root) {
      lines.push(
        ...renderToolBranch(width, `root: ${summarizePath(root) ?? root}`, palette.text.dim),
      );
    }

    if (tool.result) {
      const payloadMatchLines = getStringList(payload ?? {}, "matching_files", "files");
      const valueMatchLines = getStringListFromValue(parsedValue);
      const matchLines = payloadMatchLines.length > 0 ? payloadMatchLines : valueMatchLines;
      const count = getNumericArg(payload ?? {}, "count") ?? matchLines.length;
      lines.push(
        ...renderToolTail(
          width,
          tool.summary ?? `${count} file${count === 1 ? "" : "s"}`,
          toolStateColor(tool),
        ),
      );
      if (matchLines.length > 0) {
        lines.push(
          ...renderPreviewLines(
            width,
            matchLines,
            palette.text.assistant,
            8,
            4,
            this.showDetails,
            "files",
          ),
        );
      } else if (payload && this.showDetails) {
        lines.push(
          ...renderStructuredBranch(width, payload, this.showDetails, palette.text.assistant),
        );
      } else if (parsedValue !== undefined && this.showDetails) {
        lines.push(
          ...renderStructuredBranch(width, parsedValue, this.showDetails, palette.text.assistant),
        );
      }
    }

    return lines;
  }

  private renderSearchTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const payload = parseToolResultPayload(tool);
    const parsedValue = parseToolResultValue(tool);
    const query =
      getStringArg(args, "pattern", "query", "q", "prompt", "glob") ??
      getStringArg(payload ?? {}, "pattern", "query", "q") ??
      tool.description ??
      tool.name;
    const root =
      getStringArg(args, "path", "cwd", "root", "dir_path") ??
      getStringArg(payload ?? {}, "path", "cwd", "root");
    const title = root
      ? `Search ${summarize(query, 96)} in ${summarizePath(root) ?? root}`
      : `Search ${summarize(query, 120)}`;
    const lines = renderToolTitle(width, tool, title);

    if (tool.result) {
      const payloadResultLines = getStringList(
        payload ?? {},
        "matches",
        "results",
        "items",
        "hits",
        "files",
      );
      const valueResultLines = getStringListFromValue(parsedValue);
      const resultLines = payloadResultLines.length > 0 ? payloadResultLines : valueResultLines;
      const fallbackLines = nonEmptyLines(tool.result);
      const visibleLines = resultLines.length > 0 ? resultLines : fallbackLines;
      const count =
        getNumericArg(payload ?? {}, "count", "match_count", "matches_count", "total") ??
        visibleLines.length;
      lines.push(
        ...renderToolTail(
          width,
          tool.summary ?? `${count} match${count === 1 ? "" : "es"}`,
          toolStateColor(tool),
        ),
      );
      if (visibleLines.length > 0) {
        const limit = this.showDetails ? 8 : 4;
        const shown = visibleLines.slice(0, limit);
        for (const line of shown) {
          lines.push(
            ...(tool.isError
              ? renderToolBranch(width, line, palette.status.error)
              : renderSearchMatchBranch(width, line)),
          );
        }
        if (visibleLines.length > shown.length) {
          lines.push(
            ...renderToolBranch(
              width,
              `+ ${visibleLines.length - shown.length} more matches`,
              palette.text.dim,
            ),
          );
        }
        if (!this.showDetails && visibleLines.length > 0) {
          lines.push(...renderToolBranch(width, TOOL_EXPAND_HINT, palette.text.dim));
        }
      } else if (payload && this.showDetails) {
        lines.push(
          ...renderStructuredBranch(
            width,
            payload,
            this.showDetails,
            tool.isError ? palette.status.error : palette.text.assistant,
          ),
        );
      } else if (parsedValue !== undefined && this.showDetails) {
        lines.push(
          ...renderStructuredBranch(
            width,
            parsedValue,
            this.showDetails,
            tool.isError ? palette.status.error : palette.text.assistant,
          ),
        );
      }
    }

    return lines;
  }

  private renderMcpTool(tool: ToolCallDisplay, width: number): string[] {
    const args =
      tool.arguments && typeof tool.arguments === "object"
        ? (tool.arguments as Record<string, unknown>)
        : {};
    const title = toolDisplayName(tool);
    const detail =
      getStringArg(args, "query", "q", "prompt", "url", "path", "file_path", "file") ??
      tool.description ??
      summarizeToolArguments(tool.name, args);
    const lines = renderToolTitle(
      width,
      tool,
      `${title}${detail ? ` · ${summarize(detail, 120)}` : ""}`,
    );

    if (isPlainObject(tool.arguments)) {
      lines.push(
        ...renderStructuredBranch(width, tool.arguments, this.showDetails, palette.text.dim),
      );
    }

    if (tool.result) {
      const payload = parseToolResultPayload(tool);
      const parsedValue = parseToolResultValue(tool);
      lines.push(
        ...renderToolTail(
          width,
          tool.summary ??
            summarizeToolResultByKind(tool.name, tool.result) ??
            summarize(tool.result, 120),
          toolStateColor(tool),
        ),
      );
      if (payload) {
        lines.push(
          ...renderStructuredBranch(
            width,
            payload,
            this.showDetails,
            tool.isError ? palette.status.error : palette.text.assistant,
          ),
        );
      } else if (parsedValue !== undefined && this.showDetails) {
        lines.push(
          ...renderStructuredBranch(
            width,
            parsedValue,
            this.showDetails,
            tool.isError ? palette.status.error : palette.text.assistant,
          ),
        );
      } else if (this.showDetails) {
        lines.push(
          ...renderPreviewLines(
            width,
            nonEmptyLines(tool.result),
            tool.isError ? palette.status.error : palette.text.assistant,
            8,
            4,
            this.showDetails,
            "lines",
          ),
        );
      }
    }

    return lines;
  }

  private renderGenericTool(tool: ToolCallDisplay, width: number): string[] {
    const title = toolDisplayName(tool);
    const detail = tool.description ?? summarizeToolArguments(tool.name, tool.arguments);
    const separator = title.startsWith("Query ") ? " · " : " ";
    const lines = renderToolTitle(width, tool, `${title}${detail ? `${separator}${detail}` : ""}`);

    if (isPlainObject(tool.arguments)) {
      const structuredArgs = renderStructuredBranch(
        width,
        tool.arguments,
        this.showDetails,
        palette.text.dim,
      );
      lines.push(...structuredArgs);
    }

    if (tool.result) {
      const summary =
        tool.summary ??
        summarizeToolResultByKind(tool.name, tool.result) ??
        summarize(tool.result, 120);
      lines.push(...renderToolTail(width, summary, toolStateColor(tool)));

      const parsedResult = tryParseStructuredText(tool.result);
      if (parsedResult !== tool.result) {
        lines.push(
          ...renderStructuredBranch(
            width,
            parsedResult,
            this.showDetails,
            tool.isError ? palette.status.error : palette.text.assistant,
          ),
        );
      } else if (this.showDetails) {
        const previewLines = tool.result.split("\n").filter(Boolean);
        const shown = previewLines.slice(0, tool.isError ? 2 : 4);
        for (const line of shown) {
          lines.push(
            ...renderToolBranch(
              width,
              line,
              tool.isError ? palette.status.error : palette.text.assistant,
            ),
          );
        }
        if (previewLines.length > shown.length) {
          lines.push(
            ...renderToolBranch(
              width,
              `+ ${previewLines.length - shown.length} more lines`,
              palette.text.dim,
            ),
          );
        }
      }
    }

    return lines;
  }

  render(width: number): string[] {
    const lines: string[] = [];
    const tools = this.collapsed ? this.entry.tools.slice(-1) : this.entry.tools;

    for (const tool of tools) {
      if (tool.name === "session") {
        lines.push(...this.renderSessionTool(tool, width));
        continue;
      }
      if (isReadTool(tool.name)) {
        lines.push(...this.renderReadTool(tool, width));
        continue;
      }
      if (isListTool(tool.name)) {
        lines.push(...this.renderListTool(tool, width));
        continue;
      }
      if (isWriteTool(tool.name)) {
        lines.push(...this.renderWriteTool(tool, width));
        continue;
      }
      if (isEditTool(tool.name)) {
        lines.push(...this.renderEditTool(tool, width));
        continue;
      }
      if (isRunTool(tool.name)) {
        lines.push(...this.renderRunTool(tool, width));
        continue;
      }
      if (isGlobTool(tool.name)) {
        lines.push(...this.renderGlobTool(tool, width));
        continue;
      }
      if (isSearchTool(tool.name)) {
        lines.push(...this.renderSearchTool(tool, width));
        continue;
      }
      if (isMcpTool(tool.name)) {
        lines.push(...this.renderMcpTool(tool, width));
        continue;
      }
      lines.push(...this.renderGenericTool(tool, width));
    }

    return lines;
  }
}
