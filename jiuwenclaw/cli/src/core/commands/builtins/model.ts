import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createModelCommand(): SlashCommand {
  return {
    name: "model",
    description: "Set the active AI model",
    usage: "/model [model]",
    example: "/model sonnet",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const value = args.trim();
      try {
        const payload = await ctx.request<{
          current?: string;
          requested?: string;
          applied?: boolean;
          available?: string[];
        }>("command.model", value ? { model: value } : {});
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            payload.requested
              ? `Requested model: ${payload.requested}`
              : `Current model: ${payload.current ?? "unknown"}`,
            "m",
            {
              view: "kv",
              title: "Model",
              items: [
                { label: "current", value: payload.current ?? "unknown" },
                ...(payload.requested ? [{ label: "requested", value: payload.requested }] : []),
                ...(typeof payload.applied === "boolean"
                  ? [{ label: "applied", value: String(payload.applied) }]
                  : []),
                ...(payload.available?.length
                  ? [{ label: "available", value: payload.available.join(", ") }]
                  : []),
              ],
            },
          ),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `model failed: ${message}`));
      }
    },
  };
}
