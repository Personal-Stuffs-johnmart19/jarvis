import { stopOnUnavailableToolCall, stopOnRepeatedInvalidToolCall } from "./errors";
import { t } from "../../../../../src/localization";
import { FIXED_INSTRUCTIONS_FULL_MESSAGE, prepareAgentBudget } from "./agentBudget";
import { budgetBrowserObservations } from "./browserObservation";
import { buildScreenTools } from "../tools/screen";
import { localCaptureBase, restrictScreenHistory } from "./screenCapture";
import { isManagedModelId, discoverManagedModels, managedModelContext, managedModelFetch } from "./managedModels";
import { createReadFailureGuard, READ_FAILURE_MESSAGE } from "./readFailureGuard";
import { projectInterruptedHistory } from "./interruptedHistory";
import type { ToolSet } from "ai";
import { invoke } from "@/host/core";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { localServerModelMetadata, normalizeLmstudioRequest } from "./lmstudio";
import { getJarvis } from "@/jarvis";
import {
  convertToModelMessages,
  pruneMessages,
  isStepCount,
  streamText,
  type LanguageModel,
  type UIMessage,
} from "ai";
import {
  DEFAULT_MODEL_ID,
  endpointIdFromCompatModel,
  getModelContextLimit,
  isCompatModelId,
  LMSTUDIO_DEFAULT_BASE_URL,
  MAX_AGENT_STEPS,
  MLX_DEFAULT_BASE_URL,
  modelKeepsReasoning,
  OLLAMA_DEFAULT_BASE_URL,
  providerNeedsKey,
  resolveModel,
  type CustomEndpoint,
  type ProviderId,
} from "../config";
import { buildTools, type ToolContext } from "../tools/tools";
import { assertContextBudget, replyTokenBudget, CONTEXT_FULL_MESSAGE, compactModelMessagesDetailed } from "./compact";
import type { ProviderKeys, CustomEndpointKeys } from "./keyring";
import { prepareAgentPrompt } from "./prompt";
import { createProxyFetch } from "./proxyFetch";
import { modelFetch } from "./modelFetch";

const localProxyFetch = modelFetch(createProxyFetch({ allowPrivateNetwork: true }));
const providerFetch = modelFetch((input, init) => fetch(input, init));

const TOOL_LABELS: Record<string, (input: Record<string, unknown>) => string> =
  {
    read_file: (i) => `Reading ${shortPath(i.path)}`,
    list_directory: (i) => `Listing ${shortPath(i.path)}`,
    grep: (i) => `Grepping ${ellipsize(String(i.pattern ?? ""), 40)}`,
    glob: (i) => `Globbing ${ellipsize(String(i.pattern ?? ""), 40)}`,
    edit: (i) => `Editing ${shortPath(i.path)}`,
    multi_edit: (i) => `Editing ${shortPath(i.path)}`,
    write_file: (i) => `Writing ${shortPath(i.path)}`,
    create_directory: (i) => `Creating ${shortPath(i.path)}`,
    bash_run: (i) => `Running ${ellipsize(String(i.command ?? ""), 60)}`,
    bash_background: (i) =>
      `Spawning ${ellipsize(String(i.command ?? ""), 60)}`,
    bash_logs: () => `Reading logs`,
    bash_list: () => `Listing background processes`,
    bash_kill: () => `Stopping background process`,
    suggest_command: (i) =>
      `Suggesting ${ellipsize(String(i.command ?? ""), 60)}`,
    todo_write: (i) =>
      `Updating plan (${Array.isArray(i.todos) ? i.todos.length : 0} items)`,
    screen_capture: () => "Capturing screen for local LM Studio",
    run_subagent: (i) => `Spawning ${String(i.type ?? "subagent")} subagent`,
  };

function shortPath(p: unknown): string {
  if (typeof p !== "string") return "";
  const i = p.lastIndexOf("/");
  return i === -1 ? p : p.slice(i + 1);
}

