// jiuwenclaw/cli/src/core/commands/builtins/auto-harness.ts

import { addError, addInfo, parseArgs } from "../helpers.js";
import { CommandKind, type SlashCommand, type CommandContext } from "../types.js";

// Schedule subcommands

const scheduleStartCommand: SlashCommand = {
  name: "start",
  description: "创建定时 auto_harness 任务",
  usage: "/auto-harness schedule start --interval <hours> <query>",
  example: "/auto-harness schedule start --interval 4 优化数据库查询性能",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: (_ctx, partial) => {
    // Completions: --interval and interval values
    const parts = partial.trim().split(/\s+/).filter(Boolean);
    const lastPart = parts[parts.length - 1] || "";

    // If last part is exactly --interval or -i, suggest values (user just typed the flag)
    if (lastPart === "--interval" || lastPart === "-i") {
      return ["1", "2", "4", "8", "12", "24"];
    }

    // Check if --interval flag exists and we're at value position
    const intervalIndex = parts.indexOf("--interval");
    const shortIndex = parts.indexOf("-i");
    const hasIntervalFlag = intervalIndex !== -1 || shortIndex !== -1;

    if (hasIntervalFlag) {
      const flagIndex = Math.max(intervalIndex, shortIndex);
      // If we're right after the flag (at value position)
      if (parts.length === flagIndex + 1) {
        const valuePart = lastPart;
        // User started typing a number
        if (valuePart && !valuePart.startsWith("-") && /^\d/.test(valuePart)) {
          return ["1", "2", "4", "8", "12", "24"].filter((v) => v.startsWith(valuePart));
        }
        // No number typed yet, suggest values
        if (!valuePart || valuePart === "") {
          return ["1", "2", "4", "8", "12", "24"];
        }
      }
    }

    // Otherwise suggest flags (but not ones already used)
    const usedFlags: string[] = [];
    if (parts.includes("--interval")) usedFlags.push("--interval");
    if (parts.includes("-i")) usedFlags.push("-i", "--interval");

    const flags = ["--interval"].filter((f) => !usedFlags.includes(f));

    if (lastPart.startsWith("-")) {
      return flags.filter((f) => f.startsWith(lastPart));
    }

    // If all flags used, no more completions
    if (flags.length === 0) {
      return [];
    }

    return flags;
  },
  action: async (ctx, args) => {
    const parsed = parseScheduleStartArgs(args);

    if (!parsed.interval || parsed.interval < 1) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule start --interval <hours> [--immediate] <query>")
      );
      return;
    }

    if (!parsed.query) {
      ctx.addItem(
        addError(ctx.sessionId, "请提供执行目标 query")
      );
      return;
    }

    // Check config
    const configCheck = await ctx.request<{ valid: boolean; missing_fields?: Array<{ id: string; prompt: string }> }>("schedule.check_config", {});

    const missingFields = configCheck.missing_fields as Array<{ id: string; prompt: string }> | undefined;
    if (missingFields && missingFields.length > 0) {
      const missingList = missingFields.map(f => `  - ${f.prompt}`).join("\n");
      ctx.addItem(
        addInfo(ctx.sessionId, `检测到配置缺失:\n${missingList}\n\n请使用 /config edit 配置这些字段后重试`)
      );
      return;
    }

    // Ask user whether to run immediately
    let run_immediately = false;
    {
      try {
        const [answer] = await ctx.askQuestions([
          {
            header: "立即执行",
            question: "是否立即执行一次任务？（如选否，则等待首个周期后再执行）",
            options: [{ label: "立即执行" }, { label: "等待周期" }],
          },
        ]);
        run_immediately = answer.selected_options[0] === "立即执行";
      } catch {
        // User cancelled or timeout, proceed without immediate execution
        run_immediately = false;
      }
    }

    // Create the scheduled task
    const result = await ctx.request<{ error?: string; task_id?: string; next_run_time?: string }>("schedule.create", {
      interval_hours: parsed.interval,
      query: parsed.query,
      run_immediately: run_immediately,
    });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `创建失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(
        ctx.sessionId,
        `\n定时任务已创建\nID: ${result.task_id}\n下次执行: ${formatLocalTime(result.next_run_time)}\n间隔: 每 ${parsed.interval} 小时${run_immediately ? "\n(已立即执行一次)" : ""}\n`
      )
    );
  },
};

const scheduleListCommand: SlashCommand = {
  name: "list",
  description: "列出所有任务",
  kind: CommandKind.BUILT_IN,
  takesArgs: false,
  action: async (ctx, _args) => {
    ctx.addItem(addInfo(ctx.sessionId, "\n正在查询任务...\n", "i"));

    const result = await ctx.request<{ tasks?: Array<{ task_id: string; query: string; status: string; interval_hours: number; next_run_time: string; created_at: string; is_one_time?: boolean }> }>("schedule.list", {});

    const tasks = result.tasks as Array<{ task_id: string; query: string; status: string; interval_hours: number; next_run_time: string; created_at: string; is_one_time?: boolean }> | undefined;
    if (!tasks || tasks.length === 0) {
      ctx.addItem(addInfo(ctx.sessionId, "\n暂无任务\n", "i"));
      return;
    }

    const lines = ["\n【任务列表】"];
    for (const task of tasks) {
      const statusEmoji = task.status === "running" ? "[运行中]" :
                         task.status === "pending" ? "[等待]" :
                         task.status === "cancelled" ? "[已取消]" : "[已完成]";
      const isOneTime = task.is_one_time ? "[一次性]" : "";
      const queryPreview = task.query.length > 50 ? task.query.substring(0, 50) + "..." : task.query;
      lines.push(
        `${statusEmoji}${isOneTime} ${task.task_id} - ${queryPreview}`
      );
      // Show interval only for recurring tasks
      if (task.is_one_time) {
        lines.push(`   状态: ${task.status} | 类型: 一次性`);
      } else {
        lines.push(`   状态: ${task.status} | 间隔: ${task.interval_hours}h | 下次执行: ${formatLocalTime(task.next_run_time)}`);
      }
      lines.push(`   创建时间: ${formatLocalTime(task.created_at)}`);
      lines.push("");
    }

    lines.push("");
    ctx.addItem(addInfo(ctx.sessionId, lines.join("\n")));
  },
};

const scheduleStatusCommand: SlashCommand = {
  name: "status",
  description: "查看任务详情",
  usage: "/auto-harness schedule status <task_id>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Fetch task list for task_id completion
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return tasks.map((t) => t.task_id);
      return tasks.filter((t) => t.task_id.toLowerCase().startsWith(prefix)).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const task_id = args.trim();

    if (!task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule status <task_id>")
      );
      return;
    }

    const result = await ctx.request<{ error?: string; task_id?: string; query?: string; status?: string; interval_hours?: number; created_at?: string; next_run_time?: string; current_execution_id?: string; execution_history?: Array<{ execution_id: string; status: string; completed_at?: string }>; is_one_time?: boolean }>("schedule.status", { task_id });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, result.error)
      );
      return;
    }

    const isOneTime = result.is_one_time;
    const lines = [`\n【任务详情: ${result.task_id}】`];
    lines.push(`目标: ${result.query}`);
    lines.push(`状态: ${result.status}`);
    if (isOneTime) {
      lines.push(`类型: 一次性任务`);
    } else {
      lines.push(`类型: 定时任务 | 间隔: 每 ${result.interval_hours} 小时`);
      lines.push(`下次执行: ${formatLocalTime(result.next_run_time) || "已取消"}`);
    }
    lines.push(`创建时间: ${formatLocalTime(result.created_at)}`);

    if (result.current_execution_id) {
      lines.push(`当前执行: ${result.current_execution_id}`);
    }

    const history = result.execution_history as Array<{ execution_id: string; status: string; completed_at?: string }> | undefined;
    if (history && history.length > 0) {
      lines.push(`\n【执行历史】(${history.length} 次)`);
      const recentHistory = history.slice(-5);
      for (const record of recentHistory) {
        const statusText = record.status === "success" ? "[成功]" :
                           record.status === "cancelled" ? "[取消]" : "[异常]";
        lines.push(`${statusText} ${record.execution_id} - ${formatLocalTime(record.completed_at) || "进行中"}`);
      }
    }

    lines.push("");
    ctx.addItem(
      addInfo(ctx.sessionId, lines.join("\n"))
    );
  },
};

const scheduleLogsCommand: SlashCommand = {
  name: "logs",
  description: "查看任务执行日志（默认显示当前运行日志，--history 查看历史日志）",
  usage: "/auto-harness schedule logs <task_id> [--history <n>]",
  example: "/auto-harness schedule logs sch_abc123 --history 0",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Check for trailing space before trimming (to detect completed arguments)
    const hasTrailingSpace = partial.endsWith(" ");
    const parts = partial.trim().split(/\s+/).filter(Boolean);
    const lastPart = parts[parts.length - 1] || "";

    // Extract history index value if present (to exclude from task_id detection)
    const historyMatch = partial.match(/--history\s+(\d+)/);
    const historyIndexValue = historyMatch ? historyMatch[1] : null;

    // Find existing task_id: first non-flag, non-history-index argument
    const existingTaskId = parts.find((p) => {
      return !p.startsWith("-") && p !== historyIndexValue && !/^\d+$/.test(p);
    }) || "";

    // Step 1: Check if we need a history index value
    // --history is present and we're at the value position
    if (parts.includes("--history")) {
      const historyIdx = parts.indexOf("--history");
      // If we're exactly at the position after --history (typing the value)
      if (parts.length === historyIdx + 1) {
        const values = ["0", "1", "2", "3", "4"];
        if (lastPart && /^\d/.test(lastPart)) {
          return values.filter((v) => v.startsWith(lastPart));
        }
        return values;
      }
      // If history index is complete with trailing space, suggest task_ids
      // Check: history value exists AND there's a trailing space
      if (parts.length >= historyIdx + 2 && /^\d+$/.test(parts[historyIdx + 1]) && hasTrailingSpace) {
        try {
          const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
          const tasks = result.tasks || [];
          return tasks.map((t) => t.task_id);
        } catch {
          return [];
        }
      }
      // If history index is complete but no trailing space, don't suggest task_ids yet
      if (parts.length === historyIdx + 2 && /^\d+$/.test(parts[historyIdx + 1])) {
        return [];
      }
    }

    // Step 2: Check if lastPart is exactly --history - suggest values immediately
    if (lastPart === "--history") {
      return ["0", "1", "2", "3", "4"];
    }

    // Step 3: If typing a flag, suggest --history only (--current is default, no need to suggest)
    if (lastPart.startsWith("-") && lastPart !== "--history") {
      if (parts.includes("--history")) return [];
      return ["--history"].filter((f) => f.startsWith(lastPart));
    }

    // Step 4: If we have a task_id already, suggest --history flag
    if (existingTaskId && !parts.includes("--history")) {
      return ["--history"];
    }

    // Step 5: Otherwise, suggest task_ids
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      if (!lastPart) return tasks.map((t) => t.task_id);
      return tasks.filter((t) => t.task_id.toLowerCase().startsWith(lastPart.toLowerCase())).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const parsed = parseLogArgs(args);

    if (!parsed.task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule logs <task_id> [--history <n>]")
      );
      return;
    }

    if (parsed.log_type === "current") {
      // Tail -f style streaming for current running task
      await streamCurrentLogs(ctx, parsed.task_id);
    } else {
      // History mode: read full log in batches
      await readFullHistoryLogs(ctx, parsed.task_id, parsed.history_index);
    }
  },
};

interface LogEntry {
  event_type?: string;
  content?: string;
  stage?: string;
  status?: string;
  timestamp?: string;
  message?: string;
  is_processing?: boolean;
  source_chunk_type?: string;
  tool_name?: string;
  is_error?: boolean;
  // Nested tool payload (as in history-parser.ts resolveToolPayload)
  tool_call?: {
    name?: string;
    id?: string;
    tool_call_id?: string;
    arguments?: string | Record<string, unknown>;
    description?: string;
  };
  tool_result?: {
    name?: string;
    tool_name?: string;
    result?: string;
    status?: string;
    success?: boolean;
    summary?: string;
  };
  // Direct fields fallback
  name?: string;
  id?: string;
  tool_call_id?: string;
}

// Stream logs for currently running task (tail -f style)
async function streamCurrentLogs(
  ctx: CommandContext,
  task_id: string
): Promise<void> {
  let offset = 0;
  let isRunning = true;
  let executionId = "";
  let pollInterval = 2000; // 2 seconds
  let maxPolls = 300; // Max 300 polls (~10 minutes) to prevent infinite loop
  let pollCount = 0;
  let consecutiveEmptyPolls = 0;
  const maxEmptyPolls = 3; // Stop after 3 consecutive empty polls when not running

  // Clear any previous interrupt flag before starting new stream
  ctx.clearInterruptRequested();

  ctx.addItem(addInfo(ctx.sessionId, `\n【实时日志跟踪: ${task_id}】\n正在连接...\n`));

  // Helper: check interrupt and exit if requested
  const checkInterrupt = (): boolean => {
    if (ctx.isInterruptRequested()) {
      ctx.clearInterruptRequested();
      ctx.addItem(addInfo(ctx.sessionId, `\n【日志跟踪已中断】`));
      return true;
    }
    return false;
  };

  // Helper: interruptible request - checks interrupt flag while waiting
  const interruptibleRequest = async <T>(
    method: string,
    params: Record<string, unknown>,
    timeoutMs: number,
    checkIntervalMs: number = 200
  ): Promise<T | null> => {
    const requestPromise = ctx.request<T>(method, params, timeoutMs);
    // Poll interrupt flag periodically while waiting for request
    const interruptChecker = new Promise<null>((resolve) => {
      const interval = setInterval(() => {
        if (ctx.isInterruptRequested()) {
          clearInterval(interval);
          resolve(null);
        }
      }, checkIntervalMs);
      // Clean up interval when request completes
      requestPromise.then(() => clearInterval(interval)).catch(() => clearInterval(interval));
    });
    // Race between request and interrupt
    return Promise.race([requestPromise, interruptChecker]);
  };

  while (isRunning && pollCount < maxPolls) {
    // Check interrupt at start of each loop
    if (checkInterrupt()) return;

    pollCount++;
    try {
      // Use interruptible request for immediate Ctrl+C response
      const result = await interruptibleRequest<{
        error?: string;
        logs?: Array<LogEntry>;
        execution_id?: string;
        total_lines?: number;
        is_running?: boolean;
        has_more?: boolean;
      }>("schedule.logs", {
        task_id,
        log_type: "current",
        offset,
        limit: 1000,
      }, 30000);  // 30s timeout

      // Request was interrupted - clear flag and show message via helper
      if (result === null) {
        checkInterrupt(); // clears flag and shows message (returns true when flag was set)
        return;
      }

      // Check interrupt after request completes
      if (checkInterrupt()) return;

      // Check for error - likely means execution finished
      if (result.error) {
        // Execution finished - show completion message
        if (result.error.includes("当前无正在执行的日志") || result.error.includes("不存在")) {
          ctx.addItem(addInfo(ctx.sessionId, `\n【任务执行完成】`));
          return;
        }
        ctx.addItem(addError(ctx.sessionId, result.error));
        return;
      }

      executionId = result.execution_id || "";
      isRunning = result.is_running ?? false;

      // Display new logs - aggregate streaming chunks for better display
      const logs = result.logs || [];
      if (logs.length > 0) {
        consecutiveEmptyPolls = 0;

        // Check first event type to decide how to handle reasoning
        const firstEventType = logs[0]?.event_type || "";

        if (firstEventType === "chat.reasoning") {
          // Logs start with reasoning - find first non-reasoning index
          let firstNonReasoningIdx = -1;
          for (let i = 0; i < logs.length; i++) {
            if (logs[i]?.event_type !== "chat.reasoning") {
              firstNonReasoningIdx = i;
              break;
            }
          }

          if (firstNonReasoningIdx === -1) {
            // All logs are reasoning - discard entire batch
            offset = offset + logs.length;
          } else {
            // Skip leading reasoning, display rest
            const logsToDisplay = logs.slice(firstNonReasoningIdx);
            const sections = parseAndAggregateLogs(logsToDisplay);
            for (const section of sections) {
              // Check interrupt during log display
              if (checkInterrupt()) return;
              const formattedLine = formatLogSection(section);
              if (formattedLine) {
                ctx.addItem(addInfo(ctx.sessionId, formattedLine, "i"));
              }
            }
            offset = offset + logs.length;
          }
        } else {
          // Not starting with reasoning - display all
          const sections = parseAndAggregateLogs(logs);
          for (const section of sections) {
            // Check interrupt during log display
            if (checkInterrupt()) return;
            const formattedLine = formatLogSection(section);
            if (formattedLine) {
              ctx.addItem(addInfo(ctx.sessionId, formattedLine, "i"));
            }
          }
          offset = offset + logs.length;
        }
      } else {
        consecutiveEmptyPolls++;
      }

      // Stop if not running and no new logs for a few polls
      if (!isRunning && consecutiveEmptyPolls >= maxEmptyPolls) {
        break;
      }

      // Check for local interrupt request (Ctrl+C)
      if (checkInterrupt()) return;

      // Continue polling if still running - use shorter intervals for faster interrupt response
      if (isRunning) {
        // Use shorter 500ms intervals and check interrupt status each time
        for (let i = 0; i < pollInterval / 500; i++) {
          await new Promise((resolve) => setTimeout(resolve, 500));
          // Check for local interrupt request (Ctrl+C)
          if (checkInterrupt()) return;
        }
      } else {
        // Wait a bit more to get final logs
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch (e) {
      ctx.addItem(addError(ctx.sessionId, `日志流错误: ${e}`));
      return;
    }
  }

  if (pollCount >= maxPolls) {
    ctx.addItem(addInfo(ctx.sessionId, `\n【日志跟踪超时终止】`));
  } else {
    ctx.addItem(addInfo(ctx.sessionId, `\n【日志跟踪完成: ${executionId}】`));
  }
}

// Read full history logs in batches
async function readFullHistoryLogs(
  ctx: CommandContext,
  task_id: string,
  history_index: number
): Promise<void> {
  ctx.addItem(addInfo(ctx.sessionId, `\n正在读取完整日志...\n`, "i"));

  let allLogs: Array<LogEntry> = [];
  let offset = 0;
  const batchSize = 2000;
  let executionId = "";
  let completedAt = "";
  let status = "";
  let hasMore = true;

  while (hasMore) {
    const result = await ctx.request<{
      error?: string;
      logs?: Array<LogEntry>;
      execution_id?: string;
      completed_at?: string;
      status?: string;
      total_lines?: number;
      has_more?: boolean;
    }>("schedule.logs", {
      task_id,
      log_type: "history",
      history_index,
      offset,
      limit: batchSize,
    }, 60000);

    if (result.error) {
      ctx.addItem(addError(ctx.sessionId, result.error));
      return;
    }

    // Capture metadata from first batch
    if (offset === 0) {
      executionId = result.execution_id || "";
      completedAt = result.completed_at || "";
      status = result.status || "";
    }

    const logs = result.logs || [];
    if (logs.length === 0) {
      hasMore = false;
      break;
    }

    allLogs.push(...logs);
    offset = offset + logs.length;

    // Check if there's more to read
    hasMore = result.has_more ?? (logs.length >= batchSize);
  }

  // Filter out ALL reasoning entries for history logs (no thinking display)
  // Remove chat.reasoning events AND chat.delta with llm_reasoning source
  allLogs = allLogs.filter((log) => {
    if (log.event_type === "chat.reasoning") return false;
    if (log.event_type === "chat.delta" && log.source_chunk_type === "llm_reasoning") return false;
    return true;
  });

  // Display full aggregated logs
  if (allLogs.length === 0) {
    ctx.addItem(addInfo(ctx.sessionId, "日志为空"));
    return;
  }

  const sections = parseAndAggregateLogs(allLogs);
  const lines: string[] = [`\n【执行日志: ${executionId}】`];
  if (completedAt) {
    lines.push(`完成时间: ${formatLocalTime(completedAt)} | 状态: ${status}`);
  }
  lines.push("");
  lines.push("=" .repeat(60));

  for (const section of sections) {
    const formattedLine = formatLogSectionDetailed(section);
    if (formattedLine) {
      lines.push(formattedLine);
    }
  }

  lines.push("=" .repeat(60));
  lines.push("");

  ctx.addItem(addInfo(ctx.sessionId, lines.join("\n")));
}

/**
 * Parse and aggregate logs similar to handleIncomingFrame.
 * Merges streaming chunks (chat.delta, chat.reasoning) into complete messages.
 */
interface ParsedLogSection {
  type: "thinking" | "assistant" | "stage" | "status" | "error" | "info" | "tool";
  content: string;
  stage?: string;
  status?: string;
  timestamp?: string;
  tool_name?: string;
  tool_success?: boolean;
}

// ANSI color codes for log display differentiation
const ANSI = {
  cyan: "\x1b[36m",     // 工具调用
  green: "\x1b[32m",    // 成功
  red: "\x1b[31m",      // 错误/失败
  yellow: "\x1b[33m",   // 阶段
  blue: "\x1b[34m",     // 状态
  brightWhite: "\x1b[97m",  // 辅助输出 (柔和)
  gray: "\x1b[90m",     // 思考 (dim)
  bold: "\x1b[1m",
  reset: "\x1b[0m",
};

// Format log section for streaming display (compact but differentiated with colors)
function formatLogSection(section: ParsedLogSection): string | null {
  switch (section.type) {
    case "thinking":
      // Thinking content - display truncated preview with brain icon
      const thinkingPreview = section.content.length > 150
        ? section.content.substring(0, 150) + "..."
        : section.content;
      return `${ANSI.gray}🧠 思考: ${thinkingPreview}${ANSI.reset}`;

    case "assistant":
      // Assistant output - bright white color, no truncation
      return `${ANSI.brightWhite}💬 ${section.content}${ANSI.reset}`;

    case "stage":
      if (section.status) {
        // Stage completion - prominent double-line separator
        const icon = section.status === "success" ? "✅" :
                    section.status === "failed" ? "❌" : "⏸️";
        const color = section.status === "success" ? ANSI.green :
                     section.status === "failed" ? ANSI.red : ANSI.yellow;
        const statusText = section.status === "success" ? "完成" :
                          section.status === "failed" ? "失败" : section.status;
        return `\n${ANSI.bold}${color}╔═════════════════ ${icon} 阶段${statusText}: ${section.stage || "?"} ═════════════════╗${ANSI.reset}\n`;
      }
      // Stage start - prominent single-line header
      const stageContent = section.content.length > 100
        ? section.content.substring(0, 100) + "..."
        : section.content;
      return `\n${ANSI.bold}${ANSI.yellow}┌───────────────── 📊 Stage Running: ${section.stage} ─────────────────┐${ANSI.reset}\n${ANSI.yellow}│ ${stageContent}${ANSI.reset}`;

    case "status":
      // Status change - blue separator
      return `${ANSI.blue}────────────────── ⏳ ${section.content} ──────────────────${ANSI.reset}`;

    case "error":
      // Error - red color
      return `${ANSI.red}${ANSI.bold}🔥 错误: ${section.content}${ANSI.reset}`;

    case "tool":
      // Only show tool results, skip call starts to reduce noise
      if (section.tool_success === undefined) {
        // Tool call start - skip, only show result
        return null;
      } else if (section.tool_success) {
        // Tool success result - green incoming arrow
        return `${ANSI.green}  ← ✅ ${section.tool_name || "unknown"}${ANSI.reset}`;
      } else {
        // Tool failure result - red incoming arrow
        return `${ANSI.red}  ← ❌ ${section.tool_name || "unknown"}${ANSI.reset}`;
      }

    case "info":
      return `${ANSI.gray}  · ${section.content}${ANSI.reset}`;

    default:
      return null;
  }
}

function parseAndAggregateLogs(logs: Array<LogEntry>): ParsedLogSection[] {
  const sections: ParsedLogSection[] = [];
  let currentThinking: string | null = null;
  let currentAssistant: string | null = null;
  let currentStage: string | null = null;

  for (const log of logs) {
    const eventType = log.event_type || "";
    const content = log.content || log.message || "";

    switch (eventType) {
      case "chat.processing_status":
        // Processing state change - finalize any pending content
        if (currentThinking !== null) {
          sections.push({ type: "thinking", content: currentThinking });
          currentThinking = null;
        }
        if (currentAssistant !== null) {
          sections.push({ type: "assistant", content: currentAssistant });
          currentAssistant = null;
        }
        const statusText = log.is_processing ? "▶ 开始处理" : "■ 处理完成";
        sections.push({ type: "status", content: statusText });
        break;

      case "chat.reasoning":
        // Accumulate reasoning chunks (thinking)
        if (currentThinking === null) {
          currentThinking = content;
        } else {
          currentThinking += content;
        }
        break;

      case "chat.delta":
        // Accumulate assistant message chunks
        if (log.source_chunk_type === "llm_reasoning") {
          // This is also thinking content
          if (currentThinking === null) {
            currentThinking = content;
          } else {
            currentThinking += content;
          }
        } else {
          // Regular assistant content
          if (currentAssistant === null) {
            currentAssistant = content;
          } else {
            currentAssistant += content;
          }
        }
        break;

      case "chat.final":
        // Final message - finalize assistant content
        if (content) {
          if (currentAssistant !== null) {
            currentAssistant += content;
          } else {
            currentAssistant = content;
          }
        }
        if (currentThinking !== null) {
          sections.push({ type: "thinking", content: currentThinking });
          currentThinking = null;
        }
        if (currentAssistant !== null) {
          sections.push({ type: "assistant", content: currentAssistant });
          currentAssistant = null;
        }
        break;

      case "harness.message":
        // Harness stage messages - finalize pending content before stage start
        const stage = log.stage || "";
        if (currentStage !== stage) {
          // Stage change - finalize any pending content from previous stage
          if (currentThinking !== null) {
            sections.push({ type: "thinking", content: currentThinking });
            currentThinking = null;
          }
          if (currentAssistant !== null) {
            sections.push({ type: "assistant", content: currentAssistant });
            currentAssistant = null;
          }
          currentStage = stage;
        }
        sections.push({ type: "stage", content: content, stage: stage });
        break;

      case "harness.stage_result":
        // Stage completion result - finalize thinking/assistant, then output stage
        if (currentThinking !== null) {
          sections.push({ type: "thinking", content: currentThinking });
          currentThinking = null;
        }
        if (currentAssistant !== null) {
          sections.push({ type: "assistant", content: currentAssistant });
          currentAssistant = null;
        }
        sections.push({
          type: "stage",
          content: `阶段完成: ${log.stage || "unknown"}`,
          stage: log.stage,
          status: log.status,
        });
        break;

      case "chat.tool_call":
        // Extract tool name from nested payload or direct fields
        const toolCallPayload = log.tool_call || log;
        const toolCallName = toolCallPayload.name || log.name || log.tool_name || "unknown";
        sections.push({
          type: "tool",
          content: "",
          tool_name: toolCallName,
          tool_success: undefined, // call, not result
        });
        break;

      case "chat.tool_result":
        // Extract tool name from nested payload or direct fields
        const toolResultNested = log.tool_result;
        const toolResultName = toolResultNested?.name || toolResultNested?.tool_name || log.name || log.tool_name || "unknown";
        const toolResultSuccess = !(log.is_error || toolResultNested?.success === false);
        sections.push({
          type: "tool",
          content: "",
          tool_name: toolResultName,
          tool_success: toolResultSuccess,
        });
        break;

      case "chat.error":
        if (currentThinking !== null) {
          sections.push({ type: "thinking", content: currentThinking });
          currentThinking = null;
        }
        if (currentAssistant !== null) {
          sections.push({ type: "assistant", content: currentAssistant });
          currentAssistant = null;
        }
        sections.push({ type: "error", content: content || "未知错误" });
        break;

      default:
        // Other events
        if (content) {
          sections.push({ type: "info", content: `[${eventType}] ${content.substring(0, 100)}` });
        }
        break;
    }
  }

  // Finalize any remaining pending content
  if (currentThinking !== null) {
    sections.push({ type: "thinking", content: currentThinking });
  }
  if (currentAssistant !== null) {
    sections.push({ type: "assistant", content: currentAssistant });
  }

  return sections;
}

// Format log section for history display (detailed with colors)
function formatLogSectionDetailed(section: ParsedLogSection): string | null {
  switch (section.type) {
    case "thinking":
      return null;
    case "assistant":
      // Assistant output - bright white color
      return `${ANSI.brightWhite}💬 ${section.content}${ANSI.reset}`;
    case "stage":
      if (section.status) {
        const statusText = section.status === "success" ? "✓ 成功" :
                          section.status === "failed" ? "✗ 失败" : section.status;
        const icon = section.status === "success" ? "✅" :
                    section.status === "failed" ? "❌" : "⏸️";
        const color = section.status === "success" ? ANSI.green :
                     section.status === "failed" ? ANSI.red : ANSI.yellow;
        return `\n${ANSI.bold}${color}╔══ ${icon} Stage Complete: ${section.stage} (${statusText}) ══╗${ANSI.reset}\n`;
      }
      // Stage start - with full content in detailed view
      return `\n${ANSI.bold}${ANSI.yellow}┌───────────────── 📊 Stage Running: ${section.stage} ─────────────────┐${ANSI.reset}\n${ANSI.yellow}│ ${indentMultiline(section.content, "│ ", 300)}${ANSI.reset}\n${ANSI.yellow}└──${ANSI.reset}`;
    case "status":
      return `${ANSI.blue}⏳ ${section.content}${ANSI.reset}`;
    case "error":
      return `${ANSI.red}❌ 错误: ${section.content}${ANSI.reset}`;
    case "tool":
      // Only show tool results, skip call starts to reduce noise
      if (section.tool_success === undefined) {
        return null;
      } else if (section.tool_success) {
        return `${ANSI.green}  ← ✅ ${section.tool_name || "unknown"}${ANSI.reset}`;
      } else {
        return `${ANSI.red}  ← ❌ ${section.tool_name || "unknown"}${ANSI.reset}`;
      }
    case "info":
      return `${ANSI.gray}  · ${section.content}${ANSI.reset}`;
    default:
      return null;
  }
}

// Helper: indent multiline content
function indentMultiline(text: string, prefix: string, maxLength: number): string {
  const lines = text.split("\n");
  const result: string[] = [];
  for (const line of lines) {
    const truncated = line.length > maxLength ? line.substring(0, maxLength) + "..." : line;
    result.push(prefix + truncated);
  }
  return result.join("\n");
}

const scheduleCancelCommand: SlashCommand = {
  name: "cancel",
  description: "取消任务",
  usage: "/auto-harness schedule cancel <task_id>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Fetch task list for task_id completion
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string; status?: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      // Only show non-cancelled tasks
      const activeTasks = tasks.filter((t) => t.status !== "cancelled");
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return activeTasks.map((t) => t.task_id);
      return activeTasks.filter((t) => t.task_id.toLowerCase().startsWith(prefix)).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const task_id = args.trim();

    if (!task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule cancel <task_id>")
      );
      return;
    }

    const result = await ctx.request<{ error?: string; task_id?: string }>("schedule.cancel", { task_id });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `取消失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(ctx.sessionId, `\n任务已取消: ${result.task_id}\n`)
    );
  },
};

