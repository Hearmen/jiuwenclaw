import { padToWidth } from "./rendering/text.js";
import { palette } from "./theme.js";

export function buildWelcomeLines(width: number): string[] {
  return [
    padToWidth(palette.text.secondary("JiuwenClaw CLI"), width),
    padToWidth(
      palette.text.dim(
        "Ask questions, inspect tool output, and drive the session from the terminal.",
      ),
      width,
    ),
    padToWidth(
      palette.text.dim("Use /help for commands or /hotkey for keyboard shortcuts."),
      width,
    ),
    " ".repeat(width),
  ];
}