function ellipsize(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export type BuildModelOptions = {
  modelIdOverride?: string;
  lmstudioBaseURL?: string;
  mlxBaseURL?: string;
  ollamaBaseURL?: string;
  openaiCompatibleBaseURL?: string;
};

const modelCache = new Map<string, LanguageModel>();

export async function buildLanguageModel(
  provider: ProviderId,
  keys: ProviderKeys,
  resolvedModelId: string,
  options: BuildModelOptions = {},
  customEndpointKey?: string | null,
): Promise<LanguageModel> {
  if (providerNeedsKey(provider) && !keys[provider]) {
    throw new Error(
      `No API key configured for ${provider}. Open Settings → AI to add one.`,
    );
  }
  const key = keys[provider] ?? "";
  const lmstudioURL = options.lmstudioBaseURL ?? LMSTUDIO_DEFAULT_BASE_URL;
  const mlxURL = options.mlxBaseURL ?? MLX_DEFAULT_BASE_URL;
  const ollamaURL = options.ollamaBaseURL ?? OLLAMA_DEFAULT_BASE_URL;
  const compatURL = options.openaiCompatibleBaseURL ?? "";
  const epKey = customEndpointKey ?? "";
  const cacheKey = `${provider} ${key} ${epKey} ${resolvedModelId} ${lmstudioURL} ${mlxURL} ${ollamaURL} ${compatURL}`;
  const hit = modelCache.get(cacheKey);
  if (hit) return hit;

  let built: LanguageModel;
  switch (provider) {
    case "openai": {
      const { createOpenAI } = await import("@ai-sdk/openai");
      built = createOpenAI({ apiKey: key, fetch: providerFetch })(resolvedModelId);
      break;
    }
    case "anthropic": {
      const { createAnthropic } = await import("@ai-sdk/anthropic");
      built = createAnthropic({ apiKey: key, fetch: providerFetch })(resolvedModelId);
      break;
    }
    case "google": {
      const { createGoogle } = await import("@ai-sdk/google");
      const bridge = await getJarvis();
      if (!bridge.base || !bridge.token)
        throw new Error(
          "Open the development window from Jarvis to use the shared Gemini.",
        );
      built = createGoogle({
        apiKey: bridge.token || key,
        baseURL: bridge.base || undefined,
        fetch: bridge.base ? localProxyFetch : undefined,
      })(resolvedModelId);
      break;
    }
    case "xai": {
      const { createXai } = await import("@ai-sdk/xai");
      built = createXai({ apiKey: key, fetch: providerFetch })(resolvedModelId);
      break;
    }
    case "cerebras": {
      const { createCerebras } = await import("@ai-sdk/cerebras");
      built = createCerebras({ apiKey: key, fetch: providerFetch })(resolvedModelId);
      break;
    }
    case "deepseek": {
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "deepseek",
        baseURL: "https://api.deepseek.com",
        fetch: providerFetch,
        apiKey: key,
      })(resolvedModelId);
      break;
    }
    case "mistral": {
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "mistral",
        baseURL: "https://api.mistral.ai/v1",
        fetch: providerFetch,
        apiKey: key,
      })(resolvedModelId);
      break;
    }
    case "groq": {
      const { createGroq } = await import("@ai-sdk/groq");
      built = createGroq({ apiKey: key, fetch: providerFetch })(resolvedModelId);
      break;
    }
    case "openrouter": {
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "openrouter",
        baseURL: "https://openrouter.ai/api/v1",
        apiKey: key,
        fetch: providerFetch,
        headers: {
          "HTTP-Referer": "https://github.com/Personal-Stuffs-johnmart19/jarvis",
          "X-Title": "Jarvis",
        },
      })(resolvedModelId);
      break;
    }
    case "openai-compatible": {
      if (!compatURL) {
        throw new Error(
          "OpenAI-compatible provider has no base URL. Set it in Settings → Models.",
        );
      }
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "openai-compatible",
        includeUsage: ["localhost", "127.0.0.1", "[::1]"].includes(new URL(compatURL).hostname),
        baseURL: compatURL,
        apiKey: epKey || key || undefined,
        fetch: localProxyFetch,
      })(resolvedModelId);
      break;
    }
    case "lmstudio": {
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "lmstudio",
        includeUsage: true,
        baseURL: lmstudioURL,
        fetch: (input, init) => {
          if (typeof init?.body === "string") {
            return localProxyFetch(input, { ...init, body: normalizeLmstudioRequest(
              init.body, resolvedModelId, usePreferencesStore.getState().lmstudioReasoning,
            ) });
          }
          return localProxyFetch(input, init);
        },
      })(resolvedModelId);
      break;
    }
    case "mlx": {
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "mlx",
        includeUsage: true,
        baseURL: mlxURL,
        fetch: localProxyFetch,
      })(resolvedModelId);
      break;
    }
    case "ollama": {
      const { createOpenAICompatible } = await import(
        "@ai-sdk/openai-compatible"
      );
      built = createOpenAICompatible({
        name: "ollama",
        includeUsage: true,
        baseURL: ollamaURL,
        fetch: localProxyFetch,
      })(resolvedModelId);
      break;
    }
    default: {
      const _exhaustive: never = provider;
      throw new Error(`Unsupported provider: ${_exhaustive as ProviderId}`);
    }
  }
  modelCache.set(cacheKey, built);
  return built;
}

