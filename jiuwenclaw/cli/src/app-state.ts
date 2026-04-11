import type { CommandContext } from "./core/commands/types.js";
import { isIgnorableHistoryRestoreError } from "./core/app-state-helpers.js";
import { generateSessionId } from "./core/session-state.js";
import {
  handleIncomingFrame,
  type AppEventDelegate,
  type PendingQuestion,
  type UserAnswer,
} from "./core/event-handlers.js";
import { isEventFrame, type EventFrame } from "./core/protocol.js";
import {
  StreamingState,
  type ContextCompressionStats,
  type HistoryItem,
  type SubtaskState,
  type TodoItem,
} from "./core/types.js";
import {
  getCurrentAccentColor,
  getCurrentThemeName,
  setCurrentAccentColor,
  setCurrentThemeName,
  type AccentColorName,
  type ThemeName,
} from "./ui/theme.js";
import { type ConnectionStatus, WsClient } from "./core/ws-client.js";

export interface AppSnapshot {
  connectionStatus: ConnectionStatus;
  sessionId: string;
  mode: "plan" | "agent" | "team";
  themeName: ThemeName;
  accentColor: AccentColorName;
  transcriptMode: "compact" | "detailed";
  transcriptFoldMode: "none" | "tools" | "thinking" | "all";
  collapsedToolGroupIds: Set<string>;
  entries: HistoryItem[];
  streamingState: StreamingState;
  pendingQuestion: PendingQuestion | null;
  lastError: string | null;
  isProcessing: boolean;
  isPaused: boolean;
  activeSubtasks: SubtaskState[];
  todos: TodoItem[];
  evolutionStatus: "idle" | "running";
  contextCompression: ContextCompressionStats | null;
}

export class CliPiAppState {
  private listeners = new Set<() => void>();
  private entries: HistoryItem[] = [];
  private connectionStatus: ConnectionStatus = "idle";
  private sessionId: string;
  private mode: "plan" | "agent" | "team" = "plan";
  private themeName: ThemeName = getCurrentThemeName();
  private accentColor: AccentColorName = getCurrentAccentColor();
  private transcriptMode: "compact" | "detailed" = "detailed";
  private transcriptFoldMode: "none" | "tools" | "thinking" | "all" = "none";
  private collapsedToolGroupIds = new Set<string>();
  private streamingState: StreamingState = StreamingState.Idle;
  private pendingQuestion: PendingQuestion | null = null;
  private lastError: string | null = null;
  private activeSubtasks = new Map<string, SubtaskState>();
  private todos: TodoItem[] = [];
  private evolutionStatus: "idle" | "running" = "idle";
  private contextCompression: ContextCompressionStats | null = null;
  private historyEntries: HistoryItem[] = [];
  private historyFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private historyRequestToken = 0;
  private unlistenStatus: (() => void) | null = null;
  private unlistenFrames: (() => void) | null = null;
  private readonly eventDelegate: AppEventDelegate = {
    getConnectionStatus: () => this.connectionStatus,
    getSessionId: () => this.sessionId,
    setSessionId: (sessionId) => {
      this.sessionId = sessionId;
    },
    setMode: (mode) => {
      this.mode = mode;
    },
    getEntries: () => this.entries,
    setEntries: (entries) => {
      this.entries = entries;
    },
    setStreamingState: (state) => {
      this.streamingState = state;
    },
    setPendingQuestion: (question) => {
      this.pendingQuestion = question;
    },
    setLastError: (error) => {
      this.lastError = error;
    },
    getActiveSubtasks: () => this.activeSubtasks,
    setTodos: (todos) => {
      this.todos = todos;
    },
    setEvolutionStatus: (status) => {
      this.evolutionStatus = status;
    },
    setContextCompression: (stats) => {
      this.contextCompression = stats;
    },
    pushHistoryEntry: (entry) => {
      this.historyEntries.push(entry);
    },
    scheduleHistoryFlush: () => {
      this.scheduleHistoryFlush();
    },
    safeRestoreHistory: (sessionId) => {
      this.safeRestoreHistory(sessionId);
    },
  };

  constructor(
    private readonly wsClient: WsClient,
    cliSession?: string,
  ) {
    this.sessionId = cliSession || generateSessionId();
  }

  start(): void {
    this.unlistenStatus = this.wsClient.onStatusChange((status) => {
      this.connectionStatus = status;
      this.emitChange();
    });

    this.unlistenFrames = this.wsClient.onFrame((frame) => {
      this.handleFrame(frame);
    });

    this.wsClient.connect();
  }

