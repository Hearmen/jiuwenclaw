import type { HistoryItem } from "../types.js";
import type { AccentColorName, ThemeName } from "../../ui/theme.js";
import type { PendingQuestionItem, UserAnswer } from "../event-handlers.js";
import type { FileAttachment } from "../protocol.js";
import type { ConfigItemSchema } from "./builtins/config.js";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "reconnecting" | "auth_failed";

export enum CommandKind {
  BUILT_IN = "built-in",
}

export interface CommandSuggestion {
  value: string;
  description?: string;
  usage?: string;
  example?: string;
}

export interface CommandContext {
  /**
   * options.logAsUser=false 可用于发送内部控制消息（例如 /init 生成的 orchestration prompt），
   * 避免在 CLI/TUI 历史中渲染为普通用户输入。
   */
  sendEventOnly: (method: string, params: Record<string, unknown>) => string;
  request: <T = Record<string, unknown>>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs?: number,
  ) => Promise<T>;
  askQuestions: (questions: PendingQuestionItem[], source?: string) => Promise<UserAnswer[]>;
  sendMessage: (
    content: string,
    attachments?: FileAttachment[],
    mode?: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team",
    options?: { logAsUser?: boolean },
  ) => string | null;
  sessionId: string;
  entries: HistoryItem[];
  themeName: ThemeName;
  accentColor: AccentColorName;
  updateSession: (id: string) => void;
  addItem: (item: HistoryItem) => void;
  clearEntries: () => void;
  restoreHistory: (sessionId: string) => Promise<void>;
  exitApp: () => void;
  isProcessing: boolean;
  connectionStatus: ConnectionStatus;
  mode: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team";
  setMode: (mode: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team") => void;
  setModel: (name: string) => void;
  setThemeName: (theme: ThemeName) => void;
  setAccentColor: (color: AccentColorName) => void;
  transcriptMode: "compact" | "detailed";
  setTranscriptMode: (mode: "compact" | "detailed") => void;
  transcriptFoldMode: "none" | "tools" | "thinking" | "all";
  setTranscriptFoldMode: (mode: "none" | "tools" | "thinking" | "all") => void;
  collapsedToolGroupCount: number;
  collapseToolGroups: (scope: "last" | "all") => void;
  expandToolGroups: (scope: "last" | "all") => void;
  sessionTitle: string;
  setSessionTitle: (title: string) => void;
  // Trusted directories management (global, not session-specific)
  getTrustedDirs: () => string[];
  validateDirPath: (path: string) => "valid" | "not_found" | "invalid" | "no_access";
  addTrustedDir: (path: string) => "added" | "exists" | "not_found" | "invalid" | "no_access";
  setTrustedDir: (path: string) => "set" | "not_found" | "invalid" | "no_access";
  removeTrustedDir: (path: string) => boolean;
  clearTrustedDirs: () => void;
  // Workspace directory (current working directory)
  getWorkspaceDir: () => string | undefined;
  enterConfigEditor?: (
    focusKey?: string,
    configPayload?: Record<string, unknown> & { schema?: ConfigItemSchema[] },
  ) => void;
}

export interface SlashCommand {
  name: string;
  altNames?: string[];
  description: string;
  usage?: string;
  example?: string;
  hidden?: boolean;
  isSafeConcurrent?: boolean;
  kind: CommandKind;
  action: (ctx: CommandContext, args: string) => void | Promise<void>;
  completion?: (ctx: CommandContext, partial: string) => string[] | Promise<string[]>;
  takesArgs?: boolean;
  subCommands?: SlashCommand[];
}

export type SlashCommandListProvider = () => readonly SlashCommand[];
