import { visibleWidth } from "@mariozechner/pi-tui";
import type { ConnectionStatus } from "../core/ws-client.js";
import { padToWidth } from "./rendering/text.js";
import { palette } from "./theme.js";

const ART_TITLE = [
  "╔════╗  ╔════╗  ╔════╗",
  "║    ║  ║    ║  ║    ║",
  "║ 九 ║  ║ 纹 ║  ║ 爪 ║",
  "║    ║  ║    ║  ║    ║",
  "╚════╝  ╚════╝  ╚════╝",
] as const;

function centerLine(line: string, width: number): string {
  const padding = Math.max(0, Math.floor((width - visibleWidth(line)) / 2));
  return padToWidth(`${" ".repeat(padding)}${line}`, width);
}

function connectionHint(status: ConnectionStatus): string | null {
  switch (status) {
    case "connecting":
      return "Connecting to backend…";
    case "reconnecting":
      return "Backend unavailable · retrying connection";
    case "idle":
      return "Backend unavailable · start jiuwenclaw-gateway or check --url";
    case "auth_failed":
      return "Authentication failed · check --token";
    case "connected":
    default:
      return null;
  }
}

export function buildWelcomeLines(width: number, connectionStatus: ConnectionStatus): string[] {
  const artWidth = Math.max(...ART_TITLE.map((line) => visibleWidth(line)));
  const hint = connectionHint(connectionStatus);
  if (width >= artWidth + 6) {
    return [
      ...ART_TITLE.map((line) => centerLine(palette.text.dim(line), width)),
      centerLine(palette.text.subtle("/resume to continue · /help for commands"), width),
      ...(hint ? [centerLine(palette.text.subtle(hint), width)] : []),
      " ".repeat(width),
    ];
  }

  return [
    padToWidth(palette.text.dim("九纹爪"), width),
    padToWidth(palette.text.subtle("/resume to continue · /help for commands"), width),
    ...(hint ? [padToWidth(palette.text.subtle(hint), width)] : []),
    " ".repeat(width),
  ];
}
