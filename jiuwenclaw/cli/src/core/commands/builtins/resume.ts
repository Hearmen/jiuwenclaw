import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createResumeCommand(): SlashCommand {
  return {
    name: "resume",
    altNames: ["continue"],
    description: "Resume a previous conversation",
    usage: "/resume [conversation id or search term]",
    example: "/resume sess_1234",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const value = args.trim();
      try {
        const payload = await ctx.request<{
          session_id?: string;
          query?: string;
          resumed?: boolean;
          preview?: string;
        }>("command.resume", value ? { query: value } : {});
        const nextSessionId = payload.session_id?.trim();
        if (payload.resumed && nextSessionId) {
          ctx.updateSession(nextSessionId);
          ctx.clearEntries();
          ctx.addItem(addInfo(nextSessionId, `Resumed session ${nextSessionId}`, "r"));
          void ctx.restoreHistory(nextSessionId);
          return;
        }
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            nextSessionId
              ? `Resume candidate: ${nextSessionId}`
              : value
                ? `No resume match for ${value}`
                : "No resumable session returned",
            "r",
            {
              view: "kv",
              title: "Resume",
              items: [
                ...(nextSessionId ? [{ label: "session", value: nextSessionId }] : []),
                ...(payload.query ? [{ label: "query", value: payload.query }] : []),
                ...(payload.preview ? [{ label: "preview", value: payload.preview }] : []),
              ],
            },
          ),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `resume failed: ${message}`));
      }
    },
  };
}
