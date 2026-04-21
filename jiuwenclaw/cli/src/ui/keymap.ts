import { matchesKey } from "@mariozechner/pi-tui";

/**
 * 快捷键约定（Ctrl+C）：
 * 用户在 CLI/TUI 按下 Ctrl+C 时，始终尝试向服务端发送 `chat.interrupt`，
 * 强制结束当前 session 正在运行的任务；不退出 CLI/TUI。
 */

export interface AppScreenKeymapDelegate {
  interruptTask(): void;
  toggleTodos(): void;
  toggleTeamPanel(): void;
  toggleTranscript(): void;
  redraw(): void;
}

interface KeyBinding {
  key: Parameters<typeof matchesKey>[1];
  label: string;
  description: string;
  run: (delegate: AppScreenKeymapDelegate) => void;
}

export const APP_SCREEN_KEY_BINDINGS: readonly KeyBinding[] = [
  {
    key: "ctrl+c",
    label: "ctrl+c",
    description: "请求强制结束当前任务（不退出 CLI）",
    run: (delegate) => {
      delegate.interruptTask();
    },
  },
  {
    key: "ctrl+l",
    label: "ctrl+l",
    description: "redraw screen",
    run: (delegate) => {
      delegate.redraw();
    },
  },
  {
    key: "ctrl+t",
    label: "ctrl+t",
    description: "toggle todos",
    run: (delegate) => {
      delegate.toggleTodos();
    },
  },
  {
    key: "ctrl+g",
    label: "ctrl+g",
    description: "toggle team panel",
    run: (delegate) => {
      delegate.toggleTeamPanel();
    },
  },
  {
    key: "ctrl+o",
    label: "ctrl+o",
    description: "toggle transcript detail",
    run: (delegate) => {
      delegate.toggleTranscript();
    },
  },
] as const;

export function handleAppScreenKeyInput(data: string, delegate: AppScreenKeymapDelegate): boolean {
  for (const binding of APP_SCREEN_KEY_BINDINGS) {
    if (!matchesKey(data, binding.key)) continue;
    binding.run(delegate);
    return true;
  }

  return false;
}