export type LocalProviderConfig = {
  lmstudioBaseURL?: string;
  lmstudioModelId?: string;
  mlxBaseURL?: string;
  mlxModelId?: string;
  ollamaBaseURL?: string;
  ollamaModelId?: string;
  openaiCompatibleBaseURL?: string;
  openaiCompatibleModelId?: string;
  openaiCompatibleContextLimit?: number;
  openrouterModelId?: string;
  customEndpoints?: readonly CustomEndpoint[];
  customEndpointKeys?: CustomEndpointKeys;
};

/** Shared by parent and child agents; loaded context takes priority over catalogue defaults. */
export async function configuredModelRuntime(modelId: string, local: LocalProviderConfig = {}, signal?: AbortSignal) {
  signal?.throwIfAborted();
  if (isManagedModelId(modelId)) { await discoverManagedModels(); signal?.throwIfAborted(); return { contextLimit: managedModelContext(modelId), supportsVision: false }; }
  if (modelId === "lmstudio-local" || modelId === "ollama-local") {
    const lmstudio = modelId === "lmstudio-local";
    return localServerModelMetadata(
      lmstudio ? "lmstudio" : "ollama",
      (lmstudio ? local.lmstudioBaseURL : local.ollamaBaseURL) ?? (lmstudio ? LMSTUDIO_DEFAULT_BASE_URL : OLLAMA_DEFAULT_BASE_URL),
      (lmstudio ? local.lmstudioModelId : local.ollamaModelId) ?? "",
      signal,
    ).catch(() => { signal?.throwIfAborted(); return { contextLimit: 4096, supportsVision: false }; });
  }
  const override = isCompatModelId(modelId)
    ? local.customEndpoints?.find((endpoint) => endpoint.id === endpointIdFromCompatModel(modelId))?.contextLimit
    : local.openaiCompatibleContextLimit;
  return { contextLimit: getModelContextLimit(modelId, override), supportsVision: false };
}

/** Compatibility for callers that only require the context allocation. */
export async function configuredModelContextLimit(modelId: string, local: LocalProviderConfig = {}, signal?: AbortSignal) {
  return (await configuredModelRuntime(modelId, local, signal)).contextLimit;
}

export async function buildConfiguredLanguageModel(
  modelId: string,
  keys: ProviderKeys,
  local: LocalProviderConfig = {},
  signal?: AbortSignal,
): Promise<LanguageModel> {
  signal?.throwIfAborted();
  if (isManagedModelId(modelId)) {
    const { createOpenAICompatible } = await import("@ai-sdk/openai-compatible");
    return createOpenAICompatible({ name: "jarvis-managed", baseURL: "http://127.0.0.1/v1", includeUsage: true,
      fetch: managedModelFetch(modelId, localProxyFetch, signal) })(modelId);
  }
  if (isCompatModelId(modelId)) {
    const eid = endpointIdFromCompatModel(modelId);
    const ep = local.customEndpoints?.find((e) => e.id === eid);
    if (!ep) throw new Error(`Custom endpoint not found: ${eid}`);
    if (!ep.modelId.trim()) {
      throw new Error(`${ep.name}: no model id set. Open Settings → Models.`);
    }
    if (ep.id === "jarvis-llama" && ep.baseURL === "http://127.0.0.1:18081/v1") {
      const state = await invoke<{running: boolean; model: string; projector: string; mode: string}>("backend", {method: "llama.status", params: {}});
      if (!state.running) await invoke("backend", {method: "llama.start", params: {model: state.model, projector: state.projector, mode: state.mode}});
    }
    return buildLanguageModel(
      "openai-compatible",
      keys,
      ep.modelId.trim(),
      { openaiCompatibleBaseURL: ep.baseURL },
      local.customEndpointKeys?.[eid],
    );
  }
  const m = resolveModel(modelId);
  let resolvedId: string = m.id;
  if (m.id === "lmstudio-local") {
    if (!local.lmstudioModelId?.trim()) {
      throw new Error(
        "LM Studio: no model id set. Open Settings → Models and enter the model id loaded in LM Studio.",
      );
    }
    resolvedId = local.lmstudioModelId.trim();
  } else if (m.id === "mlx-local") {
    if (!local.mlxModelId?.trim()) {
      throw new Error(
        "MLX: no model id set. Open Settings → Models and enter the model id served by mlx_lm.server.",
      );
    }
    resolvedId = local.mlxModelId.trim();
  } else if (m.id === "ollama-local") {
    if (!local.ollamaModelId?.trim()) {
      throw new Error(
        "Ollama: no model id set. Open Settings → Models and enter the model id (e.g. the name from `ollama list`).",
      );
    }
    resolvedId = local.ollamaModelId.trim();
  } else if (m.id === "openai-compatible-custom") {
    if (!local.openaiCompatibleModelId?.trim()) {
      throw new Error(
        "OpenAI-compatible: no model id set. Open Settings → Models.",
      );
    }
    resolvedId = local.openaiCompatibleModelId.trim();
  } else if (m.id === "openrouter-custom") {
    if (!local.openrouterModelId?.trim()) {
      throw new Error(
        "OpenRouter: no model id set. Open Settings → Models and enter an OpenRouter model id (e.g. anthropic/claude-sonnet-5).",
      );
    }
    resolvedId = local.openrouterModelId.trim();
  }
  return buildLanguageModel(m.provider, keys, resolvedId, {
    lmstudioBaseURL: local.lmstudioBaseURL,
    mlxBaseURL: local.mlxBaseURL,
    ollamaBaseURL: local.ollamaBaseURL,
    openaiCompatibleBaseURL: local.openaiCompatibleBaseURL,
  });
}

