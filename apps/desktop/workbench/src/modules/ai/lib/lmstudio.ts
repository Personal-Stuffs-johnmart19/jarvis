import { lmstudioReasoningEffort } from "./lmstudioReasoning";
import { lmstudioModelMetadata, ollamaContextLimit, localMetadata } from "./localContext";
import { z } from "zod";
import { invoke } from "@/host/core";
/** LM Studio's structured-output API uses json_schema; keep every JSON value kind. */
export function normalizeLmstudioRequest(body: string, modelId: string, reasoningSetting: string): string {
  const request = JSON.parse(body) as Record<string, unknown>;
  if (!request || typeof request !== "object" || Array.isArray(request)) return body;
  const format = request.response_format;
  let changed = false;
  if (format && typeof format === "object" && !Array.isArray(format)
      && (format as {type?:unknown}).type === "json_object") {
    request.response_format = { type: "json_schema", json_schema: {
      name: "helper_json_value", strict: true,
      schema: { anyOf: [
        { type: "object", additionalProperties: true },
        { type: "array", items: {} },
        { type: "string" }, { type: "number" }, { type: "boolean" }, { type: "null" },
      ] },
    } };
    changed = true;
  }
  const effort = lmstudioReasoningEffort(modelId, reasoningSetting);
  if (effort !== undefined) { request.reasoning_effort = effort; changed = true; }
  return changed ? JSON.stringify(request) : body;
}


export type LocalModel = {
  key: string;
  type: "llm" | "embedding" | "decision";
  display_name: string;
  max_context_length?: number;
  loaded_instances: { id: string; config: { context_length: number } }[];
  capabilities?: {
    vision: boolean;
    trained_for_tool_use: boolean;
    reasoning?: { allowed_options: string[]; default: string };
  };
};
const schema = z.array(z.object({
  key: z.string().min(1).max(512),
  type: z.enum(["llm", "embedding", "decision"]),
  display_name: z.string().max(512),
  max_context_length: z.number().int().positive().max(16777216).nullable().optional().transform(v => v ?? undefined),
  loaded_instances: z.array(z.object({ id: z.string().max(512), config: z.object({ context_length: z.number().int().positive().max(16777216) }) })).max(32).default([]),
  capabilities: z.object({ vision: z.boolean().default(false), trained_for_tool_use: z.boolean().default(false), reasoning: z.object({ allowed_options: z.array(z.enum(["off", "on", "low", "medium", "high"])).max(5), default: z.string().max(16) }).optional() }).optional(),
})).max(500);
export const lmCatalog = async (baseUrl: string): Promise<LocalModel[]> => schema.parse(await invoke<unknown>("lm_catalog", { baseUrl }));

export const ollamaCatalog = async (baseUrl: string) => z.array(z.object({
  id: z.string().min(1).max(512),
  loaded: z.boolean().nullable(),
  context_length: z.number().int().positive().max(16777216).nullable(),
})).max(500).parse(await invoke<unknown>("ollama_catalog", { baseUrl }));

/** Metadata only: querying capabilities/context never loads a model or sends a prompt. */
export async function localServerModelMetadata(provider: "lmstudio" | "ollama", baseUrl: string, modelKey: string, signal?: AbortSignal) {
  signal?.throwIfAborted();
  if (signal) {
    const { createProxyFetch } = await import("./proxyFetch");
    signal.throwIfAborted();
    const base = baseUrl.trim().replace(/\/+$/, "").replace(/\/v1$/, "");
    const raw = await localMetadata(`${base}/api/${provider === "lmstudio" ? "v1/models" : "ps"}`, createProxyFetch({ allowPrivateNetwork: true }), signal);
    if (provider === "lmstudio") {
      const rows = schema.parse(z.object({ models: z.unknown() }).parse(raw).models);
      return lmstudioModelMetadata(rows, modelKey);
    }
    const rows = z.object({ models: z.array(z.object({ model: z.string().max(512).optional(), name: z.string().max(512).optional(), context_length: z.number().int().positive().max(16777216).nullable().optional() })).max(500) }).parse(raw).models;
    const running = rows.find((model) => (model.model ?? model.name) === modelKey);
    return { contextLimit: ollamaContextLimit(running ? { loaded: true, context_length: running.context_length ?? null } : undefined), supportsVision: false };
  }
  if (provider === "lmstudio") {
    const models = await lmCatalog(baseUrl);
    return lmstudioModelMetadata(models, modelKey);
  }
  const models = await ollamaCatalog(baseUrl);
  return { contextLimit: ollamaContextLimit(models.find((model) => model.id === modelKey)), supportsVision: false };
}

export async function localServerContext(provider: "lmstudio" | "ollama", baseUrl: string, modelKey: string, signal?: AbortSignal) {
  return (await localServerModelMetadata(provider, baseUrl, modelKey, signal)).contextLimit;
}