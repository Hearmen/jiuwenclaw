import { addError, addInfo, extractObject, formatValue } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

function flattenConfigEntries(
  value: unknown,
  prefix = "",
): Array<{ label: string; value: string }> {
  if (value === null || value === undefined) {
    return prefix ? [{ label: prefix, value: String(value) }] : [];
  }

  if (Array.isArray(value)) {
    if (!prefix) {
      return value.map((item, index) => ({ label: `[${index}]`, value: formatValue(item) }));
    }
    return [{ label: prefix, value: formatValue(value) }];
  }

  if (typeof value !== "object") {
    return prefix
      ? [{ label: prefix, value: formatValue(value) }]
      : [{ label: "value", value: formatValue(value) }];
  }

  const obj = value as Record<string, unknown>;
  const entries = Object.entries(obj).sort(([left], [right]) => left.localeCompare(right));
  const flattened = entries.flatMap(([key, nested]) => {
    const nextPrefix = prefix ? `${prefix}.${key}` : key;
    if (nested && typeof nested === "object" && !Array.isArray(nested)) {
      return flattenConfigEntries(nested, nextPrefix);
    }
    return [{ label: nextPrefix, value: formatValue(nested) }];
  });

  return flattened.length > 0 ? flattened : prefix ? [{ label: prefix, value: "{}" }] : [];
}

export function createConfigCommand(): SlashCommand {
  return {
    name: "config",
    altNames: ["settings", "setting"],
    description: "View backend config",
    usage: "/config [key]",
    example: "/config model_name",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const key = args.trim();
      let payload: unknown;
      try {
        payload = await ctx.request("config.get", key ? { key } : {});
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `failed to load config: ${message}`));
        return;
      }

      const objectPayload = extractObject(payload);
      const items = objectPayload
        ? flattenConfigEntries(objectPayload)
        : [{ label: key || "value", value: formatValue(payload) }];

      if (items.length === 0) {
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            key ? `No config value found for ${key}` : "No config values returned",
            "c",
          ),
        );
        return;
      }

      ctx.addItem(
        addInfo(ctx.sessionId, key ? `Config: ${key}` : "Config values", "c", {
          view: "kv",
          title: key ? `Config · ${key}` : "Config",
          items,
        }),
      );
    },
  };
}