export type AgentUsage = {
  inputTokens: number;
  outputTokens: number;
  cachedInputTokens: number;
};

export type AgentUsageDelta = AgentUsage & {
  lastInputTokens: number;
  lastCachedTokens: number;
};

export type RunAgentOptions = {
  keys: ProviderKeys;
  modelId?: string;
  customInstructions?: string;
  agentPersona?: { name: string; instructions: string } | null;
  toolContext: ToolContext;
  tools?: ToolSet;
  onStep?: (step: string | null) => void;
  onUsage?: (delta: AgentUsageDelta) => void;
  onCompact?: (info: { droppedCount: number }) => void;
  onFinishMeta?: (info: { hitStepCap: boolean; finishReason: string }) => void;
  lmstudioBaseURL?: string;
  lmstudioModelId?: string;
  mlxBaseURL?: string;
  mlxModelId?: string;
  ollamaBaseURL?: string;
  ollamaModelId?: string;
  openaiCompatibleBaseURL?: string;
  openaiCompatibleModelId?: string;
  openaiCompatibleContextLimit?: number;
  openrouterModelId?: string;
  customEndpoints?: readonly CustomEndpoint[];
  customEndpointKeys?: CustomEndpointKeys;
  planMode?: boolean;
  projectMemory?: string | null;
  uiMessages: UIMessage[];
  abortSignal?: AbortSignal;
};

