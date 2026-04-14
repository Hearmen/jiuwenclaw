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
} from "@mariozechner/pi-tui";
import type { CliPiAppState } from "../app-state.js";
import { CommandService, parseSlashCommand } from "../core/commands/CommandService.js";
import { handleAppScreenKeyInput } from "./keymap.js";
import { buildAppScreenLines } from "./screen-layout.js";
import { padToWidth } from "./rendering/text.js";
import { editorTheme, palette, selectListTheme } from "./theme.js";

const END_CURSOR = "\x1b[7m \x1b[0m";
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

export class AppScreen implements Component, Focusable {
  private readonly editor: Editor;
  private readonly unsubscribe: () => void;
  private _focused = false;
  private activeQuestionId: string | null = null;
  private activeQuestionIndex = 0;
  private draftBeforeQuestion = "";
  private pendingQuestionAnswers = new Map<number, string>();
  private questionList: SelectList | null = null;
  private showFullThinking = true;
  private showToolDetails = false;
  private showShortcutHelp = false;
  private exitArmedUntil = 0;
  private transientNotice: string | null = null;
  private transientNoticeTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly tui: TUI,
    private readonly state: CliPiAppState,
    private readonly commands: CommandService,
    private readonly exit: () => void,
  ) {
    this.editor = new Editor(tui, editorTheme, { paddingX: 1, autocompleteMaxVisible: 6 });
    this.editor.setAutocompleteProvider(
      new CombinedAutocompleteProvider(this.buildSlashCommands(), process.cwd()),
    );
    this.editor.onChange = () => {
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
    this.unsubscribe();
  }

  invalidate(): void {
    this.editor.invalidate();
  }

  handleInput(data: string): void {
    const handled = handleAppScreenKeyInput(data, {
      getSnapshot: () => this.state.getSnapshot(),
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
      toggleThinking: () => {
        this.showFullThinking = !this.showFullThinking;
        this.tui.requestRender();
      },
      toggleToolDetails: () => {
        this.showToolDetails = !this.showToolDetails;
        this.tui.requestRender();
      },
      toggleShortcutHelp: () => {
        this.showShortcutHelp = !this.showShortcutHelp;
        this.tui.requestRender();
      },
    });
    if (handled) {
      return;
    }

    const snapshot = this.state.getSnapshot();
    if (snapshot.pendingQuestion && this.questionList !== null) {
      this.questionList.handleInput(data);
      this.tui.requestRender();
      return;
    }

    this.editor.handleInput(data);
  }

  render(width: number): string[] {
    const snapshot = this.state.getSnapshot();
    this.editor.borderColor = snapshot.pendingQuestion
      ? palette.border.question
      : palette.border.active;
    const editorLines = this.applySlashCommandHint(this.editor.render(width), width);
    const questionLines = this.buildPendingQuestionLines(snapshot, width);
    return buildAppScreenLines(snapshot, {
      width,
      questionLines,
      editorLines,
      showFullThinking: this.showFullThinking,
      showToolDetails: this.showToolDetails,
      showShortcutHelp: this.showShortcutHelp,
      transientNotice: this.transientNotice,
    });
  }

  private async handleSubmit(raw: string): Promise<void> {
    const text = raw.trim();
    if (!text) return;

    const snapshot = this.state.getSnapshot();
    if (snapshot.pendingQuestion) {
      if (this.questionList === null) {
        this.state.answerQuestion(text);
      }
      this.editor.addToHistory(text);
      this.editor.setText("");
      return;
    }

    if (text.startsWith("/")) {
      await this.commands.execute(text, {
        ...this.state.getCommandContext(),
        exitApp: this.exit,
      });
      this.editor.addToHistory(text);
      this.editor.setText("");
      return;
    }

    if (snapshot.isProcessing) {
      this.state.addItem({
        kind: "error",
        id: `busy-${Date.now()}`,
        sessionId: snapshot.sessionId,
        content: "session is busy, run /cancel first",
        at: new Date().toISOString(),
      });
      return;
    }

    const requestId = this.state.sendMessage(text);
    if (!requestId) {
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
  }

  private handleStateChange(): void {
    const snapshot = this.state.getSnapshot();
    const questionId = snapshot.pendingQuestion?.requestId ?? null;
    if (questionId && questionId !== this.activeQuestionId) {
      this.activeQuestionId = questionId;
      this.activeQuestionIndex = 0;
      this.pendingQuestionAnswers.clear();
      this.draftBeforeQuestion = this.editor.getText();
      this.editor.setText("");
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
    }
    this.tui.requestRender();
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
      lines.push(
        padToWidth(
          palette.status.warning(
            `Permission${progress ? ` ${this.activeQuestionIndex + 1}/${total}` : ""}`,
          ),
          width,
        ),
      );
      if (summary.tool) {
        lines.push(padToWidth(palette.text.assistant(`${summary.tool} wants to run`), width));
      }
      const primaryDetail = summary.command ?? summary.description ?? summary.reason;
      if (primaryDetail) {
        lines.push(
          ...wrapPlainText(primaryDetail, width)
            .slice(0, 2)
            .map((line) =>
              padToWidth(summary.command ? palette.text.tool(line) : palette.text.dim(line), width),
            ),
        );
      }
    } else {
      lines.push(
        ...wrapPlainText(
          `[${question.header || "Question"}${progress}] ${question.question}`,
          width,
        ).map((line) => padToWidth(palette.status.warning(line), width)),
      );
    }

    if (this.questionList !== null) {
      lines.push(" ".repeat(width));
      lines.push(...this.questionList.render(width));
      lines.push(
        padToWidth(
          palette.text.dim(
            permissionRequest
              ? "Use ↑/↓ to review options, Enter to approve, Esc to reject"
              : "Use ↑/↓ to choose, Enter to confirm, Esc to reject",
          ),
          width,
        ),
      );
    }

    lines.push(" ".repeat(width));
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
      label: option.label,
      description: option.description,
    }));
    const list = new SelectList(items, Math.min(Math.max(items.length, 1), 6), selectListTheme);
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
