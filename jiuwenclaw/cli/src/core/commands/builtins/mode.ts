import type { AutocompleteItem } from "@mariozechner/pi-tui";
import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

/** TUI `/mode` 参数补全：分组标题不可选；默认落在 agent.plan。 */
export function buildModeAutocompleteItems(): AutocompleteItem[] {
  return [
    { value: "__mode_hdr_agent__", label: "agent", navigable: false },
    { value: "agent.plan", label: "    plan", navigable: true },
    { value: "agent.fast", label: "    fast", navigable: true },
    { value: "__mode_hdr_code__", label: "code", navigable: false },
    { value: "code.normal", label: "    normal", navigable: true },
    { value: "code.plan", label: "    plan", navigable: true },
    { value: "team", label: "team", navigable: true },
  ];
}

export function createModeCommand(): SlashCommand {
  const directModes = [
    "agent",
    "code",
    "agent.plan",
    "agent.fast",
    "code.plan",
    "code.normal",
    "team",
  ] as const;
  const modeAlias: Record<
    string,
    "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team"
  > = {
    plan: "agent.plan",
    agent: "agent.plan",
    code: "code.normal",
    "agent.plan": "agent.plan",
    "agent.fast": "agent.fast",
    "code.plan": "code.plan",
    "code.normal": "code.normal",
    team: "team",
  };

  return {
    name: "mode",
    description: "Switch chat mode",
    usage: "/mode <agent|code|team>",
    example: "/mode agent",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    completion: async () => [...directModes],
    action: async (ctx, args) => {
      const requestedMode = args.trim();
      const nextMode = modeAlias[requestedMode];
      if (!nextMode) {
        ctx.addItem(
          makeItem(
            ctx.sessionId,
            "error",
            "usage: /mode <agent|code|team>",
          ),
        );
        return;
      }
      try {
        await ctx.request("mode.set", { mode: nextMode });
      } catch {
        // Some backends still accept mode only on chat.send.
      }
      ctx.setMode(nextMode);
      ctx.addItem(makeItem(ctx.sessionId, "info", `Mode set to ${requestedMode}`, "m"));
    },
  };
}
