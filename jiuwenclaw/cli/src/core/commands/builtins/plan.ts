import { addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createPlanCommand(): SlashCommand {
  return {
    name: "plan",
    description: "Enable plan mode or view the current session plan",
    usage: "/plan [open|<description>]",
    example: "/plan outline the migration steps",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: (ctx, args) => {
      const value = args.trim();
      if (ctx.mode !== "agent.plan") {
        ctx.setMode("agent.plan");
      }

      if (!value) {
        ctx.addItem(addInfo(ctx.sessionId, "Plan mode enabled", "p"));
        return;
      }

      if (value === "open") {
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            "Plan mode is active. Type your planning request directly or run /plan <description>.",
            "p",
          ),
        );
        return;
      }

      const requestId = ctx.sendMessage(value, "agent.plan");
      if (!requestId) {
        ctx.addItem(
          addInfo(ctx.sessionId, "offline: waiting for reconnect before sending plan request", "p"),
        );
      }
    },
  };
}
