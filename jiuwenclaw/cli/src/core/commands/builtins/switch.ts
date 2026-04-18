import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createSwitchCommand(): SlashCommand {
  return {
    name: "switch",
    description: "Switch to an existing session",
    usage: "/switch <session_id>",
    example: "/switch my-session",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      if (ctx.isProcessing) {
        ctx.addItem(makeItem(ctx.sessionId, "error", "session is busy"));
        return;
      }
      const targetId = args.trim();
      if (!targetId) {
        ctx.addItem(makeItem(ctx.sessionId, "error", "usage: /switch <session_id>"));
        return;
      }
      ctx.updateSession(targetId);
      ctx.clearEntries();
      ctx.addItem(makeItem(targetId, "info", `Switched to session ${targetId}`, "i"));
      await ctx.restoreHistory(targetId);
    },
  };
}
