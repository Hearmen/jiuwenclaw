import { matchesKey } from "@mariozechner/pi-tui";
import type { AppSnapshot } from "../app-state.js";

export interface AppScreenKeymapDelegate {
  getSnapshot(): AppSnapshot;
  cancel(): void;
  requestExit(): void;
  toggleThinking(): void;
  toggleToolDetails(): void;
  toggleShortcutHelp(): void;
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
    description: "cancel active run or arm exit",
    run: (delegate) => {
      const snapshot = delegate.getSnapshot();
      if (snapshot.isProcessing) {
        delegate.cancel();
      } else {
        delegate.requestExit();
      }
    },
  },
  {
    key: "ctrl+t",
    label: "ctrl+t",
    description: "toggle thinking detail",
    run: (delegate) => {
      delegate.toggleThinking();
    },
  },
  {
    key: "ctrl+o",
    label: "ctrl+o",
    description: "toggle tool detail",
    run: (delegate) => {
      delegate.toggleToolDetails();
    },
  },
  {
    key: "ctrl+k",
    label: "ctrl+k",
    description: "toggle shortcut help",
    run: (delegate) => {
      delegate.toggleShortcutHelp();
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