export async function runAgentStream(opts: RunAgentOptions) {
  const modelId = opts.modelId ?? DEFAULT_MODEL_ID;
  const endpoints = opts.customEndpoints ?? [];
  const info = resolveModel(modelId, endpoints);
  const provider = info.provider;
  const captureBase = localCaptureBase(provider, opts.lmstudioBaseURL);
  const model = await buildConfiguredLanguageModel(modelId, opts.keys, {
    lmstudioBaseURL: captureBase ?? opts.lmstudioBaseURL,
    lmstudioModelId: opts.lmstudioModelId,
    mlxBaseURL: opts.mlxBaseURL,
    mlxModelId: opts.mlxModelId,
    ollamaBaseURL: opts.ollamaBaseURL,
    ollamaModelId: opts.ollamaModelId,
    openaiCompatibleBaseURL: opts.openaiCompatibleBaseURL,
    openaiCompatibleModelId: opts.openaiCompatibleModelId,
    openrouterModelId: opts.openrouterModelId,
    customEndpoints: opts.customEndpoints,
    customEndpointKeys: opts.customEndpointKeys,
  }, opts.abortSignal);
  const { contextLimit, supportsVision } = await configuredModelRuntime(modelId, opts, opts.abortSignal);
  const captureAllowed = captureBase !== null && supportsVision;
  const baseTools = opts.tools ?? buildTools(opts.toolContext);
  const tools: ToolSet = budgetBrowserObservations(baseTools, contextLimit);
  delete tools.screen_capture;
  const captureSession = opts.toolContext.getSessionId() ?? crypto.randomUUID();
  const captureTools = buildScreenTools(captureSession, captureAllowed);
  if (captureAllowed) Object.assign(tools, captureTools);
  const history = restrictScreenHistory(await convertToModelMessages(projectInterruptedHistory(opts.uiMessages), {tools: {...tools, ...captureTools}}), captureAllowed);
  const keepsReasoning = modelKeepsReasoning(info);
  const prunedHistory = pruneMessages({
    messages: history,
    reasoning: keepsReasoning ? "none" : "before-last-message",
    emptyMessages: "remove",
  });

  const budget = await prepareAgentBudget({ modelId, contextLimit, tools, owner: baseTools,
    persona: opts.agentPersona, customInstructions: opts.customInstructions,
    projectMemory: opts.projectMemory, planMode: opts.planMode });
  const { system: stableSystem, plan, activeTools, reservedTokens } = budget;
  if (budget.fixedInstructionsFull) {
    throw Object.assign(new Error(t(FIXED_INSTRUCTIONS_FULL_MESSAGE)), { code: "context_budget_exceeded" });
  }
  const prompt = prepareAgentPrompt(stableSystem, plan, prunedHistory, provider);
  let compactReported = false;

  const readFailureGuard = createReadFailureGuard();
  let stepsSeen = 0;
  return streamText({
    model,
    instructions: prompt.system,
    messages: prompt.messages,
    allowSystemInMessages: false,
    maxOutputTokens: replyTokenBudget(contextLimit),
    // Flash defaults to medium thinking; low keeps interactive chat responsive.
    // Leave Flash-Lite's faster provider default and other providers unchanged.
    providerOptions: provider === "google" && ["gemini-3.8-flash", "gemini-3.6-flash"].includes(info.id)
      ? { google: { thinkingConfig: { thinkingLevel: "low" } } } : undefined,
    maxRetries: 0,
    timeout: {
      // The shared broker allows one 60-second upstream attempt. Headers and
      // SSE heartbeats are transport progress, not an assistant response.
      firstChunkMs: 65000,
      chunkMs: 30000,
      tools: { jarvis_catalogMs: 15000, mcp_catalogMs: 15000, utility_catalogMs: 15000 },
    },
    tools,
    activeTools,
    prepareStep: ({ messages }) => {
      const compact = compactModelMessagesDetailed(messages, contextLimit, {
        reservedTokens,
      });
      assertContextBudget(compact, t(CONTEXT_FULL_MESSAGE));
      if (compact.compacted && !compactReported) {
        compactReported = true;
        opts.onCompact?.({ droppedCount: compact.droppedCount });
      }
      const nextPrompt = prepareAgentPrompt(stableSystem, plan, restrictScreenHistory(compact.messages, captureAllowed), provider);
      if (readFailureGuard.limited || stepsSeen >= MAX_AGENT_STEPS - 1) {
        const reason = readFailureGuard.limited ? READ_FAILURE_MESSAGE : "This is the final response step. Answer the user using verified results already obtained. Explain any incomplete work or missing information honestly; do not call more tools.";
        const finalPrompt = prepareAgentPrompt(`${stableSystem}\n\n${reason}`, plan, restrictScreenHistory(compact.messages, captureAllowed), provider);
        return { instructions: finalPrompt.system, messages: finalPrompt.messages, toolChoice: "none", activeTools: [] };
      }
      return {instructions: nextPrompt.system, messages: nextPrompt.messages};
    },
    stopWhen: [isStepCount(MAX_AGENT_STEPS), stopOnUnavailableToolCall, stopOnRepeatedInvalidToolCall],
    abortSignal: opts.abortSignal,
    onStepEnd: (step) => {
      readFailureGuard.observe(step.content);
      stepsSeen++;
      if (opts.onStep) {
        const last = step.toolCalls?.[step.toolCalls.length - 1];
        if (last) {
          const label = TOOL_LABELS[last.toolName];
          opts.onStep(
            label
              ? label((last.input ?? {}) as Record<string, unknown>)
              : `Calling ${last.toolName}`,
          );
        } else if (step.text) {
          opts.onStep("Writing");
        }
      }
      if (opts.onUsage && step.usage) {
        const u = step.usage;
        const stepInput = u.inputTokens ?? 0;
        const stepCached = u.inputTokenDetails?.cacheReadTokens ?? 0;
        opts.onUsage({
          inputTokens: stepInput,
          outputTokens: u.outputTokens ?? 0,
          cachedInputTokens: stepCached,
          lastInputTokens: stepInput,
          lastCachedTokens: stepCached,
        });
      }
    },
    onEnd: (result) => {
      opts.onStep?.(null);
      const finishReason =
        (result as { finishReason?: string } | undefined)?.finishReason ?? "";
      opts.onFinishMeta?.({
        hitStepCap: stepsSeen >= MAX_AGENT_STEPS,
        finishReason,
      });
    },
  });
}
