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
  const state = useChatStore();
  const prefs = usePreferencesStore();
  const snapshot = useMemo(() => ({
    modelId: state.selectedModelId, keys: state.apiKeys,
    local: {
      lmstudioBaseURL: prefs.lmstudioBaseURL, lmstudioModelId: prefs.lmstudioModelId,
      mlxBaseURL: prefs.mlxBaseURL, mlxModelId: prefs.mlxModelId,
      ollamaBaseURL: prefs.ollamaBaseURL, ollamaModelId: prefs.ollamaModelId,
      openaiCompatibleBaseURL: prefs.openaiCompatibleBaseURL, openaiCompatibleModelId: prefs.openaiCompatibleModelId,
      openaiCompatibleContextLimit: prefs.openaiCompatibleContextLimit, openrouterModelId: prefs.openrouterModelId,
      customEndpoints: prefs.customEndpoints, customEndpointKeys: state.customEndpointKeys,
    },
    reasoning: prefs.lmstudioReasoning,
  }), [state.selectedModelId, state.apiKeys, state.customEndpointKeys, prefs.lmstudioBaseURL, prefs.lmstudioModelId,
    prefs.mlxBaseURL, prefs.mlxModelId, prefs.ollamaBaseURL, prefs.ollamaModelId, prefs.openaiCompatibleBaseURL,
    prefs.openaiCompatibleModelId, prefs.openaiCompatibleContextLimit, prefs.openrouterModelId, prefs.customEndpoints, prefs.lmstudioReasoning]);
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
