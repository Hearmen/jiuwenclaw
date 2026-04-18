import {
  CombinedAutocompleteProvider,
  Editor,
  SelectList,
  type SelectItem,
  type AutocompleteItem,
  type Component,
  type Focusable,
  type SlashCommand as TuiSlashCommand,
  TUI,
  matchesKey,
} from "@mariozechner/pi-tui";
import { statSync } from "node:fs";
import { basename, extname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import type { CliPiAppState } from "../app-state.js";
import { CommandService, parseSlashCommand } from "../core/commands/CommandService.js";
import { addError, addInfo } from "../core/commands/helpers.js";
import type { SessionListPayload, SessionMeta } from "../core/commands/builtins/resume.js";
import type { ModelListPayload } from "../core/commands/builtins/model.js";
import { handleAppScreenKeyInput } from "./keymap.js";
import { buildAppScreenLines } from "./screen-layout.js";
import {
  isTeamWorking,
  orderedMemberIds,
  teamWorkingStartedAtMs,
} from "./components/team-shared.js";
import { padToWidth } from "./rendering/text.js";
import { editorTheme, palette, selectListTheme } from "./theme.js";

const END_CURSOR = "\x1b[7m \x1b[0m";
const COMPOSER_ATTACHMENT_TOKEN_RE = /\[Image #(\d+)\]/g;
const PERMISSION_TOOL_RE = /工具\s+`([^`]+)`\s+需要授权/;
const PERMISSION_RISK_RE = /安全风险评估：\**\s*([^\s*]+)?\s*\**([^*\n]+?风险)\**/m;
const PERMISSION_QUOTE_RE = /^>\s*(.+)$/gm;
const PERMISSION_JSON_BLOCK_RE = /```json\s*([\s\S]*?)\s*```/i;

type PermissionSummary = {
  tool?: string;
  risk?: string;
  reason?: string;
  command?: string;
  description?: string;
};

type ComposerAttachment = {
  id: string;
  kind: "image";
  path: string;
  filename: string;
};

type ResumeSessionListState = {
  list: SelectList;
  sessions: SessionMeta[];
  total: number;
};

type ModelListState = {
  list: SelectList;
  models: string[];
  current: string;
};

const IMAGE_MIME_TYPES: Record<string, string> = {
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
};

function resolveFdBinary(): string | null {
  for (const candidate of ["fd", "fdfind"]) {
    const result = spawnSync(candidate, ["--version"], {
      stdio: "ignore",
      timeout: 400,
    });
    if (result.status === 0) {
      return candidate;
    }
  }
  return null;
}

function normalizeComposerPath(raw: string): string | null {
  const trimmed = raw
    .trim()
    .replace(/^@/, "")
    .replace(/^"(.*)"$/, "$1");
  if (!trimmed) return null;
  if (trimmed.startsWith("file://")) {
    try {
      return decodeURIComponent(trimmed.slice("file://".length));
    } catch {
      return trimmed.slice("file://".length);
    }
  }
  return trimmed;
}

function looksLikeImagePath(path: string): boolean {
  return extname(path).toLowerCase() in IMAGE_MIME_TYPES;
}

function expandUserPath(path: string): string {
  if (path === "~") {
    return process.env.HOME ?? path;
  }
  if (path.startsWith("~/")) {
    return resolve(process.env.HOME ?? "~", path.slice(2));
  }
  return resolve(process.cwd(), path);
}

function formatComposerAttachmentPath(path: string): string {
  return /\s/.test(path) ? `@"${path}"` : `@${path}`;
}

function composerAttachmentToken(index: number): string {
  return `[Image #${index}] `;
}

function findAttachmentTokenAtCursor(
  line: string,
  cursorCol: number,
): { start: number; end: number } | null {
  for (const match of line.matchAll(COMPOSER_ATTACHMENT_TOKEN_RE)) {
    const start = match.index ?? -1;
    if (start < 0) continue;
    const end = start + match[0].length;
    if (cursorCol > start && cursorCol <= end) {
      return { start, end };
    }
  }
  return null;
}