const scheduleDeleteCommand: SlashCommand = {
  name: "delete",
  description: "删除任务（取消并移除）",
  usage: "/auto-harness schedule delete <task_id>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  completion: async (ctx, partial) => {
    // Fetch task list for task_id completion
    try {
      const result = await ctx.request<{ tasks?: Array<{ task_id: string }> }>("schedule.list", {}, 5000);
      const tasks = result.tasks || [];
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return tasks.map((t) => t.task_id);
      return tasks.filter((t) => t.task_id.toLowerCase().startsWith(prefix)).map((t) => t.task_id);
    } catch {
      return [];
    }
  },
  action: async (ctx, args) => {
    const task_id = args.trim();

    if (!task_id) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule delete <task_id>")
      );
      return;
    }

    const result = await ctx.request<{ error?: string; task_id?: string }>("schedule.delete", { task_id });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `删除失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(ctx.sessionId, `\n任务已删除: ${result.task_id}\n`)
    );
  },
};

// Schedule parent command

const scheduleCommand: SlashCommand = {
  name: "schedule",
  description: "任务管理",
  usage: "/auto-harness schedule <start|list|status|logs|cancel|delete>",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  subCommands: [scheduleStartCommand, scheduleListCommand, scheduleStatusCommand, scheduleLogsCommand, scheduleCancelCommand, scheduleDeleteCommand],
  completion: (_ctx, partial) => {
    const subNames = ["start", "list", "status", "logs", "cancel", "delete"];
    const prefix = partial.trim().toLowerCase();
    if (!prefix) return subNames;
    return subNames.filter((n) => n.startsWith(prefix));
  },
  action: (ctx, args) => {
    const subcommand = args.trim().split(/\s+/)[0];
    if (!subcommand) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness schedule <subcommand>\n子命令: start, list, status, logs, cancel, delete")
      );
    }
  },
};

// Run command - one-time task execution

const runCommand: SlashCommand = {
  name: "run",
  description: "执行一次性 auto_harness 任务",
  usage: "/auto-harness run <query>",
  example: "/auto-harness run 优化数据库查询性能",
  kind: CommandKind.BUILT_IN,
  takesArgs: true,
  action: async (ctx, args) => {
    const query = args.trim();

    if (!query) {
      ctx.addItem(
        addError(ctx.sessionId, "用法: /auto-harness run <query>")
      );
      return;
    }

    // Check config
    const configCheck = await ctx.request<{ valid: boolean; missing_fields?: Array<{ id: string; prompt: string }> }>("schedule.check_config", {});

    const missingFields = configCheck.missing_fields as Array<{ id: string; prompt: string }> | undefined;
    if (missingFields && missingFields.length > 0) {
      const missingList = missingFields.map(f => `  - ${f.prompt}`).join("\n");
      ctx.addItem(
        addInfo(ctx.sessionId, `检测到配置缺失:\n${missingList}\n\n请使用 /config edit 配置这些字段后重试`)
      );
      return;
    }

    ctx.addItem(addInfo(ctx.sessionId, `\n正在创建一次性任务...\n`, "i"));

    // Create and execute one-time task
    const result = await ctx.request<{ error?: string; task_id?: string; status?: string; message?: string }>("schedule.run", {
      query,
    });

    if (result.error) {
      ctx.addItem(
        addError(ctx.sessionId, `创建失败: ${result.error}`)
      );
      return;
    }

    ctx.addItem(
      addInfo(ctx.sessionId, `\n一次性任务已创建并开始执行\nID: ${result.task_id}\n状态: ${result.status}\n`)
    );

    // Start streaming logs
    if (result.task_id) {
      await streamCurrentLogs(ctx, result.task_id);
    }
  },
};

// Main auto-harness command

export function createAutoHarnessCommand(): SlashCommand {
  return {
    name: "auto-harness",
    description: "Auto-Harness 任务管理",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    subCommands: [runCommand, scheduleCommand],
    completion: (_ctx, partial) => {
      const subNames = ["run", "schedule"];
      const prefix = partial.trim().toLowerCase();
      if (!prefix) return subNames;
      return subNames.filter((n) => n.startsWith(prefix));
    },
    action: (ctx, args) => {
      const text = args.trim();
      if (!text) {
        ctx.addItem(
          addError(ctx.sessionId, "用法: /auto-harness <run|schedule>\n子命令: run, schedule")
        );
      }
    },
  };
}

// Helper functions

function parseScheduleStartArgs(args: string): { interval: number; query: string } {
  const parts = parseArgs(args);

  let interval = 0;
  let queryParts: string[] = [];
  let i = 0;

  while (i < parts.length) {
    if (parts[i] === "--interval" || parts[i] === "-i") {
      i++;
      if (i < parts.length) {
        interval = parseInt(parts[i], 10) || 0;
        i++;
      }
    } else {
      queryParts.push(parts[i]);
      i++;
    }
  }

  return {
    interval,
    query: queryParts.join(" "),
  };
}

function formatLocalTime(isoTime?: string): string {
  if (!isoTime) return "未知";
  try {
    const date = new Date(isoTime);
    // Format as local time: YYYY-MM-DD HH:mm
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    const hours = String(date.getHours()).padStart(2, "0");
    const minutes = String(date.getMinutes()).padStart(2, "0");
    return `${year}-${month}-${day} ${hours}:${minutes}`;
  } catch {
    return isoTime;
  }
}

function parseLogArgs(args: string): { task_id: string; log_type: string; history_index: number } {
  const parts = parseArgs(args);

  let log_type = "current";
  let history_index = -1;

  // First, extract --history index if present
  const historyMatch = args.match(/--history\s+(\d+)/);
  const historyIndexValue = historyMatch ? historyMatch[1] : null;

  if (args.includes("--current")) {
    log_type = "current";
    history_index = -1;
  } else if (historyMatch) {
    log_type = "history";
    history_index = parseInt(historyMatch[1], 10);
  }

  // Find task_id: first non-flag argument, excluding history index value
  const task_id = parts.find((p) => {
    return !p.startsWith("-") && p !== historyIndexValue;
  }) || "";

  return { task_id, log_type, history_index };
}