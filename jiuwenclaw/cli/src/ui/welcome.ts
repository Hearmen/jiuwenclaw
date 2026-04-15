import { visibleWidth } from "@mariozechner/pi-tui";
import { padToWidth } from "./rendering/text.js";
import { palette } from "./theme.js";

const ART_TITLE = ["╭──╮  ╭──╮  ╭──╮", "│九│  │纹│  │爪│", "╰──╯  ╰──╯  ╰──╯"] as const;

function centerLine(line: string, width: number): string {
  const padding = Math.max(0, Math.floor((width - visibleWidth(line)) / 2));
  return padToWidth(`${" ".repeat(padding)}${line}`, width);
}

export function buildWelcomeLines(width: number): string[] {
  const artWidth = Math.max(...ART_TITLE.map((line) => visibleWidth(line)));
  if (width >= artWidth + 6) {
    return [
      ...ART_TITLE.map((line) => centerLine(palette.text.dim(line), width)),
      centerLine(palette.text.subtle("/resume to continue · /help for commands"), width),
      " ".repeat(width),
    ];
  }

  return [
    padToWidth(palette.text.dim("九纹爪"), width),
    padToWidth(palette.text.subtle("/resume to continue · /help for commands"), width),
    " ".repeat(width),
  ];
}