function syncComposerImageTokens(
  text: string,
  existingAttachments: ComposerAttachment[],
  shouldConsume: (path: string) => boolean,
): { normalizedText: string; attachments: ComposerAttachment[] } {
  let attachments = [...existingAttachments];
  let working = text;

  const findOrAddAttachment = (resolvedPath: string): number => {
    const existingIndex = attachments.findIndex((attachment) => attachment.path === resolvedPath);
    if (existingIndex >= 0) {
      return existingIndex + 1;
    }
    attachments.push({
      id: `attachment-${Date.now().toString(16)}-${attachments.length}`,
      kind: "image",
      path: resolvedPath,
      filename: basename(resolvedPath),
    });
    return attachments.length;
  };

  const consume = (rawPath: string): string | null => {
    const normalized = normalizeComposerPath(rawPath);
    if (!normalized) return null;
    const resolved = expandUserPath(normalized);
    if (!shouldConsume(resolved)) return null;
    return composerAttachmentToken(findOrAddAttachment(resolved));
  };

  working = working.replace(/(^|[\t ])@(?:"([^"]+)"|([^\s]+))/gm, (full, prefix, quoted, plain) => {
    const replacement = consume(quoted ?? plain ?? "");
    return replacement ? `${prefix}${replacement}` : full;
  });

  working = working.replace(
    /(^|[\t ])((?:file:\/\/[^\s]+|(?:~\/|\.{1,2}\/|\/)[^\s"'`]+\.(?:png|jpe?g|gif|webp)))(?=$|[\t ])/gim,
    (full, prefix, rawPath) => {
      const replacement = consume(rawPath ?? "");
      return replacement ? `${prefix}${replacement}` : full;
    },
  );

  const tokenMatches = [...working.matchAll(/\[Image #(\d+)\]/g)];
  const nextAttachments: ComposerAttachment[] = [];
  for (const match of tokenMatches) {
    const tokenIndex = Number.parseInt(match[1] ?? "", 10);
    if (!Number.isFinite(tokenIndex) || tokenIndex < 1) {
      continue;
    }
    const attachment = attachments[tokenIndex - 1];
    if (attachment) {
      nextAttachments.push(attachment);
    }
  }
  attachments = nextAttachments;

  let tokenOrdinal = 0;
  working = working.replace(/\[Image #(\d+)\]\s*/g, () => {
    tokenOrdinal += 1;
    return composerAttachmentToken(tokenOrdinal);
  });

  const normalizedText = working
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/[ \t]{2,}/g, " ");

  return { normalizedText, attachments };
}

function expandComposerImageTokens(text: string, attachments: ComposerAttachment[]): string {
  return text.replace(/\[Image #(\d+)\]\s*/g, (_full, rawIndex: string) => {
    const index = Number.parseInt(rawIndex, 10);
    if (!Number.isFinite(index) || index < 1) {
      return "";
    }
    const attachment = attachments[index - 1];
    return attachment ? `${formatComposerAttachmentPath(attachment.path)} ` : "";
  });
}

function isPermissionRequest(source: string | undefined, questionText: string): boolean {
  return source === "permission" || PERMISSION_TOOL_RE.test(questionText);
}

function parsePermissionSummary(questionText: string): PermissionSummary {
  const tool = PERMISSION_TOOL_RE.exec(questionText)?.[1]?.trim();
  const riskMatch = PERMISSION_RISK_RE.exec(questionText);
  const risk = riskMatch
    ? `${(riskMatch[1] ?? "").trim()} ${riskMatch[2].trim()}`.trim()
    : undefined;
  const reason = [...questionText.matchAll(PERMISSION_QUOTE_RE)]
    .map((match) => match[1]?.trim() ?? "")
    .find(Boolean);

  let command: string | undefined;
  let description: string | undefined;
  const jsonBlock = PERMISSION_JSON_BLOCK_RE.exec(questionText)?.[1]?.trim();
  if (jsonBlock) {
    try {
      const parsed = JSON.parse(jsonBlock) as Record<string, unknown>;
      command =
        typeof parsed.command === "string"
          ? parsed.command.trim()
          : typeof parsed.cmd === "string"
            ? parsed.cmd.trim()
            : undefined;
      description = typeof parsed.description === "string" ? parsed.description.trim() : undefined;
    } catch {
      // Ignore malformed JSON blocks in permission prompts.
    }
  }

  return {
    tool,
    risk,
    reason,
    command,
    description,
  };
}

function compressRiskLabel(risk: string | undefined): string | undefined {
  if (!risk) return undefined;
  const normalized = risk.replace(/\s+/g, " ").trim();
  return normalized
    .replace(/^高\s*/u, "High ")
    .replace(/^中\s*/u, "Medium ")
    .replace(/^低\s*/u, "Low ")
    .replace(/风险$/u, "risk");
}

function permissionToolKind(tool: string | undefined): "bash" | "filesystem" | "generic" {
  const normalized = tool?.trim().toLowerCase() ?? "";
  if (
    normalized === "bash" ||
    normalized === "shell" ||
    normalized === "sh" ||
    normalized === "powershell" ||
    normalized === "command" ||
    normalized === "exec" ||
    normalized === "run" ||
    normalized === "mcp_exec_command" ||
    normalized === "create_terminal"
  ) {
    return "bash";
  }
  if (
    normalized.includes("read") ||
    normalized.includes("write") ||
    normalized.includes("edit") ||
    normalized.includes("search") ||
    normalized.includes("grep") ||
    normalized.includes("glob") ||
    normalized.includes("fetch") ||
    normalized.includes("file") ||
    normalized.includes("memory")
  ) {
    return "filesystem";
  }
  return "generic";
}

function extractFilesystemTarget(summary: PermissionSummary): string | undefined {
  const raw = summary.command ?? summary.description ?? "";
  const quoted = /(["'`])([^"'`]+)\1/.exec(raw)?.[2]?.trim();
  if (quoted) return quoted;
  const pathish = /((?:\/|\.\/|\.\.\/)[^\s,)]+)/.exec(raw)?.[1]?.trim();
  if (pathish) return pathish;
  return undefined;
}

function renderPermissionBlock(
  width: number,
  summary: PermissionSummary,
  progressLabel: string,
): string[] {
  const lines: string[] = [];
  const risk = compressRiskLabel(summary.risk);
  const kind = permissionToolKind(summary.tool);
  const primaryDetail = summary.command ?? summary.description ?? summary.reason;

  lines.push(padToWidth(palette.status.warning(progressLabel), width));

  if (kind === "bash") {
    lines.push(
      padToWidth(palette.text.assistant(`${summary.tool ?? "command"} wants to run`), width),
    );
    if (summary.command) {
      lines.push(
        ...wrapPlainText(summary.command, width)
          .slice(0, 2)
          .map((line) => padToWidth(palette.text.tool(line), width)),
      );
    } else if (primaryDetail) {
      lines.push(
        ...wrapPlainText(primaryDetail, width)
          .slice(0, 2)
          .map((line) => padToWidth(palette.text.dim(line), width)),
      );
    }
  } else if (kind === "filesystem") {
    lines.push(
      padToWidth(palette.text.assistant(`${summary.tool ?? "tool"} wants to access files`), width),
    );
    const target = extractFilesystemTarget(summary);
    if (target) {
      lines.push(padToWidth(palette.text.tool(target), width));
    }
    if (primaryDetail && primaryDetail !== target) {
      lines.push(
        ...wrapPlainText(primaryDetail, width)
          .slice(0, 1)
          .map((line) => padToWidth(palette.text.dim(line), width)),
      );
    }
  } else {
    if (summary.tool) {
      lines.push(padToWidth(palette.text.assistant(`${summary.tool} requires permission`), width));
    }
    if (primaryDetail) {
      lines.push(
        ...wrapPlainText(primaryDetail, width)
          .slice(0, 2)
          .map((line) =>
            padToWidth(summary.command ? palette.text.tool(line) : palette.text.dim(line), width),
          ),
      );
    }
  }

  if (risk) {
    lines.push(
      padToWidth(
        /high/i.test(risk) ? palette.status.error(risk) : palette.status.warning(risk),
        width,
      ),
    );
  }

  return lines;
}

function normalizePermissionOptionLabel(label: string): string {
  const trimmed = label.trim();
  if (trimmed === "本次允许") return "Allow once";
  if (trimmed === "总是允许") return "Always allow";
  if (trimmed === "拒绝") return "Reject";
  return trimmed;
}

function isAllowOption(label: string): boolean {
  const normalized = label.trim();
  return normalized.includes("允许") || /^allow\b/i.test(normalized);
}

function isRejectOption(label: string): boolean {
  const normalized = label.trim();
  return (
    normalized.includes("拒绝") || /^reject\b/i.test(normalized) || /^deny\b/i.test(normalized)
  );
}

function wrapPlainText(text: string, width: number): string[] {
  const maxWidth = Math.max(12, width - 1);
  const source = text.replace(/\r/g, "").split("\n");
  const lines: string[] = [];
  for (const rawLine of source) {
    const words = rawLine.split(/\s+/).filter((word) => word.length > 0);
    if (words.length === 0) {
      lines.push("");
      continue;
    }
    let current = "";
    for (const word of words) {
      const next = current ? `${current} ${word}` : word;
      if (next.length <= maxWidth) {
        current = next;
        continue;
      }
      if (current) {
        lines.push(current);
      }
      current = word.length <= maxWidth ? word : word.slice(0, maxWidth);
    }
    if (current) {
      lines.push(current);
    }
  }
  return lines.length > 0 ? lines : [text.slice(0, maxWidth)];
}

function formatSessionTime(timestamp: number | undefined): string {
  if (!timestamp) return "-";
  return new Date(timestamp * 1000).toLocaleString();
}

function buildResumeSessionItems(sessions: SessionMeta[]): SelectItem[] {
  return sessions.map((session) => ({
    value: session.session_id,
    label: session.title?.trim() || session.session_id,
    description: `${session.session_id} · msgs ${session.message_count ?? 0} · ${formatSessionTime(session.last_message_at)}`,
  }));
}

export class AppScreen implements Component, Focusable {
  private readonly editor: Editor;
  private readonly unsubscribe: () => void;
  private readonly composerAutocompleteProvider: CombinedAutocompleteProvider;
  private _focused = false;
  private activeQuestionId: string | null = null;
  private activeQuestionIndex = 0;
  private draftBeforeQuestion = "";
  private draftAttachmentsBeforeQuestion: ComposerAttachment[] = [];
  private composerAttachments: ComposerAttachment[] = [];
  private syncingComposerInput = false;
  private pendingQuestionAnswers = new Map<number, string>();
  private questionList: SelectList | null = null;
  private resumeSessionList: ResumeSessionListState | null = null;
  private modelList: ModelListState | null = null;
  private showTodos = true;
  private showTeamPanel = false;
  private selectedTeamMemberId: string | null = null;
  private viewedTeamMemberId: string | null = null;
  private exitArmedUntil = 0;
  private transientNotice: string | null = null;
  private transientNoticeTimer: ReturnType<typeof setTimeout> | null = null;
  private animationTimer: ReturnType<typeof setInterval> | null = null;
  private animationPhase = 0;
  private runningStartedAtMs: number | null = null;
  private pendingSubmittedInput: string | null = null;
  private pendingSubmittedBaseline = 0;
  private pendingSubmittedSessionId: string | null = null;

  constructor(
    private readonly tui: TUI,
    private readonly state: CliPiAppState,
    private readonly commands: CommandService,
    private readonly exit: () => void,
  ) {
    this.editor = new Editor(tui, editorTheme, { paddingX: 1, autocompleteMaxVisible: 6 });
    this.composerAutocompleteProvider = new CombinedAutocompleteProvider(
      this.buildSlashCommands(),
      process.cwd(),
      resolveFdBinary(),
    );
    this.editor.setAutocompleteProvider(this.composerAutocompleteProvider);
    this.editor.onChange = () => {
      this.syncComposerAttachmentsFromEditor();
      this.tui.requestRender();
    };
    this.editor.onSubmit = (value) => {
      void this.handleSubmit(value);
    };
    this.unsubscribe = this.state.onChange(() => {
      this.handleStateChange();
    });
  }

  get focused(): boolean {
    return this._focused;
  }

  set focused(value: boolean) {
    this._focused = value;
    this.editor.focused = value;
  }

  dispose(): void {
    if (this.transientNoticeTimer) {
      clearTimeout(this.transientNoticeTimer);
      this.transientNoticeTimer = null;
    }
    if (this.animationTimer) {
      clearInterval(this.animationTimer);
      this.animationTimer = null;
    }
    this.unsubscribe();
  }

  invalidate(): void {
    this.editor.invalidate();
  }

  handleInput(data: string): void {
    const snapshot = this.state.getSnapshot();
    const pendingQuestion = snapshot.pendingQuestion;
    const activeQuestion =
      pendingQuestion?.questions[this.activeQuestionIndex] ?? pendingQuestion?.questions[0];
    const permissionRequest = activeQuestion
      ? isPermissionRequest(pendingQuestion?.source, activeQuestion.question)
      : false;

    if (!pendingQuestion && snapshot.isProcessing && matchesKey(data, "escape")) {
      this.state.cancel();
      return;
    }

    const handled = handleAppScreenKeyInput(data, {
      getSnapshot: () => snapshot,
      cancel: () => this.state.cancel(),
      requestExit: () => {
        const now = Date.now();
        if (now <= this.exitArmedUntil) {
          this.exit();
          return;
        }
        this.exitArmedUntil = now + 1500;
        this.transientNotice = "Press Ctrl+C again to exit";
        if (this.transientNoticeTimer) {
          clearTimeout(this.transientNoticeTimer);
        }
        this.transientNoticeTimer = setTimeout(() => {
          this.transientNotice = null;
          this.transientNoticeTimer = null;
          this.tui.requestRender();
        }, 1500);
        this.tui.requestRender();
      },
      toggleTodos: () => {
        this.showTodos = !this.showTodos;
        this.tui.requestRender();
      },
      toggleTeamPanel: () => {
        this.showTeamPanel = !this.showTeamPanel;
        if (!this.showTeamPanel) {
          this.viewedTeamMemberId = null;
        }
        this.tui.requestRender();
      },
      toggleTranscript: () => {
        const snapshot = this.state.getSnapshot();
        this.state.setTranscriptMode(
          snapshot.transcriptMode === "detailed" ? "compact" : "detailed",
        );
      },
      redraw: () => {
        this.tui.invalidate();
        this.tui.requestRender(true);
        this.transientNotice = "Screen redrawn";
        if (this.transientNoticeTimer) {
          clearTimeout(this.transientNoticeTimer);
        }
        this.transientNoticeTimer = setTimeout(() => {
          this.transientNotice = null;
          this.transientNoticeTimer = null;
          this.tui.requestRender();
        }, 1200);
        this.tui.requestRender();
      },
    });
    if (handled) {
      return;
    }

    if (permissionRequest && activeQuestion) {
      const lower = data.toLowerCase();
      if (lower === "y") {
        const allow = activeQuestion.options.find((option) => isAllowOption(option.label));
        if (allow) {
          this.handleQuestionSelection(allow.label);
          return;
        }
      }
      if (lower === "n") {
        const reject = activeQuestion.options.find((option) => isRejectOption(option.label));
        if (reject) {
          this.handleQuestionSelection(reject.label);
          return;
        }
      }
    }

    if (!snapshot.pendingQuestion && this.resumeSessionList !== null) {
      this.resumeSessionList.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.modelList !== null) {
      this.modelList.list.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (!snapshot.pendingQuestion && this.showTeamPanel) {
      if (matchesKey(data, "left")) {
        this.viewedTeamMemberId = null;
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "return")) {
        this.viewedTeamMemberId = this.selectedTeamMemberId;
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "up")) {
        this.moveTeamPanelSelection(snapshot, -1);
        this.tui.requestRender();
        return;
      }
      if (matchesKey(data, "down")) {
        this.moveTeamPanelSelection(snapshot, 1);
        this.tui.requestRender();
        return;
      }
    }

    if (snapshot.pendingQuestion && this.questionList !== null) {
      this.questionList.handleInput(data);
      this.tui.requestRender();
      return;
    }

    if (
      !snapshot.pendingQuestion &&
      this.editor.getText().length === 0 &&
      this.composerAttachments.length > 0
    ) {
      if (matchesKey(data, "backspace")) {
        this.composerAttachments = this.composerAttachments.slice(0, -1);
        this.tui.requestRender();
        return;
      }
    }

    if (!snapshot.pendingQuestion && matchesKey(data, "backspace")) {
      if (this.deleteComposerAttachmentTokenBackwards()) {
        this.tui.requestRender();
        return;
      }
    }

    this.editor.handleInput(data);
  }

  render(width: number): string[] {
    const snapshot = this.state.getSnapshot();
    const teamWorking =
      snapshot.mode === "team" &&
      isTeamWorking(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    this.editor.borderColor = snapshot.pendingQuestion
      ? palette.border.question
      : palette.border.panel;
    const editorLines = this.applyComposerTokenHighlight(
      this.applySlashCommandHint(this.editor.render(width), width),
    );
    const composerPreviewLines: string[] = [];
    const questionLines = [
      ...this.buildResumeSessionListLines(width),
      ...this.buildModelListLines(width),
      ...this.buildPendingQuestionLines(snapshot, width),
    ];
    return buildAppScreenLines(snapshot, {
      width,
      questionLines,
      editorLines,
      composerPreviewLines,
      pendingInput: this.pendingSubmittedInput ?? undefined,
      pendingInputBaseline: this.pendingSubmittedInput ? this.pendingSubmittedBaseline : undefined,
      showFullThinking: snapshot.transcriptMode === "detailed",
      showToolDetails: snapshot.transcriptMode === "detailed",
      showShortcutHelp: false,
      showTodos: this.showTodos,
      showTeamPanel: this.showTeamPanel,
      selectedTeamMemberId: this.selectedTeamMemberId,
      viewedTeamMemberId: this.viewedTeamMemberId,
      transientNotice: this.transientNotice,
      animationPhase: this.animationPhase,
      runningElapsedMs:
        (snapshot.isProcessing || teamWorking) && this.runningStartedAtMs !== null
          ? Date.now() - this.runningStartedAtMs
          : undefined,
    });
  }

  private async handleSubmit(raw: string): Promise<void> {
    const text = raw.trim();
    const content = this.composeOutgoingMessage(text);
    if (!content) return;

    const snapshot = this.state.getSnapshot();
    if (snapshot.pendingQuestion) {
      if (this.questionList === null) {
        this.state.answerQuestion(text);
      }
      this.editor.addToHistory(text);
      this.editor.setText("");
      this.composerAttachments = [];
      return;
    }

    if (text.startsWith("/")) {
      if (/^\/(?:resume|continue)\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.composerAttachments = [];
        await this.openResumeSessionList();
        return;
      }
      if (/^\/model\s*$/.test(text)) {
        this.editor.addToHistory(text);
        this.editor.setText("");
        this.composerAttachments = [];
        await this.openModelList();
        return;
      }
      await this.commands.execute(text, {
        ...this.state.getCommandContext(),
        exitApp: this.exit,
      });
      this.beginPendingSubmittedInput(text, snapshot);
      this.editor.addToHistory(text);
      this.editor.setText("");
      this.composerAttachments = [];
      try {
        await this.commands.execute(text, {
          ...this.state.getCommandContext(),
          exitApp: this.exit,
        });
      } finally {
        this.clearPendingSubmittedInput();
      }
      return;
    }

    if (snapshot.isProcessing || snapshot.isPaused) {
      this.beginPendingSubmittedInput(text, snapshot);
      const requestId = this.state.supplement(content);
      if (!requestId) {
        this.clearPendingSubmittedInput();
        this.state.addItem({
          kind: "error",
          id: `offline-${Date.now()}`,
          sessionId: snapshot.sessionId,
          content: "offline: waiting for reconnect",
          at: new Date().toISOString(),
        });
        return;
      }
      this.editor.addToHistory(text);
      this.editor.setText("");
      this.composerAttachments = [];
      return;
    }

    this.beginPendingSubmittedInput(text, snapshot);
    const requestId = this.state.sendMessage(content);
    if (!requestId) {
      this.clearPendingSubmittedInput();
      this.state.addItem({
        kind: "error",
        id: `offline-${Date.now()}`,
        sessionId: snapshot.sessionId,
        content: "offline: waiting for reconnect",
        at: new Date().toISOString(),
      });
      return;
    }

    this.editor.addToHistory(text);
    this.editor.setText("");
    this.composerAttachments = [];
  }

  private handleStateChange(): void {
    const snapshot = this.state.getSnapshot();
    if (
      this.pendingSubmittedInput &&
      (snapshot.sessionId !== this.pendingSubmittedSessionId ||
        snapshot.entries.length !== this.pendingSubmittedBaseline)
    ) {
      this.clearPendingSubmittedInput(false);
    }
    const questionId = snapshot.pendingQuestion?.requestId ?? null;
    if (questionId && questionId !== this.activeQuestionId) {
      this.activeQuestionId = questionId;
      this.activeQuestionIndex = 0;
      this.pendingQuestionAnswers.clear();
      this.draftBeforeQuestion = this.editor.getText();
      this.draftAttachmentsBeforeQuestion = [...this.composerAttachments];
      this.editor.setText("");
      this.composerAttachments = [];
      this.syncQuestionList(snapshot);
    } else if (questionId && this.activeQuestionId) {
      this.syncQuestionList(snapshot);
    } else if (!questionId && this.activeQuestionId) {
      this.activeQuestionId = null;
      this.activeQuestionIndex = 0;
      this.pendingQuestionAnswers.clear();
      this.questionList = null;
      if (!this.editor.getText() && this.draftBeforeQuestion) {
        this.editor.setText(this.draftBeforeQuestion);
      }
      this.draftBeforeQuestion = "";
      this.composerAttachments = [...this.draftAttachmentsBeforeQuestion];
      this.draftAttachmentsBeforeQuestion = [];
    }
    this.syncTeamPanelSelection(snapshot);
    this.syncAnimationLoop(snapshot);
    this.tui.requestRender();
  }

  private beginPendingSubmittedInput(
    text: string,
    snapshot: ReturnType<CliPiAppState["getSnapshot"]>,
  ): void {
    this.pendingSubmittedInput = text;
    this.pendingSubmittedBaseline = snapshot.entries.length;
    this.pendingSubmittedSessionId = snapshot.sessionId;
    this.tui.requestRender();
  }

  private clearPendingSubmittedInput(requestRender = true): void {
    this.pendingSubmittedInput = null;
    this.pendingSubmittedBaseline = 0;
    this.pendingSubmittedSessionId = null;
    if (requestRender) {
      this.tui.requestRender();
    }
  }

  private syncTeamPanelSelection(snapshot: ReturnType<CliPiAppState["getSnapshot"]>): void {
    const memberIds = orderedMemberIds(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    if (memberIds.length === 0) {
      this.selectedTeamMemberId = null;
      this.viewedTeamMemberId = null;
      return;
    }
    if (!this.selectedTeamMemberId || !memberIds.includes(this.selectedTeamMemberId)) {
      this.selectedTeamMemberId = memberIds[0] ?? null;
    }
    if (this.viewedTeamMemberId && !memberIds.includes(this.viewedTeamMemberId)) {
      this.viewedTeamMemberId = null;
    }
  }

  private moveTeamPanelSelection(
    snapshot: ReturnType<CliPiAppState["getSnapshot"]>,
    delta: -1 | 1,
  ): void {
    const memberIds = orderedMemberIds(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    if (memberIds.length === 0) {
      this.selectedTeamMemberId = null;
      return;
    }
    const currentIndex = this.selectedTeamMemberId
      ? memberIds.indexOf(this.selectedTeamMemberId)
      : 0;
    const baseIndex = currentIndex >= 0 ? currentIndex : 0;
    const nextIndex = Math.max(0, Math.min(memberIds.length - 1, baseIndex + delta));
    const nextMemberId = memberIds[nextIndex] ?? memberIds[0] ?? null;
    this.selectedTeamMemberId = nextMemberId;
    if (this.viewedTeamMemberId !== null) {
      this.viewedTeamMemberId = nextMemberId;
    }
  }

  private async openResumeSessionList(): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<SessionListPayload>("session.list", {});
      const sessions = payload.sessions ?? [];
      const total = payload.total ?? sessions.length;
      if (sessions.length === 0) {
        this.resumeSessionList = null;
        this.state.addItem(addInfo(snapshot.sessionId, "No sessions found", "r"));
        return;
      }

      const items = buildResumeSessionItems(sessions);
      const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
        minPrimaryColumnWidth: 24,
        maxPrimaryColumnWidth: 42,
      });
      list.onSelect = (item) => {
        void this.handleResumeSessionSelection(item.value);
      };
      list.onCancel = () => {
        this.resumeSessionList = null;
        this.tui.requestRender();
      };
      this.resumeSessionList = { list, sessions, total };
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.resumeSessionList = null;
      this.state.addItem(addError(snapshot.sessionId, `resume failed: ${message}`));
    }
  }

  private async handleResumeSessionSelection(sessionId: string): Promise<void> {
    const nextSessionId = sessionId.trim();
    if (!nextSessionId) {
      return;
    }
    this.resumeSessionList = null;
    this.state.updateSession(nextSessionId);
    this.state.clearEntries();
    await this.state.restoreHistory(nextSessionId);
    this.tui.requestRender();
  }

  private buildResumeSessionListLines(width: number): string[] {
    if (!this.resumeSessionList) {
      return [];
    }
    return [
      padToWidth(
        palette.status.warning(`Resume session (${this.resumeSessionList.total} total)`),
        width,
      ),
      ...this.resumeSessionList.list.render(width),
      padToWidth(palette.text.dim("↑/↓ choose · Enter resume · Esc cancel"), width),
    ];
  }

  async openModelList(): Promise<void> {
    const snapshot = this.state.getSnapshot();
    try {
      const payload = await this.state.request<ModelListPayload>("command.model", {});
      const models = payload.available_models ?? [];
      const current = payload.current ?? "unknown";
      if (models.length === 0) {
        this.modelList = null;
        this.state.addItem(addInfo(snapshot.sessionId, "No models configured", "m"));
        return;
      }

      const items = models.map((m, i) => {
        const isCurrent = m === current;
        return {
          label: `${i + 1}. ${m}${isCurrent ? " (current)" : ""}`,
          value: m,
        };
      });
      const list = new SelectList(items, Math.min(Math.max(items.length, 1), 8), selectListTheme, {
        minPrimaryColumnWidth: 24,
        maxPrimaryColumnWidth: 42,
      });
      list.onSelect = (item) => {
        void this.handleModelSelection(item.value);
      };
      list.onCancel = () => {
        this.modelList = null;
        this.tui.requestRender();
      };
      this.modelList = { list, models, current };
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.modelList = null;
      this.state.addItem(addError(snapshot.sessionId, `Failed to load models: ${message}`));
    }
  }

  private async handleModelSelection(modelName: string): Promise<void> {
    if (!modelName) {
      return;
    }
    this.modelList = null;
    try {
      const payload = await this.state.request<{
        current?: string;
        requested?: string;
        applied?: boolean;
      }>("command.model", { model: modelName });
      const nextModel = payload.current ?? modelName;
      this.state.clearEntries();
      this.state.addItem(
        addInfo(this.state.getSnapshot().sessionId, `Switched model to: ${nextModel}`, "m"),
      );
      this.tui.requestRender();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.state.addItem(addError(this.state.getSnapshot().sessionId, `Failed to switch model: ${message}`));
      this.tui.requestRender();
    }
  }

  private buildModelListLines(width: number): string[] {
    if (!this.modelList) {
      return [];
    }
    return [
      padToWidth(
        palette.status.warning(`Available models (${this.modelList.models.length} total)`),
        width,
      ),
      ...this.modelList.list.render(width),
      padToWidth(palette.text.dim("↑/↓ choose · Enter switch · Esc cancel"), width),
    ];
  }

  private syncComposerAttachmentsFromEditor(): void {
    if (this.syncingComposerInput) {
      return;
    }

    const originalText = this.editor.getText();
    const { normalizedText, attachments } = syncComposerImageTokens(
      originalText,
      this.composerAttachments,
      (path) => this.isComposerImageFile(path),
    );

    this.composerAttachments = attachments;

    if (normalizedText !== originalText) {
      this.syncingComposerInput = true;
      this.editor.setText(normalizedText);
      this.syncingComposerInput = false;
    }
  }

  private deleteComposerAttachmentTokenBackwards(): boolean {
    const cursor = this.editor.getCursor();
    const lines = this.editor.getLines();
    const currentLine = lines[cursor.line] ?? "";
    const tokenRange = findAttachmentTokenAtCursor(currentLine, cursor.col);
    if (!tokenRange) {
      return false;
    }

    const nextLine =
      `${currentLine.slice(0, tokenRange.start)}${currentLine.slice(tokenRange.end)}`.replace(
        / {2,}/g,
        " ",
      );
    const nextLines = [...lines];
    nextLines[cursor.line] = nextLine;
    const nextText = nextLines.join("\n");
    const nextCol = Math.min(tokenRange.start, nextLine.length);

    this.syncingComposerInput = true;
    this.editor.setText(nextText);
    const editorState = this.editor as unknown as {
      state?: { cursorLine: number; cursorCol: number };
    };
    if (editorState.state) {
      editorState.state.cursorLine = cursor.line;
      editorState.state.cursorCol = nextCol;
    }
    this.syncingComposerInput = false;
    this.syncComposerAttachmentsFromEditor();
    return true;
  }

  private composeOutgoingMessage(text: string): string {
    return expandComposerImageTokens(text, this.composerAttachments)
      .replace(/[ \t]{2,}/g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .trim();
  }

  private isComposerImageFile(path: string): boolean {
    if (!looksLikeImagePath(path)) {
      return false;
    }

    try {
      const stats = statSync(path);
      if (!stats.isFile()) {
        return false;
      }
      return true;
    } catch {
      return false;
    }
  }

  private syncAnimationLoop(snapshot: ReturnType<CliPiAppState["getSnapshot"]>): void {
    const hasRunningTools = snapshot.toolExecutions.some(
      (execution) => execution.tool.status === "running",
    );
    const teamWorking =
      snapshot.mode === "team" &&
      isTeamWorking(snapshot.teamMemberEvents, snapshot.teamMessageEvents);
    const teamStartedAt = teamWorkingStartedAtMs(
      snapshot.teamMemberEvents,
      snapshot.teamMessageEvents,
    );
    const shouldAnimate = snapshot.isProcessing || hasRunningTools || teamWorking;
    if (!shouldAnimate) {
      if (this.animationTimer) {
        clearInterval(this.animationTimer);
        this.animationTimer = null;
      }
      this.animationPhase = 0;
      this.runningStartedAtMs = null;
      return;
    }
    if (snapshot.isProcessing) {
      if (this.runningStartedAtMs === null) {
        this.runningStartedAtMs = Date.now();
      }
    } else if (teamWorking) {
      this.runningStartedAtMs = teamStartedAt ?? this.runningStartedAtMs ?? Date.now();
    }
    if (this.animationTimer) {
      return;
    }
    this.animationTimer = setInterval(() => {
      this.animationPhase = (this.animationPhase + 1) % 12;
      this.tui.requestRender();
    }, 220);
  }

  private applySlashCommandHint(editorLines: string[], width: number): string[] {
    const hint = this.getInlineSlashCommandHint();
    if (!hint || editorLines.length < 3) {
      return editorLines;
    }

    const contentIndex = 1;
    const line = editorLines[contentIndex] ?? "";
    const cursorIndex = line.indexOf(END_CURSOR);
    if (cursorIndex === -1) {
      return editorLines;
    }

    const hintedLine = padToWidth(
      line.replace(END_CURSOR, `${END_CURSOR}${palette.text.dim(` ${hint}`)}`),
      width,
    );

    const nextLines = [...editorLines];
    nextLines[contentIndex] = hintedLine;
    return nextLines;
  }

  private applyComposerTokenHighlight(editorLines: string[]): string[] {
    return editorLines.map((line) =>
      line.replace(COMPOSER_ATTACHMENT_TOKEN_RE, (token) => palette.text.info(token)),
    );
  }

  private getInlineSlashCommandHint(): string | null {
    const text = this.editor.getText();
    if (!text.startsWith("/") || text.includes("\n")) {
      return null;
    }

    const cursor = this.editor.getCursor();
    const lines = this.editor.getLines();
    const currentLine = lines[cursor.line] ?? "";
    if (cursor.line !== 0 || cursor.col !== currentLine.length) {
      return null;
    }

    const parsed = parseSlashCommand(text, this.commands.getAll());
    if (!parsed.command || parsed.args.trim()) {
      return null;
    }

    const usage = parsed.command.usage?.trim() ?? "";
    if (!usage.startsWith("/")) {
      return null;
    }

    const suffix = usage.replace(/^\/[^\s]+/, "").trim();
    return suffix || null;
  }

  private buildSlashCommands(): TuiSlashCommand[] {
    return this.commands.getAll().map((command) => ({
      name: command.name,
      description: command.description,
      getArgumentCompletions: command.completion
        ? async (argumentPrefix: string): Promise<AutocompleteItem[] | null> => {
            const items = await command.completion!(this.state.getCommandContext(), argumentPrefix);
            return items.map((value) => ({
              value,
              label: value,
              description: command.description,
            }));
          }
        : undefined,
    }));
  }

  private buildPendingQuestionLines(
    snapshot: ReturnType<CliPiAppState["getSnapshot"]>,
    width: number,
  ): string[] {
    const pendingQuestion = snapshot.pendingQuestion;
    if (!pendingQuestion) {
      return [];
    }

    const question =
      pendingQuestion.questions[this.activeQuestionIndex] ?? pendingQuestion.questions[0];
    if (!question) {
      return [];
    }

    const total = pendingQuestion.questions.length;
    const progress = total > 1 ? ` (${this.activeQuestionIndex + 1}/${total})` : "";
    const permissionRequest = isPermissionRequest(pendingQuestion.source, question.question);
    const lines: string[] = [];

    if (permissionRequest) {
      const summary = parsePermissionSummary(question.question);
      const title = progress ? `Permission ${this.activeQuestionIndex + 1}/${total}` : "Permission";
      lines.push(...renderPermissionBlock(width, summary, title));
    } else {
      lines.push(
        ...wrapPlainText(
          `[${question.header || "Question"}${progress}] ${question.question}`,
          width,
        ).map((line) => padToWidth(palette.status.warning(line), width)),
      );
    }

    if (this.questionList !== null) {
      lines.push(...this.questionList.render(width));
      lines.push(
        padToWidth(
          palette.text.dim(
            permissionRequest
              ? "↑/↓ review · Enter confirm · Esc reject"
              : "↑/↓ choose · Enter confirm · Esc reject",
          ),
          width,
        ),
      );
    }
    return lines;
  }

  private syncQuestionList(snapshot: ReturnType<CliPiAppState["getSnapshot"]>): void {
    const pendingQuestion = snapshot.pendingQuestion;
    if (!pendingQuestion) {
      this.questionList = null;
      return;
    }

    const question = pendingQuestion.questions[this.activeQuestionIndex];
    if (!question || question.options.length === 0) {
      this.questionList = null;
      return;
    }

    const items: SelectItem[] = question.options.map((option) => ({
      value: option.label,
      label:
        pendingQuestion.source === "permission"
          ? normalizePermissionOptionLabel(option.label)
          : option.label,
      description: option.description,
    }));
    const maxVisible = pendingQuestion.source === "permission" ? 4 : 6;
    const list = new SelectList(
      items,
      Math.min(Math.max(items.length, 1), maxVisible),
      selectListTheme,
    );
    list.onSelect = (item) => {
      this.handleQuestionSelection(item.value);
    };
    list.onCancel = () => {
      const reject = question.options.find((option) => option.label === "拒绝");
      if (reject) {
        this.handleQuestionSelection(reject.label);
      }
    };
    const selectedValue = this.pendingQuestionAnswers.get(this.activeQuestionIndex);
    const selectedIndex = selectedValue
      ? items.findIndex((item) => item.value === selectedValue)
      : 0;
    if (selectedIndex >= 0) {
      list.setSelectedIndex(selectedIndex);
    }
    this.questionList = list;
  }

  private handleQuestionSelection(label: string): void {
    const snapshot = this.state.getSnapshot();
    const pendingQuestion = snapshot.pendingQuestion;
    if (!pendingQuestion) {
      return;
    }

    this.pendingQuestionAnswers.set(this.activeQuestionIndex, label);
    if (this.activeQuestionIndex < pendingQuestion.questions.length - 1) {
      this.activeQuestionIndex += 1;
      this.syncQuestionList(this.state.getSnapshot());
      this.tui.requestRender();
      return;
    }

    const answers = pendingQuestion.questions.map((question, index) => ({
      selected_options: [
        this.pendingQuestionAnswers.get(index) ?? question.options[0]?.label ?? "",
      ].filter((value) => value.length > 0),
    }));
    this.state.submitQuestionAnswers(answers);
  }
}
