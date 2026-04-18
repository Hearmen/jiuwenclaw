import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export interface ModelMeta {
  name: string;
  model_name?: string;
  client_provider?: string;
  api_base?: string;
}

export interface ModelListPayload {
  current?: string;
  available_models?: string[];
  models?: ModelMeta[];
}

export function createModelCommand(): SlashCommand {
  return {
    name: "model",
    description: "View, add, or switch AI models defined in config.yaml",
    usage: "/model [name] | /model add <name> <key=value>...",
    example: "/model work (switch)\n/model add work model=gpt-4 api_key=xxx",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const raw = args.trim();

      // 1. Handle Add Model: /model add <name> <key=value> ...
      if (raw.match(/^add\s+\S+/)) {
        const parts = raw.split(/\s+/);
        if (parts.length < 3) {
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              "Usage: /model add <name> key=value ...",
              "m",
            ),
          );
          return;
        }

        const target = parts[1];
        const settings: Record<string, string> = {};
        for (let i = 2; i < parts.length; i++) {
          const eqIdx = parts[i].indexOf("=");
          if (eqIdx > 0) {
            const key = parts[i].substring(0, eqIdx);
            const val = parts[i].substring(eqIdx + 1);
            settings[key] = val;
          }
        }

        try {
          await ctx.request("command.model", {
            action: "add_model",
            target: target,
            config: settings,
          });
          ctx.addItem(
            addInfo(ctx.sessionId, `Added/Updated model config: ${target}`, "m", {
              view: "kv",
              items: Object.entries(settings).map(([k, v]) => ({
                label: k,
                value: k.toLowerCase().includes("key") || k.toLowerCase().includes("token") ? "****" : v,
              })),
            }),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          ctx.addItem(addError(ctx.sessionId, `Failed to add model: ${message}`));
        }
        return;
      }

      // 2. Handle View and Switch
      const value = raw;
      try {
        // If no arg or "list", show selectable model list
        if (value === "" || value === "list") {
          const payload = await ctx.request<ModelListPayload>("command.model", {});
          const models = payload.available_models ?? [];
          const current = payload.current ?? "unknown";
          if (models.length === 0) {
            ctx.addItem(addInfo(ctx.sessionId, "No models configured", "m"));
            return;
          }
          const items = models.map((m, i) => {
            const isCurrent = m === current;
            return {
              label: String(i + 1),
              value: `${m}${isCurrent ? " (current)" : ""}`,
            };
          });
          ctx.addItem(
            addInfo(ctx.sessionId, `Available models (${models.length} total)`, "m", {
              view: "list",
              title: "Switch Model",
              items,
            }),
          );
          return;
        }

        // Switch to specific model
        const payload = await ctx.request<{
          current?: string;
          requested?: string;
          applied?: boolean;
          type?: string;
        }>("command.model", { model: value });

        const isSwitch = !!payload.requested;
        const title = isSwitch
          ? `Switched to: ${payload.requested}`
          : "Model Configuration";
        const icon = isSwitch ? "m" : "c";

        ctx.addItem(
          addInfo(
            ctx.sessionId,
            payload.requested
              ? `Switched model config to: ${payload.requested}`
              : `Current model: ${payload.current ?? "unknown"}`,
            icon,
            {
              view: "kv",
              title,
              items: [
                { label: "current", value: payload.current ?? "unknown" },
                ...(payload.type ? [{ label: "type", value: payload.type }] : []),
                ...(typeof payload.applied === "boolean"
                  ? [{ label: "applied", value: String(payload.applied) }]
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
