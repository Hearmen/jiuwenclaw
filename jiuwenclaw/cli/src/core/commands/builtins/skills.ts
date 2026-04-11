import { flattenArrayPayload, formatValue, makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createSkillsCommand(): SlashCommand {
  return {
    name: "skills",
    description: "List skills",
    usage: "/skills",
    example: "/skills",
    kind: CommandKind.BUILT_IN,
    action: async (ctx) => {
      const payload = await ctx.request("skills.list", {});
      const items = flattenArrayPayload(payload).map((item, index) => {
        if (item && typeof item === "object") {
          const obj = item as Record<string, unknown>;
          return {
            label: typeof obj.name === "string" ? obj.name : String(index + 1),
            value: typeof obj.path === "string" ? obj.path : undefined,
            description: typeof obj.description === "string" ? obj.description : undefined,
          };
        }
        return { label: String(index + 1), value: formatValue(item) };
      });
      ctx.addItem(
        makeItem(
          ctx.sessionId,
          "info",
          items.length > 0 ? "Installed skills" : "No skills returned",
          "*",
          {
            view: "list",
            title: "Skills",
            items,
          },
        ),
      );
    },
  };
}
