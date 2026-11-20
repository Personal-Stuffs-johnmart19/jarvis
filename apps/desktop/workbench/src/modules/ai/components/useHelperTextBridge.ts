import { useEffect, useMemo } from "react";
import { generateHelperText } from "../lib/helperTextGeneration";
import { invoke } from "@/host/core";
import { listen } from "@/host/event";
import { usePreferencesStore } from "@/modules/settings/preferences";
import { useChatStore } from "../store/chatStore";
import { buildConfiguredLanguageModel, configuredModelContextLimit } from "../lib/agent";
import { estimateTokens } from "../lib/compact";
import { HelperTextRunner, helperTextWireReply, matchesHelperTextRoute, parseHelperTextRequest } from "../lib/helperText";
import { t } from "../../../../../src/localization";

/** Keys and endpoint configuration stay here; only the opaque route crosses IPC. */
export function useHelperTextBridge(owner: string, session: string, _status?: string) {
  const selectedModelId = useChatStore((s) => s.selectedModelId);
  const apiKeys = useChatStore((s) => s.apiKeys);
  const customEndpointKeys = useChatStore((s) => s.customEndpointKeys);
  const lmstudioBaseURL = usePreferencesStore((s) => s.lmstudioBaseURL);
  const lmstudioModelId = usePreferencesStore((s) => s.lmstudioModelId);
  const mlxBaseURL = usePreferencesStore((s) => s.mlxBaseURL);
  const mlxModelId = usePreferencesStore((s) => s.mlxModelId);
  const ollamaBaseURL = usePreferencesStore((s) => s.ollamaBaseURL);
  const ollamaModelId = usePreferencesStore((s) => s.ollamaModelId);
  const openaiCompatibleBaseURL = usePreferencesStore((s) => s.openaiCompatibleBaseURL);
  const openaiCompatibleModelId = usePreferencesStore((s) => s.openaiCompatibleModelId);
  const openaiCompatibleContextLimit = usePreferencesStore((s) => s.openaiCompatibleContextLimit);
  const openrouterModelId = usePreferencesStore((s) => s.openrouterModelId);
  const customEndpoints = usePreferencesStore((s) => s.customEndpoints);
  const lmstudioReasoning = usePreferencesStore((s) => s.lmstudioReasoning);
  const snapshot = useMemo(() => ({
    modelId: selectedModelId, keys: apiKeys,
    local: {
      lmstudioBaseURL, lmstudioModelId,
      mlxBaseURL, mlxModelId,
      ollamaBaseURL, ollamaModelId,
      openaiCompatibleBaseURL, openaiCompatibleModelId,
      openaiCompatibleContextLimit, openrouterModelId,
      customEndpoints, customEndpointKeys,
    },
    reasoning: lmstudioReasoning,
  }), [selectedModelId, apiKeys, customEndpointKeys, lmstudioBaseURL, lmstudioModelId,
    mlxBaseURL, mlxModelId, ollamaBaseURL, ollamaModelId, openaiCompatibleBaseURL,
    openaiCompatibleModelId, openaiCompatibleContextLimit, openrouterModelId, customEndpoints, lmstudioReasoning]);
  const route = useMemo(() => crypto.randomUUID(), [snapshot]);
  const runner = useMemo(() => new HelperTextRunner({
    context: signal => configuredModelContextLimit(snapshot.modelId, snapshot.local, signal),
    model: signal => buildConfiguredLanguageModel(snapshot.modelId, snapshot.keys, snapshot.local, signal),
    generate: generateHelperText,
    estimate: estimateTokens,
    usage: usage => useChatStore.getState().recordUsage(session, {
      inputTokens: usage.inputTokens ?? 0, outputTokens: usage.outputTokens ?? 0,
      cachedInputTokens: usage.inputTokenDetails?.cacheReadTokens ?? 0,
      lastInputTokens: usage.inputTokens ?? 0, lastCachedTokens: usage.inputTokenDetails?.cacheReadTokens ?? 0,
    }),
  }), [snapshot, session]);
  useEffect(() => {
    let disposed = false;
    let unsubscribe: (() => void) | undefined;
    void listen<{ event: string; data: unknown }>("backend-event", ({ payload }) => {
      if (disposed) return;
      if (payload.event === "helper.text.cancel") {
        const data = payload.data as { id?: unknown };
        if (typeof data?.id === "string") runner.cancel(data.id);
        return;
      }
      if (payload.event !== "helper.text.request") return;
      let request;
      try { request = parseHelperTextRequest(payload.data); } catch {
        const data = payload.data as Record<string, unknown> | null;
        if (data && typeof data.id === "string" && /^[a-zA-Z0-9_-]{1,128}$/.test(data.id) && data.owner === owner && data.session === session && data.route === route)
          void invoke("backend", { method: "helper.text.complete", params: { id: data.id, owner, session, route, text: "", error: t("Invalid helper text request") } }).catch(() => {});
        return;
      }
      if (!matchesHelperTextRoute(request, owner, session, route)) return;
      void runner.run(request).then(reply => invoke("backend", { method: "helper.text.complete", params: helperTextWireReply(reply, t) })).catch(() => {});
    }).then(off => { if (disposed) off(); else unsubscribe = off; }).catch(() => {});
    return () => { disposed = true; unsubscribe?.(); runner.cancel(); };
  }, [owner, session, route, runner]);
  return route;
}
