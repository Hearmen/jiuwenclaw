import { generateSessionId } from "../../session-state.js";
import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createTeamCommand(): SlashCommand {
  return {
    name: "team",
    description: "Toggle team mode",
    usage: "/team",
    example: "/team",
    kind: CommandKind.BUILT_IN,
    action: async (ctx) => {
      if (ctx.isProcessing) {
        ctx.addItem(addError(ctx.sessionId, "session is busy; stop the current run before switching team mode"));
        return;
      }

      const nextMode = ctx.mode === "team" ? "plan" : "team";
      const nextId = generateSessionId();
      try {
        await ctx.request("session.create", { session_id: nextId });
      } catch {
        // Some backends may create the session lazily on first message.
      }

      ctx.setMode(nextMode);
      ctx.updateSession(nextId);
      ctx.clearEntries();
      ctx.addItem(
        addInfo(
          nextId,
          nextMode === "team"
            ? `Team mode enabled. Started a fresh conversation in ${nextId}`
            : `Team mode disabled. Back to plan mode in ${nextId}`,
          "t",
        ),
      );
      await ctx.restoreHistory(nextId);
    },
  };
}
