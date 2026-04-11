import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createDiffCommand(): SlashCommand {
  return {
    name: "diff",
    description: "View uncommitted changes and per-turn diffs",
    usage: "/diff",
    example: "/diff",
    kind: CommandKind.BUILT_IN,
    action: async (ctx) => {
      try {
        const payload = await ctx.request<{
          summary?: string;
          items?: Array<{ label: string; value?: string }>;
        }>("command.diff", {});
        ctx.addItem(
          addInfo(ctx.sessionId, payload.summary || "Workspace diff summary", "d", {
            view: "kv",
            title: "Diff",
            items: payload.items ?? [],
          }),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `diff failed: ${message}`));
      }
    },
  };
}
