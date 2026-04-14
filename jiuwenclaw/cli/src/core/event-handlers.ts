import { parseHistoryFrame, createSessionResultToolDisplay } from "./history-parser.js";
import type { EventFrame } from "./protocol.js";
import {
  StreamingState,
  type ContextCompressionStats,
  type HistoryItem,
  type JsonObject,
  type SubtaskState,
  type TodoItem,
} from "./types.js";
import type { ConnectionStatus } from "./ws-client.js";
import {
  createId,
  findLastIndex,
  isIgnorableHistoryRestoreError,
  upsertToolGroup,
} from "./app-state-helpers.js";

export interface PendingQuestion {
  requestId: string;
  source?: string;
  questions: PendingQuestionItem[];
}

export interface PendingQuestionItem {
  header: string;
  question: string;
  options: PendingQuestionOption[];
  multiSelect?: boolean;
}

export interface PendingQuestionOption {
  label: string;
  description?: string;
}

export interface UserAnswer {
  selected_options: string[];
  custom_input?: string;
}

export interface AppEventDelegate {
  getConnectionStatus(): ConnectionStatus;
  getSessionId(): string;
  setSessionId(sessionId: string): void;
  setMode(mode: "plan" | "agent" | "team"): void;
  getEntries(): HistoryItem[];
  setEntries(entries: HistoryItem[]): void;
  setStreamingState(state: StreamingState): void;
  setPendingQuestion(question: PendingQuestion | null): void;
  setLastError(error: string | null): void;
  getActiveSubtasks(): Map<string, SubtaskState>;
  setTodos(todos: TodoItem[]): void;
  setEvolutionStatus(status: "idle" | "running"): void;
  setContextCompression(stats: ContextCompressionStats | null): void;
  pushHistoryEntry(entry: HistoryItem): void;
  scheduleHistoryFlush(): void;
  safeRestoreHistory(sessionId: string): void;
}

function appendEntry(delegate: AppEventDelegate, entry: HistoryItem): void {
  delegate.setEntries([...delegate.getEntries(), entry]);
}

function addSessionResultEntry(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
  effectiveEvent: string,
): void {
  const tool = createSessionResultToolDisplay(payload, effectiveEvent);
  appendEntry(delegate, {
    kind: "tool_group",
    id: createId("session-result"),
    sessionId: activeSessionId,
    requestId: typeof payload.request_id === "string" ? payload.request_id : undefined,
    tools: [tool],
    at: new Date().toISOString(),
  });
}

function handleConnectionAck(delegate: AppEventDelegate, frame: EventFrame): boolean {
  if (frame.event !== "connection.ack") {
    return false;
  }
  // session_id is determined at construction time; connection.ack is only
  // used as a signal to restore history once connected.
  const sessionId = delegate.getSessionId();
  if (sessionId && delegate.getConnectionStatus() === "connected") {
    delegate.safeRestoreHistory(sessionId);
  }
  return true;
}

function normalizePendingQuestion(payload: Record<string, unknown>): PendingQuestionItem[] {
  const rawQuestions = Array.isArray(payload.questions) ? payload.questions : [];
  const normalized = rawQuestions
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => ({
      header: typeof item.header === "string" ? item.header : "Question",
      question: typeof item.question === "string" ? item.question : "",
      options: Array.isArray(item.options)
        ? item.options
            .filter((option): option is Record<string, unknown> =>
              Boolean(option && typeof option === "object"),
            )
            .map((option) => ({
              label: typeof option.label === "string" ? option.label : "",
              description: typeof option.description === "string" ? option.description : undefined,
            }))
            .filter((option) => option.label.length > 0)
        : [],
      multiSelect: item.multi_select === true,
    }))
    .filter((item) => item.question.length > 0);

  if (normalized.length > 0) {
    return normalized;
  }

  const fallbackText =
    typeof payload.text === "string"
      ? payload.text
      : typeof payload.content === "string"
        ? payload.content
        : "";
  if (!fallbackText) {
    return [];
  }

  return [
    {
      header: "Question",
      question: fallbackText,
      options: [],
      multiSelect: false,
    },
  ];
}

