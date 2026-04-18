import type { CommandContext } from "./core/commands/types.js";
import {
  computeTimeoutAt,
  isIgnorableHistoryRestoreError,
  rebuildToolExecutionStateFromEntries,
  upsertToolGroupDisplay,
} from "./core/app-state-helpers.js";
import {
  applyToolResult,
  createToolCallDisplay,
  parseHistoryFrame,
} from "./core/history-parser.js";
import { generateSessionId } from "./core/session-state.js";
import { getToolGroupIds } from "./core/transcript-timeline.js";
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
  type TeamMemberEvent,
  type TeamMessageEvent,
  type TeamTaskEvent,
  type TodoItem,
  type ToolCallDisplay,
  type ToolExecution,
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
  mode: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team";
  themeName: ThemeName;
  accentColor: AccentColorName;
  transcriptMode: "compact" | "detailed";
  transcriptFoldMode: "none" | "tools" | "thinking" | "all";
  collapsedToolGroupIds: Set<string>;
  entries: HistoryItem[];
  toolExecutions: ToolExecution[];
  streamingState: StreamingState;
  pendingQuestion: PendingQuestion | null;
  lastError: string | null;
  isProcessing: boolean;
  isPaused: boolean;
  activeSubtasks: SubtaskState[];
  todos: TodoItem[];
  teamMemberEvents: TeamMemberEvent[];
  teamTaskEvents: TeamTaskEvent[];
  teamMessageEvents: TeamMessageEvent[];
  evolutionStatus: "idle" | "running";
  contextCompression: ContextCompressionStats | null;
}

export class CliPiAppState {
  private listeners = new Set<() => void>();
  private entries: HistoryItem[] = [];
  private connectionStatus: ConnectionStatus = "idle";
  private sessionId: string;
  private mode: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team" =
    "agent.plan";
  private themeName: ThemeName = getCurrentThemeName();
  private accentColor: AccentColorName = getCurrentAccentColor();
  private transcriptMode: "compact" | "detailed" = "compact";
  private transcriptFoldMode: "none" | "tools" | "thinking" | "all" = "none";
  private collapsedToolGroupIds = new Set<string>();
  private streamingState: StreamingState = StreamingState.Idle;
  private pendingQuestion: PendingQuestion | null = null;
  private lastError: string | null = null;
  private activeSubtasks = new Map<string, SubtaskState>();
  private todos: TodoItem[] = [];
  private teamMemberEvents: TeamMemberEvent[] = [];
  private teamTaskEvents: TeamTaskEvent[] = [];
  private teamMessageEvents: TeamMessageEvent[] = [];
  private evolutionStatus: "idle" | "running" = "idle";
  private contextCompression: ContextCompressionStats | null = null;
  private toolExecutions = new Map<string, ToolExecution>();
  private toolExecutionOrder: string[] = [];
  private orphanToolResults = new Map<
    string,
    { tool: ToolCallDisplay; requestId?: string; updatedAt: string }
  >();
  private historyEntries: HistoryItem[] = [];
  private historyFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private toolTimeoutTimer: ReturnType<typeof setTimeout> | null = null;
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
    appendTeamMemberEvent: (event) => {
      this.teamMemberEvents = [...this.teamMemberEvents.slice(-99), event];
    },
    appendTeamTaskEvent: (event) => {
      this.teamTaskEvents = [...this.teamTaskEvents.slice(-99), event];
    },
    appendTeamMessageEvent: (event) => {
      this.teamMessageEvents = [...this.teamMessageEvents.slice(-99), event];
    },
    setEvolutionStatus: (status) => {
      this.evolutionStatus = status;
    },
    setContextCompression: (stats) => {
      this.contextCompression = stats;
    },
    addToolCallPayload: (payload, sessionId, requestId, startedAt) => {
      this.addToolCallPayload(payload, sessionId, requestId, startedAt);
    },
    addToolResultPayload: (payload, sessionId, requestId, updatedAt) => {
      this.addToolResultPayload(payload, sessionId, requestId, updatedAt);
    },
    addSyntheticToolExecution: (tool, sessionId, requestId, at) => {
      this.addSyntheticToolExecution(tool, sessionId, requestId, at);
    },
    clearToolExecutionState: () => {
      this.clearToolExecutionState();
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
    if (this.toolTimeoutTimer) {
      clearTimeout(this.toolTimeoutTimer);
      this.toolTimeoutTimer = null;
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
      toolExecutions: this.toolExecutionOrder
        .map((toolCallId) => this.toolExecutions.get(toolCallId))
        .filter((item): item is ToolExecution => Boolean(item)),
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
      teamMemberEvents: [...this.teamMemberEvents],
      teamTaskEvents: [...this.teamTaskEvents],
      teamMessageEvents: [...this.teamMessageEvents],
      evolutionStatus: this.evolutionStatus,
      contextCompression: this.contextCompression ? { ...this.contextCompression } : null,
    };
  }

