import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createModeCommand(): SlashCommand {
  return {
    name: "mode",
    description: "Switch chat mode",
    usage: "/mode <plan|agent>",
    example: "/mode agent",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    completion: async () => ["plan", "agent"],
    action: async (ctx, args) => {
      const nextMode = args.trim();
      if (nextMode !== "plan" && nextMode !== "agent") {
        ctx.addItem(makeItem(ctx.sessionId, "error", "usage: /mode <plan|agent>"));
        return;
      }
      try {
        await ctx.request("mode.set", { mode: nextMode });
      } catch {
        // Some backends still accept mode only on chat.send.
      }
      ctx.setMode(nextMode);
      ctx.addItem(makeItem(ctx.sessionId, "info", `Mode set to ${nextMode}`, "m"));
    },
  };
}