function handleDelta(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const content = typeof payload.content === "string" ? payload.content : "";
  if (!content) return false;

  const entries = delegate.getEntries();
  if (payload.source_chunk_type === "llm_reasoning") {
    appendEntry(delegate, {
      kind: "thinking",
      id: createId("reasoning"),
      sessionId: activeSessionId,
      content,
      at: new Date().toISOString(),
    });
    return true;
  }

  const requestId = typeof payload.request_id === "string" ? payload.request_id : undefined;
  const existingIndex = findLastIndex(
    entries,
    (entry) => entry.kind === "assistant" && entry.streaming === true,
  );
  if (existingIndex === -1) {
    delegate.setEntries([
      ...entries,
      {
        kind: "assistant",
        id: createId("stream"),
        sessionId: activeSessionId,
        content,
        requestId,
        streaming: true,
        at: new Date().toISOString(),
      },
    ]);
  } else {
    delegate.setEntries(
      entries.map((entry, index) =>
        index === existingIndex && entry.kind === "assistant"
          ? { ...entry, content: entry.content + content, requestId: entry.requestId ?? requestId }
          : entry,
      ),
    );
  }
  delegate.setStreamingState(StreamingState.Responding);
  return true;
}

function handleFinal(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const content = typeof payload.content === "string" ? payload.content : "";
  const entries = delegate.getEntries();
  const streamingIndex = findLastIndex(
    entries,
    (entry) => entry.kind === "assistant" && entry.streaming === true,
  );
  delegate.setEntries(
    streamingIndex !== -1
      ? [
          ...entries.filter(
            (entry, index) => !(index === streamingIndex && entry.kind === "assistant"),
          ),
          {
            ...(entries[streamingIndex] as Extract<HistoryItem, { kind: "assistant" }>),
            content:
              content ||
              (entries[streamingIndex]?.kind === "assistant"
                ? entries[streamingIndex].content
                : ""),
            requestId:
              typeof payload.request_id === "string"
                ? payload.request_id
                : entries[streamingIndex]?.kind === "assistant"
                  ? entries[streamingIndex].requestId
                  : undefined,
            streaming: false,
          },
        ]
      : [
          ...entries,
          {
            kind: "assistant",
            id: createId("assistant-final"),
            sessionId: activeSessionId,
            content,
            requestId: typeof payload.request_id === "string" ? payload.request_id : undefined,
            streaming: false,
            at: new Date().toISOString(),
          },
        ],
  );
  delegate.setStreamingState(StreamingState.Idle);
  return true;
}

function handleReasoning(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const content = typeof payload.content === "string" ? payload.content : "";
  if (!content) return false;
  appendEntry(delegate, {
    kind: "thinking",
    id: createId("reasoning"),
    sessionId: activeSessionId,
    content,
    at: new Date().toISOString(),
  });
  return true;
}

function handleError(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
): boolean {
  const message =
    typeof payload.error === "string"
      ? payload.error
      : typeof payload.content === "string"
        ? payload.content
        : "Unknown error";
  if (isIgnorableHistoryRestoreError(message)) {
    return false;
  }
  appendEntry(delegate, {
    kind: "error",
    id: createId("error"),
    sessionId: activeSessionId,
    content: message,
    at: new Date().toISOString(),
  });
  delegate.setLastError(message);
  delegate.setStreamingState(StreamingState.Idle);
  return true;
}

function handleMediaEvent(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
  activeSessionId: string,
  effectiveEvent: "chat.media" | "chat.file",
): boolean {
  const name =
    typeof payload.file_name === "string"
      ? payload.file_name
      : typeof payload.name === "string"
        ? payload.name
        : "";
  const content = name ? `[${effectiveEvent}: ${name}]` : `[${effectiveEvent}]`;
  appendEntry(delegate, {
    kind: "system",
    id: createId("system"),
    sessionId: activeSessionId,
    content,
    at: new Date().toISOString(),
    meta: {
      eventType: effectiveEvent,
      rawPayload: payload as JsonObject,
      fileName: name || undefined,
      filePath: typeof payload.path === "string" ? payload.path : undefined,
    },
  });
  return true;
}

function handleContextCompressed(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const rate = typeof payload.rate === "number" ? payload.rate : 0;
  const before = typeof payload.before_compressed === "number" ? payload.before_compressed : null;
  const after = typeof payload.after_compressed === "number" ? payload.after_compressed : null;
  delegate.setContextCompression({
    rate,
    beforeCompressed: before,
    afterCompressed: after,
  });
  return true;
}

function handleSubtaskUpdate(
  delegate: AppEventDelegate,
  payload: Record<string, unknown>,
): boolean {
  const taskId = typeof payload.task_id === "string" ? payload.task_id : "";
  if (!taskId) return false;
  const subtasks = delegate.getActiveSubtasks();
  if (payload.status === "completed" || payload.status === "error") {
    subtasks.delete(taskId);
    return true;
  }
  subtasks.set(taskId, {
    task_id: taskId,
    description: typeof payload.description === "string" ? payload.description : "",
    status: (typeof payload.status === "string"
      ? payload.status
      : "starting") as SubtaskState["status"],
    index: typeof payload.index === "number" ? payload.index : 0,
    total: typeof payload.total === "number" ? payload.total : 0,
    tool_name: typeof payload.tool_name === "string" ? payload.tool_name : undefined,
    tool_count: typeof payload.tool_count === "number" ? payload.tool_count : 0,
    message: typeof payload.message === "string" ? payload.message : undefined,
    is_parallel: payload.is_parallel === true,
  });
  return true;
}