  getCommandContext(): CommandContext {
    const snapshot = this.getSnapshot();
    const toolGroupIds = getToolGroupIds(snapshot.entries, snapshot.toolExecutions);
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
      collapsedToolGroupCount: toolGroupIds.filter((id) => snapshot.collapsedToolGroupIds.has(id))
        .length,
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
    timeoutMs?: number,
  ): Promise<T> => {
    const id = `tui_${Date.now().toString(16)}_${Math.random().toString(36).slice(2, 6)}`;
    const response = await this.wsClient.request(id, method, {
      ...params,
      session_id: params.session_id ?? this.sessionId,
    }, timeoutMs ?? 30000);
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
    this.teamMemberEvents = [];
    this.teamTaskEvents = [];
    this.teamMessageEvents = [];
    this.evolutionStatus = "idle";
    this.contextCompression = null;
    this.clearToolExecutionState();
    this.historyEntries = [];
    this.emitChange();
  };

  readonly setMode = (
    mode: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team",
  ): void => {
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
    const ids = getToolGroupIds(
      this.entries,
      this.toolExecutionOrder
        .map((toolCallId) => this.toolExecutions.get(toolCallId))
        .filter((item): item is ToolExecution => Boolean(item)),
    );
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
      const ids = getToolGroupIds(
        this.entries,
        this.toolExecutionOrder
          .map((toolCallId) => this.toolExecutions.get(toolCallId))
          .filter((item): item is ToolExecution => Boolean(item)),
      );
      const last = ids[ids.length - 1];
      if (last) {
        this.collapsedToolGroupIds = new Set(this.collapsedToolGroupIds);
        this.collapsedToolGroupIds.delete(last);
      }
    }
    this.emitChange();
  };

  readonly sendMessage = (
    content: string,
    modeOverride?: "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team",
  ): string | null => {
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
    ];
    this.streamingState = StreamingState.Responding;
    this.emitChange();
    return requestId;
  };

  supplement(content: string): string | null {
    if (this.connectionStatus !== "connected") return null;
    const trimmed = content.trim();
    if (!trimmed) return null;
    const requestId = this.sendEventOnly("chat.interrupt", {
      intent: "supplement",
      new_input: trimmed,
    });
    this.lastError = null;
    this.entries = [
      ...this.entries,
      {
        kind: "user",
        id: `user-${requestId}`,
        sessionId: this.sessionId,
        content: trimmed,
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

  readonly restoreHistory = async (targetSessionId: string): Promise<void> => {
    this.historyRequestToken += 1;
    const requestToken = this.historyRequestToken;
    this.historyEntries = [];
    this.clearToolExecutionState();
    if (this.historyFlushTimer) {
      clearTimeout(this.historyFlushTimer);
      this.historyFlushTimer = null;
    }
    const payload = await this.request<{
      messages?: unknown[];
      total_pages?: number;
      page_idx?: number;
    }>("history.get", { session_id: targetSessionId, page_idx: 1 });
    if (Array.isArray(payload.messages)) {
      for (const message of payload.messages) {
        if (!message || typeof message !== "object" || Array.isArray(message)) {
          continue;
        }
        const entry = parseHistoryFrame({
          type: "event",
          event: "history.message",
          payload: {
            session_id: targetSessionId,
            message: message as Record<string, unknown>,
            total_pages: payload.total_pages,
            page_idx: payload.page_idx,
          },
        });
        if (entry) {
          this.historyEntries.push(entry);
        }
      }
    }
    setTimeout(() => {
      if (requestToken !== this.historyRequestToken) return;
      this.entries = [...this.historyEntries];
      this.rebuildToolExecutionState();
      this.emitChange();
    }, 80);
  };

  private readonly clearToolExecutionState = (): void => {
    if (this.toolTimeoutTimer) {
      clearTimeout(this.toolTimeoutTimer);
      this.toolTimeoutTimer = null;
    }
    this.toolExecutions = new Map();
    this.toolExecutionOrder = [];
    this.orphanToolResults = new Map();
  };

  private scheduleToolTimeoutCheck(): void {
    if (this.toolTimeoutTimer) {
      clearTimeout(this.toolTimeoutTimer);
      this.toolTimeoutTimer = null;
    }
    let nextTimeoutMs = Number.POSITIVE_INFINITY;
    const now = Date.now();
    for (const execution of this.toolExecutions.values()) {
      if (execution.tool.status !== "running") {
        continue;
      }
      const timeoutMs = Date.parse(execution.timeoutAt);
      if (Number.isNaN(timeoutMs)) {
        continue;
      }
      nextTimeoutMs = Math.min(nextTimeoutMs, timeoutMs);
    }
    if (!Number.isFinite(nextTimeoutMs)) {
      return;
    }
    const delay = Math.max(0, nextTimeoutMs - now);
    this.toolTimeoutTimer = setTimeout(() => {
      this.toolTimeoutTimer = null;
      if (this.markTimedOutExecutions()) {
        this.emitChange();
      } else {
        this.scheduleToolTimeoutCheck();
      }
    }, delay + 10);
  }

  private markTimedOutExecutions(): boolean {
    const nowIso = new Date().toISOString();
    const nowMs = Date.parse(nowIso);
    let changed = false;
    for (const [toolCallId, execution] of this.toolExecutions) {
      if (execution.tool.status !== "running") {
        continue;
      }
      const timeoutMs = Date.parse(execution.timeoutAt);
      if (Number.isNaN(timeoutMs) || timeoutMs > nowMs) {
        continue;
      }
      const nextTool: ToolCallDisplay = {
        ...execution.tool,
        status: "timeout",
        isError: false,
      };
      this.toolExecutions.set(toolCallId, {
        ...execution,
        tool: nextTool,
        updatedAt: nowIso,
        timedOutAt: nowIso,
      });
      this.entries = upsertToolGroupDisplay(
        this.entries,
        execution.sessionId,
        execution.requestId,
        nextTool,
      );
      changed = true;
    }
    this.scheduleToolTimeoutCheck();
    return changed;
  }

  private addToolCallPayload(
    payload: Record<string, unknown>,
    sessionId: string,
    requestId?: string,
    startedAt?: string,
  ): void {
    const tool = createToolCallDisplay(payload);
    if (!tool.callId) {
      return;
    }

    if (this.toolExecutions.has(tool.callId)) {
      return;
    }

    const started = startedAt ?? new Date().toISOString();
    const orphan = this.orphanToolResults.get(tool.callId);
    const nextTool = orphan
      ? {
          ...tool,
          status: orphan.tool.status,
          result: orphan.tool.result,
          summary: orphan.tool.summary,
          isError: orphan.tool.isError,
        }
      : tool;

    this.toolExecutions.set(tool.callId, {
      toolCallId: tool.callId,
      sessionId,
      requestId: requestId ?? orphan?.requestId,
      tool: nextTool,
      startedAt: started,
      updatedAt: orphan?.updatedAt ?? started,
      timeoutAt: computeTimeoutAt(started),
      resultArrivedAfterTimeout: false,
    });
    this.toolExecutionOrder.push(tool.callId);
    if (orphan) {
      this.orphanToolResults.delete(tool.callId);
    }
    this.entries = upsertToolGroupDisplay(
      this.entries,
      sessionId,
      requestId ?? orphan?.requestId,
      nextTool,
    );
    this.scheduleToolTimeoutCheck();
  }

  private addToolResultPayload(
    payload: Record<string, unknown>,
    sessionId: string,
    requestId?: string,
    updatedAt?: string,
  ): void {
    const baseTool = createToolCallDisplay(payload);
    const resultTool = applyToolResult(baseTool, payload);
    if (!resultTool.callId) {
      return;
    }

    const nowIso = updatedAt ?? new Date().toISOString();
    const existing = this.toolExecutions.get(resultTool.callId);
    if (!existing) {
      this.orphanToolResults.set(resultTool.callId, {
        tool: resultTool,
        requestId,
        updatedAt: nowIso,
      });
      this.entries = upsertToolGroupDisplay(this.entries, sessionId, requestId, resultTool);
      return;
    }

    const wasTimedOut = existing.tool.status === "timeout";
    const nextTool: ToolCallDisplay = {
      ...existing.tool,
      ...resultTool,
      arguments: existing.tool.arguments,
      description: existing.tool.description ?? resultTool.description,
      formattedArgs: existing.tool.formattedArgs ?? resultTool.formattedArgs,
      status: wasTimedOut && !resultTool.isError ? "timeout" : resultTool.status,
      summary: wasTimedOut
        ? resultTool.summary
          ? `${resultTool.summary} (after timeout)`
          : resultTool.isError
            ? "failed after timeout"
            : "completed after timeout"
        : resultTool.summary,
    };
    this.toolExecutions.set(resultTool.callId, {
      ...existing,
      requestId: existing.requestId ?? requestId,
      tool: nextTool,
      updatedAt: nowIso,
      resultArrivedAfterTimeout: wasTimedOut || existing.resultArrivedAfterTimeout,
    });
    this.entries = upsertToolGroupDisplay(
      this.entries,
      sessionId,
      existing.requestId ?? requestId,
      nextTool,
    );
    this.scheduleToolTimeoutCheck();
  }

  private addSyntheticToolExecution(
    tool: ToolCallDisplay,
    sessionId: string,
    requestId?: string,
    at?: string,
  ): void {
    const timestamp = at ?? new Date().toISOString();
    this.toolExecutions.set(tool.callId, {
      toolCallId: tool.callId,
      sessionId,
      requestId,
      tool,
      startedAt: timestamp,
      updatedAt: timestamp,
      timeoutAt: computeTimeoutAt(timestamp),
    });
    if (!this.toolExecutionOrder.includes(tool.callId)) {
      this.toolExecutionOrder.push(tool.callId);
    }
    this.entries = upsertToolGroupDisplay(this.entries, sessionId, requestId, tool);
    this.scheduleToolTimeoutCheck();
  }

  private rebuildToolExecutionState(): void {
    const rebuilt = rebuildToolExecutionStateFromEntries(this.entries);
    this.toolExecutions = rebuilt.toolExecutions;
    this.toolExecutionOrder = rebuilt.toolExecutionOrder;
    this.orphanToolResults = new Map();
    this.scheduleToolTimeoutCheck();
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
      this.rebuildToolExecutionState();
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
