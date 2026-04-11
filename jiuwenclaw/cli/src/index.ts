#!/usr/bin/env node

import { ProcessTerminal, TUI } from "@mariozechner/pi-tui";
import { parseArgs } from "node:util";
import { CliPiAppState } from "./app-state.js";
import { CommandService } from "./core/commands/CommandService.js";
import { createBuiltinCommands } from "./core/commands/registry.js";
import { WsClient } from "./core/ws-client.js";
import { AppScreen } from "./ui/app-screen.js";

const { values } = parseArgs({
  options: {
    url: { type: "string", default: "ws://127.0.0.1:19001/cli" },
    session: { type: "string" },
    token: { type: "string", default: "" },
    help: { type: "boolean", short: "h" },
  },
  strict: true,
});

if (values.help) {
  console.log(`jiuwenclaw-tui-pi - Terminal UI for JiuwenClaw using pi-tui

Options:
  --url <url>       Gateway CLI WebSocket URL (default: ws://127.0.0.1:19001/cli)
  --session <id>    Resume a specific session
  --token <token>   Authentication token
  -h, --help        Show this help
`);
  process.exit(0);
}

if (!process.stdin.isTTY || !process.stdout.isTTY) {
  console.error("jiuwenclaw-tui-pi requires an interactive TTY");
  process.exit(1);
}

const wsClient = new WsClient(values.url ?? "ws://127.0.0.1:19001/cli", values.token ?? "");
const appState = new CliPiAppState(wsClient, values.session);
const commandService = new CommandService();
commandService.register(createBuiltinCommands());

const terminal = new ProcessTerminal();
const tui = new TUI(terminal);
tui.setClearOnShrink(true);

let closed = false;
let screen: AppScreen | null = null;

function closeUi(exitCode = 0): void {
  if (closed) return;
  closed = true;
  screen?.dispose();
  appState.stop();
  try {
    tui.stop();
  } catch {
    // Ignore repeated stop failures.
  }
  process.exit(exitCode);
}

function crash(error: unknown): void {
  const message = error instanceof Error ? (error.stack ?? error.message) : String(error);
  if (!closed) {
    screen?.dispose();
    appState.stop();
    try {
      tui.stop();
    } catch {
      // Ignore repeated stop failures.
    }
    closed = true;
  }
  console.error(message);
  process.exit(1);
}

screen = new AppScreen(tui, appState, commandService, () => closeUi(0));
tui.addChild(screen);
tui.setFocus(screen);

process.on("SIGTERM", () => closeUi(0));
process.on("uncaughtException", crash);
process.on("unhandledRejection", crash);

appState.start();
tui.start();