  stop(): void {
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
      this.historyFlushTimer = null;
    }
    this.unlistenStatus?.();
    this.unlistenStatus = null;
    this.unlistenFrames?.();
    this.unlistenFrames = null;
    this.wsClient.disconnect();
  }

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  getSnapshot(): AppSnapshot {
    const isProcessing =
      this.streamingState === StreamingState.Responding ||
      this.streamingState === StreamingState.WaitingForConfirmation;
    return {
      connectionStatus: this.connectionStatus,
      sessionId: this.sessionId,
      mode: this.mode,
      themeName: this.themeName,
      accentColor: this.accentColor,
      transcriptMode: this.transcriptMode,
      transcriptFoldMode: this.transcriptFoldMode,
      collapsedToolGroupIds: new Set(this.collapsedToolGroupIds),
      entries: [...this.entries],
      streamingState: this.streamingState,
      pendingQuestion: this.pendingQuestion
        ? {
            ...this.pendingQuestion,
            questions: this.pendingQuestion.questions.map((question) => ({
              ...question,
              options: [...question.options],
            })),
          }
        : null,
      lastError: this.lastError,
      isProcessing,
      isPaused: this.streamingState === StreamingState.Paused,
      activeSubtasks: [...this.activeSubtasks.values()].sort((a, b) => a.index - b.index),
      todos: [...this.todos],
      evolutionStatus: this.evolutionStatus,
      contextCompression: this.contextCompression ? { ...this.contextCompression } : null,
    };
  }

  getCommandContext(): CommandContext {
    const snapshot = this.getSnapshot();
    return {
      sendEventOnly: this.sendEventOnly,
      request: this.request,
      sendMessage: this.sendMessage,
      sessionId: snapshot.sessionId,
      entries: snapshot.entries,
      themeName: snapshot.themeName,
      accentColor: snapshot.accentColor,
      updateSession: this.updateSession,
      addItem: this.addItem,
      clearEntries: this.clearEntries,
      restoreHistory: this.restoreHistory,
      exitApp: () => {
        // AppScreen injects the real exit handler when executing slash commands.
      },
      isProcessing: snapshot.isProcessing,
      connectionStatus: snapshot.connectionStatus,
      mode: snapshot.mode,
      setMode: this.setMode,
      setThemeName: this.setThemeName,
      setAccentColor: this.setAccentColor,
      transcriptMode: snapshot.transcriptMode,
      setTranscriptMode: this.setTranscriptMode,
      transcriptFoldMode: snapshot.transcriptFoldMode,
      setTranscriptFoldMode: this.setTranscriptFoldMode,
      collapsedToolGroupCount: snapshot.collapsedToolGroupIds.size,
      collapseToolGroups: this.collapseToolGroups,
      expandToolGroups: this.expandToolGroups,
    };
  }

  readonly sendEventOnly = (method: string, params: Record<string, unknown>): string => {
    const id = `tui_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 6)}`;
    this.wsClient.send({
      type: "req",
      id,
      method,
      params: { ...params, session_id: params.session_id ?? this.sessionId },
    });
    return id;
  };

  readonly request = async <T = Record<string, unknown>>(
    method: string,
    params: Record<string, unknown>,
  ): Promise<T> => {
    const id = `tui_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 6)}`;
    const response = await this.wsClient.request(id, method, {
      ...params,
      session_id: params.session_id ?? this.sessionId,
    });
    return response.payload as T;
  };

  readonly updateSession = (newId: string): void => {
    this.sessionId = newId;
    this.emitChange();
  };

  readonly addItem = (item: HistoryItem): void => {
    this.entries = [...this.entries, item];
    this.lastError = item.kind === "error" ? item.content : this.lastError;
    this.emitChange();
  };

  readonly clearEntries = (): void => {
    this.entries = [];
    this.pendingQuestion = null;
    this.lastError = null;
    this.streamingState = StreamingState.Idle;
    this.collapsedToolGroupIds.clear();
    this.activeSubtasks.clear();
    this.todos = [];
    this.evolutionStatus = "idle";
    this.contextCompression = null;
    this.historyEntries = [];
    this.emitChange();
  };

  readonly setMode = (mode: "plan" | "agent" | "team"): void => {
    if (this.mode !== mode) {
      this.mode = mode;
      this.emitChange();
    }
  };

  readonly setThemeName = (theme: ThemeName): void => {
    if (this.themeName !== theme) {
      this.themeName = theme;
      setCurrentThemeName(theme);
      this.emitChange();
    }
  };

  readonly setAccentColor = (color: AccentColorName): void => {
    if (this.accentColor !== color) {
      this.accentColor = color;
      setCurrentAccentColor(color);
      this.emitChange();
    }
  };

  readonly setTranscriptMode = (mode: "compact" | "detailed"): void => {
    if (this.transcriptMode !== mode) {
      this.transcriptMode = mode;
      this.emitChange();
    }
  };

  readonly setTranscriptFoldMode = (mode: "none" | "tools" | "thinking" | "all"): void => {
    if (this.transcriptFoldMode !== mode) {
      this.transcriptFoldMode = mode;
      this.emitChange();
    }
  };

  readonly collapseToolGroups = (scope: "last" | "all"): void => {
    const ids = this.entries
      .filter(
        (entry): entry is Extract<HistoryItem, { kind: "tool_group" }> =>
          entry.kind === "tool_group",
      )
      .map((entry) => entry.id);
    if (scope === "all") {
      this.collapsedToolGroupIds = new Set(ids);
    } else {
      const last = ids[ids.length - 1];
      if (last) {
        this.collapsedToolGroupIds = new Set(this.collapsedToolGroupIds);
        this.collapsedToolGroupIds.add(last);
      }
    }
    this.emitChange();
  };

  readonly expandToolGroups = (scope: "last" | "all"): void => {
    if (scope === "all") {
      this.collapsedToolGroupIds.clear();
    } else {
      const ids = this.entries
        .filter(
          (entry): entry is Extract<HistoryItem, { kind: "tool_group" }> =>
            entry.kind === "tool_group",
        )
        .map((entry) => entry.id);
      const last = ids[ids.length - 1];
      if (last) {
        this.collapsedToolGroupIds = new Set(this.collapsedToolGroupIds);
        this.collapsedToolGroupIds.delete(last);
      }
    }
    this.emitChange();
  };

  sendMessage(content: string, modeOverride?: "plan" | "agent" | "team"): string | null {
    if (this.connectionStatus !== "connected") return null;
    const mode = modeOverride ?? this.mode;
    const requestId = this.sendEventOnly("chat.send", { content, query: content, mode });
    this.lastError = null;
    this.entries = [
      ...this.entries,
      {
        kind: "user",
        id: `user-${requestId}`,
        sessionId: this.sessionId,
        content,
        at: new Date().toISOString(),
      },
      {
        kind: "assistant",
        id: `assistant-${requestId}`,
        sessionId: this.sessionId,
        content: "",
        streaming: true,
        requestId,
        at: new Date().toISOString(),
      },
    ];
    this.streamingState = StreamingState.Responding;
    this.emitChange();
    return requestId;
  }

  cancel(): void {
    this.sendEventOnly("chat.interrupt", { intent: "cancel" });
  }

  resume(): void {
    this.sendEventOnly("chat.resume", {});
  }

  submitQuestionAnswers(answers: UserAnswer[]): void {
    if (!this.pendingQuestion) return;
    if (this.pendingQuestion.source === "permission_interrupt") {
      this.sendEventOnly("chat.send", {
        query: "",
        request_id: this.pendingQuestion.requestId,
        answers,
      });
    } else {
      this.sendEventOnly("chat.user_answer", {
        request_id: this.pendingQuestion.requestId,
        answers,
      });
    }
    this.pendingQuestion = null;
    this.streamingState = StreamingState.Idle;
    this.emitChange();
  }

  answerQuestion(answer: string): void {
    this.submitQuestionAnswers([{ selected_options: [answer], custom_input: answer }]);
  }

  async restoreHistory(targetSessionId: string): Promise<void> {
    this.historyRequestToken += 1;
    const requestToken = this.historyRequestToken;
    this.historyEntries = [];
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
      this.historyFlushTimer = null;
    }
    await this.request("history.get", { session_id: targetSessionId });
    setTimeout(() => {
      if (requestToken !== this.historyRequestToken) return;
      this.entries = [...this.historyEntries];
      this.emitChange();
    }, 80);
  }

  private emitChange(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }

  private handleFrame(frame: unknown): void {
    if (!isEventFrame(frame as EventFrame | any)) return;
    const typedFrame = frame as EventFrame;
    if (handleIncomingFrame(this.eventDelegate, typedFrame)) {
      this.emitChange();
    }
  }

  private scheduleHistoryFlush(): void {
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
    }
    this.historyFlushTimer = setTimeout(() => {
      this.historyFlushTimer = null;
      this.entries = [...this.historyEntries];
      this.emitChange();
    }, 50);
  }

  private safeRestoreHistory(sessionId: string): void {
    void (async () => {
      try {
        await this.restoreHistory(sessionId);
      } catch (error) {
        if (isIgnorableHistoryRestoreError(error)) {
          return;
        }
        this.lastError = error instanceof Error ? error.message : String(error);
        this.emitChange();
      }
    })();
  }
}
