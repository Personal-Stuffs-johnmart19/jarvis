import { readToolResult } from "../lib/toolHistory";
import {
  preferenceInputSchema,
  parsePreferenceChange,
} from "./preferenceSchema";
import { tool } from "ai";
import { z } from "zod";
import { invoke } from "@/host/core";
import { usePreferencesStore } from "@/modules/settings/preferences";
import {
  setTheme,
  setThemeId,
  setEditorFontSize,
  setEditorWordWrap,
  setDefaultModel,
  setLmstudioReasoning,
} from "@/modules/settings/store";
import { useChatStore } from "../store/chatStore";
import { useAgentsStore } from "../store/agentsStore";
import { useSnippetsStore } from "../store/snippetsStore";
import { loadMessages } from "../lib/sessions";
import {
  MODELS,
  compatModelIdForEndpoint,
  endpointIdFromCompatModel,
  isCompatModelId,
  resolveModel,
  providerNeedsKey,
  type ModelId,
} from "../config";
import { lmCatalog } from "../lib/lmstudio";
import type { ToolContext } from "./context";

function page<T>(items: T[], query: string, cursor: number, limit: number) {
  const filtered = query
    ? items.filter((i) =>
        JSON.stringify(i).toLowerCase().includes(query.toLowerCase()),
      )
    : items;
  const selected: (T | { truncated: boolean; preview: string })[] = [];
  let bytes = 0;
  for (const item of filtered.slice(cursor, cursor + limit)) {
    const size = new TextEncoder().encode(JSON.stringify(item)).length;
    if (bytes + size > 24000) {
      if (!selected.length)
        selected.push({
          truncated: true,
          preview: JSON.stringify(item).slice(0, 5000),
        });
      break;
    }
    selected.push(item);
    bytes += size;
  }
  const end = cursor + selected.length;
  return {
    items: selected,
    total: filtered.length,
    next_cursor: end < filtered.length && selected.length ? end : null,
  };
}
const range = {
  query: z.string().max(100).default(""),
  cursor: z.number().int().min(0).default(0),
  limit: z.number().int().min(1).max(50).default(20),
};
export function publicPreferences() {
  const p = usePreferencesStore.getState();
  return {
    theme: p.theme,
    themeId: p.themeId,
    editorFontSize: p.editorFontSize,
    editorWordWrap: p.editorWordWrap,
    customInstructions: p.customInstructions,
    defaultModelId: p.defaultModelId,
    lmstudioModelId: p.lmstudioModelId,
    lmstudioReasoning: p.lmstudioReasoning,
  };
}
export function buildDataServices(ctx: ToolContext) {
  return {
    data_query: tool({
      description:
        "Query the canonical data used by Jarvis UI: sessions, preferences, agents, snippets, memory, suggestions, queue, playlists, journal or runtime. No credential/keyring data. Paginate with next_cursor. Data and memory.",
      inputSchema: z.object({
        source: z.enum([
          "sessions",
          "preferences",
          "agents",
          "snippets",
          "memory",
          "suggestions",
          "queue",
          "playlists",
          "journal",
          "runtime",
        ]),
        ...range,
      }),
      execute: async ({ source, query, cursor, limit }) => {
        if (source === "sessions")
          return page(useChatStore.getState().sessions, query, cursor, limit);
        if (source === "preferences") return publicPreferences();
        if (source === "agents")
          return page(useAgentsStore.getState().all(), query, cursor, limit);
        if (source === "snippets")
          return page(
            useSnippetsStore.getState().snippets,
            query,
            cursor,
            limit,
          );
        return invoke("backend", {
          method: "services.read",
          params: {
            name: "data.query",
            args: { source, query, cursor, limit },
          },
        });
      },
    }),
    conversation_read: tool({
      description:
        "Read text from the canonical saved chat session, or the active session by default. Reasoning and raw tool payloads are excluded. Chat history.",
      inputSchema: z.object({
        session_id: z.string().max(128).optional(),
        ...range,
      }),
      execute: async ({ session_id, query, cursor, limit }) => {
        const state = useChatStore.getState();
        const id = session_id ?? ctx.getSessionId();
        if (!id || !state.sessions.some((s) => s.id === id))
          return { error: "Unknown session" };
        const { getOrCreateChat } = await import("../store/chatRuntime");
        const messages =
          id === state.activeSessionId
            ? getOrCreateChat(id).messages
            : ((await loadMessages(id)) ?? []);
        return page(
          messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({
              id: m.id,
              role: m.role,
              text: m.parts
                .filter((p) => p.type === "text")
                .map((p) => (p.type === "text" ? p.text : ""))
                .join("\n")
                .slice(0, 12000),
            })),
          query,
          cursor,
          limit,
        );
      },
    }),
    tool_result_read: tool({
      description:
        "Read a completed tool output from this chat by tool_call_id. Use offsets to recover details omitted from model context, without repeating execution. No reasoning, inputs, pending approvals or other sessions.",
      inputSchema: z.object({
        tool_call_id: z.string().min(1).max(128),
        offset: z.number().int().min(0).default(0),
        limit: z.number().int().min(1).max(8000).default(2000),
      }),
      execute: async ({ tool_call_id, offset, limit }) => {
        const id = ctx.getSessionId();
        if (!id || !useChatStore.getState().sessions.some((s) => s.id === id))
          return { error: "Unknown session" };
        const { getOrCreateChat } = await import("../store/chatRuntime");
        return readToolResult(getOrCreateChat(id).messages, tool_call_id, offset, limit);
      },
    }),
    voice_activation: tool({
      description:
        "Read or change actual Jarvis voice activation (wake-word) mode. For 'enable voice activation' use enabled=true; disable with false. Only changes filtering for Jarvis; never turns on a muted microphone. Read the returned state before reporting success. This is not a text preference.",
      inputSchema: z.object({ enabled: z.boolean().optional() }),
      execute: async (args) =>
        invoke("backend", { method: "voice.activation", params: args }),
    }),
    preferences_set: tool({
      description:
        "Change an explicit non-secret setting in the same persistent store used by the GUI. Requires approval. Custom instructions are GUI-only. This tool does not control microphone, voice activation, credentials or permissions.",
      inputSchema: preferenceInputSchema,
      needsApproval: true,
      execute: async (raw) => {
        const input = parsePreferenceChange(raw);
        switch (input.key) {
          case "theme":
            await setTheme(input.value);
            break;
          case "themeId": {
            const { listBuiltinThemes } = await import(
              "@/modules/theme/themes"
            );
            const { listCustomThemes } = await import(
              "@/modules/theme/customThemes"
            );
            if (
              ![...listBuiltinThemes(), ...(await listCustomThemes())].some(
                (t) => t.id === input.value,
              )
            )
              throw new Error("Unknown theme");
            await setThemeId(input.value);
            break;
          }
          case "editorFontSize":
            await setEditorFontSize(input.value);
            break;
          case "editorWordWrap":
            await setEditorWordWrap(input.value);
            break;
          case "lmstudioReasoning":
            await setLmstudioReasoning(input.value);
            break;
        }
        return { ok: true, key: input.key, value: input.value };
      },
    }),
    models_list: tool({
      description:
        "List provider models/configured availability and downloaded LM Studio model capabilities without loading weights. No API keys are returned.",
      inputSchema: z.object({
        ...range,
        local_details: z.boolean().default(false),
      }),
      execute: async ({ query, cursor, limit, local_details }) => {
        const state = useChatStore.getState(),
          p = usePreferencesStore.getState();
        const models = MODELS.map((m) => ({
          ...m,
          configured: providerNeedsKey(m.provider)
            ? Boolean(state.apiKeys[m.provider])
            : m.provider === "lmstudio"
              ? Boolean(p.lmstudioModelId)
              : m.provider === "mlx"
                ? Boolean(p.mlxModelId)
                : m.provider === "ollama"
                  ? Boolean(p.ollamaModelId)
                  : true,
        }));
        const custom = p.customEndpoints.map((ep) => ({
          id: compatModelIdForEndpoint(ep.id),
          provider: "openai-compatible",
          label: ep.name,
          model: ep.modelId,
          configured: Boolean(ep.modelId && ep.baseURL),
        }));
        const result = page([...models, ...custom], query, cursor, limit);
        return {
          ...result,
          ...(local_details
            ? {
                local: await lmCatalog(p.lmstudioBaseURL).catch((e) => ({
                  error: String(e),
                })),
              }
            : {}),
        };
      },
    }),
    model_select: tool({
      description:
        "Select a configured model for following tasks, using the GUI's shared default-model setting. Requires approval; does not add keys or download weights.",
      inputSchema: z.object({ id: z.string().max(128) }),
      needsApproval: true,
      execute: async ({ id }) => {
        const state = useChatStore.getState(),
          p = usePreferencesStore.getState();
        const model = resolveModel(id, p.customEndpoints);
        if (
          isCompatModelId(id) &&
          !p.customEndpoints.some(
            (ep) =>
              ep.id === endpointIdFromCompatModel(id) &&
              ep.modelId &&
              ep.baseURL,
          )
        )
          throw new Error("Endpoint is not configured");
        if (
          !isCompatModelId(id) &&
          providerNeedsKey(model.provider) &&
          !state.apiKeys[model.provider]
        )
          throw new Error("Provider is not connected");
        if (model.provider === "lmstudio" && !p.lmstudioModelId)
          throw new Error("Select a downloaded local model in Settings first");
        if (model.provider === "mlx" && !p.mlxModelId)
          throw new Error("Select an MLX model in Settings first");
        if (model.provider === "ollama" && !p.ollamaModelId)
          throw new Error("Select an Ollama model in Settings first");
        await setDefaultModel(id as ModelId);
        return { ok: true, id, effective: "following model request" };
      },
    }),
    agent_select: tool({
      description:
        "Select an existing shared agent/persona, exactly as in the GUI. Requires approval; does not alter tool permission policy.",
      inputSchema: z.object({ id: z.string().max(128) }),
      needsApproval: true,
      execute: async ({ id }) => {
        const state = useAgentsStore.getState();
        if (!state.all().some((a) => a.id === id))
          throw new Error("Unknown agent");
        state.setActiveId(id);
        return { ok: true, id };
      },
    }),
    snippet_save: tool({
      description:
        "Save a reusable prompt snippet in the same GUI snippet store. The content is data, never executed. Requires approval.",
      inputSchema: z.object({
        id: z.string().regex(/^[A-Za-z0-9_-]{1,128}$/),
        handle: z.string().regex(/^[a-z0-9-]{1,64}$/),
        name: z.string().min(1).max(100),
        description: z.string().max(500).default(""),
        content: z.string().max(12000),
      }),
      needsApproval: true,
      execute: async (snippet) => {
        useSnippetsStore.getState().upsert(snippet);
        return { ok: true, id: snippet.id };
      },
    }),
  };
}
