import {
  CombinedAutocompleteProvider,
  Editor,
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
import { editorTheme, palette } from "./theme.js";

const END_CURSOR = "\x1b[7m \x1b[0m";

export class AppScreen implements Component, Focusable {
  private readonly editor: Editor;
  private readonly unsubscribe: () => void;
  private _focused = false;
  private activeQuestionId: string | null = null;
  private draftBeforeQuestion = "";
  private showFullThinking = false;
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

    this.editor.handleInput(data);
  }

  render(width: number): string[] {
    const snapshot = this.state.getSnapshot();
    this.editor.borderColor = snapshot.pendingQuestion
      ? palette.border.question
      : palette.border.active;
    const editorLines = this.applySlashCommandHint(this.editor.render(width), width);
    return buildAppScreenLines(snapshot, {
      width,
      terminalRows: this.tui.terminal.rows,
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
      this.state.answerQuestion(text);
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
      this.draftBeforeQuestion = this.editor.getText();
      this.editor.setText("");
    } else if (!questionId && this.activeQuestionId) {
      this.activeQuestionId = null;
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
}
