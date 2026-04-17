import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createModeCommand(): SlashCommand {
  const directModes = ["agent.plan", "agent.fast", "code.plan", "code.normal", "team"] as const;
  const modeAlias: Record<
    string,
    "agent.plan" | "agent.fast" | "code.plan" | "code.normal" | "team"
  > = {
    plan: "agent.plan",
    agent: "agent.fast",
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
    usage: "/mode <agent.plan|agent.fast|code.plan|code.normal|team>",
    example: "/mode agent.plan",
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
            "usage: /mode <agent.plan|agent.fast|code.plan|code.normal|team>",
          ),
        );
        return;
      }
      try {
        await ctx.request("mode.set", { mode: requestedMode });
      } catch {
        // Some backends still accept mode only on chat.send.
      }
      ctx.setMode(nextMode);
      ctx.addItem(makeItem(ctx.sessionId, "info", `Mode set to ${requestedMode}`, "m"));
    },
  };
}