function handleTodoUpdated(delegate: AppEventDelegate, payload: Record<string, unknown>): boolean {
  const todos = Array.isArray(payload.todos) ? payload.todos : [];
  delegate.setTodos(
    todos
      .filter((item): item is TodoItem => Boolean(item && typeof item === "object"))
      .map((item) => ({
        id: typeof item.id === "string" ? item.id : "",
        content: typeof item.content === "string" ? item.content : "",
        activeForm: typeof item.activeForm === "string" ? item.activeForm : "",
        status: (item.status === "in_progress" || item.status === "completed"
          ? item.status
          : "pending") as TodoItem["status"],
        createdAt: typeof item.createdAt === "string" ? item.createdAt : new Date().toISOString(),
        updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : new Date().toISOString(),
      }))
      .filter((item) => item.id.length > 0),
  );
  return true;
}

export function handleIncomingFrame(delegate: AppEventDelegate, frame: EventFrame): boolean {
  const connectionChanged = handleConnectionAck(delegate, frame);

  const payload = frame.payload;
  const effectiveEvent = typeof payload.event_type === "string" ? payload.event_type : frame.event;
  const activeSessionId = delegate.getSessionId();
  const eventSessionId = typeof payload.session_id === "string" ? payload.session_id : "";
  if (eventSessionId && eventSessionId !== activeSessionId) {
    return connectionChanged;
  }

  switch (effectiveEvent) {
    case "chat.delta":
      return handleDelta(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.final":
      return handleFinal(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.reasoning":
      return handleReasoning(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.error":
      return handleError(delegate, payload, activeSessionId) || connectionChanged;

    case "chat.tool_call":
      delegate.setEntries(
        upsertToolGroup(
          delegate.getEntries(),
          activeSessionId,
          typeof payload.request_id === "string" ? payload.request_id : undefined,
          payload,
          false,
        ),
      );
      return true;

    case "chat.tool_result":
      delegate.setEntries(
        upsertToolGroup(
          delegate.getEntries(),
          activeSessionId,
          typeof payload.request_id === "string" ? payload.request_id : undefined,
          payload,
          true,
        ),
      );
      return true;

    case "chat.processing_status":
      delegate.setStreamingState(
        payload.is_processing === true ? StreamingState.Responding : StreamingState.Idle,
      );
      if (payload.is_processing !== true) {
        delegate.getActiveSubtasks().clear();
        delegate.setEvolutionStatus("idle");
      }
      return true;

    case "chat.interrupt_result": {
      const intent = typeof payload.intent === "string" ? payload.intent : "cancel";
      delegate.setStreamingState(intent === "cancel" ? StreamingState.Idle : StreamingState.Paused);
      return true;
    }

    case "chat.ask_user_question": {
      const requestId = typeof payload.request_id === "string" ? payload.request_id : "";
      const questions = normalizePendingQuestion(payload);
      if (!requestId || questions.length === 0) {
        return connectionChanged;
      }
      delegate.setPendingQuestion({
        requestId,
        source: typeof payload.source === "string" ? payload.source : undefined,
        questions,
      });
      delegate.setStreamingState(StreamingState.WaitingForConfirmation);
      return true;
    }

    case "history.message": {
      const entry = parseHistoryFrame(frame);
      if (!entry) {
        return connectionChanged;
      }
      delegate.pushHistoryEntry(entry);
      delegate.scheduleHistoryFlush();
      return connectionChanged;
    }

    case "chat.media":
    case "chat.file":
      return handleMediaEvent(delegate, payload, activeSessionId, effectiveEvent);

    case "context.compressed":
      return handleContextCompressed(delegate, payload);

    case "chat.subtask_update":
      return handleSubtaskUpdate(delegate, payload);

    case "chat.session_result":
    case "session_result":
      addSessionResultEntry(delegate, payload, activeSessionId, effectiveEvent);
      return true;

    case "chat.evolution_status":
      delegate.setEvolutionStatus(payload.status === "start" ? "running" : "idle");
      return true;

    case "todo.updated":
      return handleTodoUpdated(delegate, payload);

    case "session.updated": {
      const mode = typeof payload.mode === "string" ? payload.mode : "";
      if (mode === "plan" || mode === "agent" || mode === "team") {
        delegate.setMode(mode as "plan" | "agent" | "team");
      }
      return true;
    }

    default:
      return connectionChanged;
  }
}
