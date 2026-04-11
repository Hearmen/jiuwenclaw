export enum StreamingState {
  Idle = "idle",
  Responding = "responding",
  Paused = "paused",
  WaitingForConfirmation = "waiting_for_confirmation",
}

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export interface JsonObject {
  [key: string]: JsonValue;
}

export interface SystemMeta {
  eventType?: "chat.media" | "chat.file" | "notice";
  rawPayload?: JsonObject;
  fileName?: string;
  filePath?: string;
}

export interface InfoMeta {
  view?: "help" | "list" | "kv";
  title?: string;
  items?: Array<{ label: string; value?: string; description?: string }>;
}

export interface ToolCallDisplay {
  callId: string;
  name: string;
  arguments?: unknown;
  description?: string;
  formattedArgs?: string;
  status: "running" | "completed" | "error";
  result?: string;
  summary?: string;
  isError?: boolean;
}

export type SubtaskStatus = "starting" | "tool_call" | "tool_result" | "completed" | "error";

export interface SubtaskState {
  task_id: string;
  description: string;
  status: SubtaskStatus;
  index: number;
  total: number;
  tool_name?: string;
  tool_count: number;
  message?: string;
  is_parallel: boolean;
}

export interface ContextCompressionStats {
  rate: number;
  beforeCompressed: number | null;
  afterCompressed: number | null;
}

export type TodoStatus = "pending" | "in_progress" | "completed";

export interface TodoItem {
  id: string;
  content: string;
  activeForm: string;
  status: TodoStatus;
  createdAt: string;
  updatedAt: string;
}

export type HistoryItem =
  | { kind: "user"; id: string; sessionId: string; content: string; at: string }
  | {
      kind: "assistant";
      id: string;
      sessionId: string;
      content: string;
      streaming?: boolean;
      requestId?: string;
      at: string;
    }
  | { kind: "thinking"; id: string; sessionId: string; content: string; at: string }
  | {
      kind: "tool_group";
      id: string;
      sessionId: string;
      requestId?: string;
      tools: ToolCallDisplay[];
      at: string;
    }
  | {
      kind: "system";
      id: string;
      sessionId: string;
      content: string;
      meta?: SystemMeta;
      at: string;
    }
  | { kind: "error"; id: string; sessionId: string; content: string; at: string }
  | {
      kind: "info";
      id: string;
      sessionId: string;
      content: string;
      icon?: string;
      meta?: InfoMeta;
      at: string;
    };
