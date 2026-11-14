import { invoke } from "@/host/core";

import { isAvailableManagedModel, type ManagedModel } from "../config";
export { isManagedModelId, type ManagedModel } from "../config";
let discovery: Promise<ManagedModel[]> | undefined;
let discoveryKey = "";
const metadata = new Map<string, ManagedModel>();
export function invalidateManagedModelDiscovery() {
  discovery = undefined;
  metadata.clear();
}
export function discoverManagedModels(freshness = ""): Promise<ManagedModel[]> {
  if (!discovery || discoveryKey !== freshness) {
    discoveryKey = freshness;
    metadata.clear();
    const request = invoke<{ models: ManagedModel[] }>("backend", { method: "managed_chat.list", params: {} })
      .then(({ models }) => {
        const valid = Array.isArray(models) ? models.filter(isAvailableManagedModel) : [];
        // A request started before a lifecycle change must not restore stale context.
        if (discovery === request) {
          metadata.clear();
          for (const model of valid) metadata.set(model.model_id, model);
        }
        return valid;
      });
    discovery = request;
    void request.then(() => { if (discovery === request) discovery = undefined; }, () => { if (discovery === request) discovery = undefined; });
  }
  return discovery;
}
export function managedModelContext(id: string): number {
  const context = metadata.get(id)?.context;
  return typeof context === "number" && Number.isFinite(context) && context > 0 ? context : 8192;
}
export function unloadManagedModel(modelId: string) {
  return invoke("backend", { method: "managed_chat.unload", params: { model_id: modelId } });
}

/** Resolve each inference request independently; never cache a mutable server endpoint. */
export function managedModelFetch(modelId: string, fetcher: typeof fetch, parentSignal?: AbortSignal): typeof fetch {
  return async (input, init) => {
    const requestId = crypto.randomUUID();
    const requestSignal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
    const signals = [parentSignal, requestSignal].filter((signal): signal is AbortSignal => Boolean(signal));
    const signal = signals.length ? AbortSignal.any(signals) : undefined;
    signal?.throwIfAborted();
    let cancelled = false;
    const cancel = () => { if (cancelled) return; cancelled = true; void invoke("backend", { method: "managed_chat.cancel", params: { request_id: requestId } }).catch(() => {}); };
    signal?.addEventListener("abort", cancel, { once: true });
    const cleanup = () => signal?.removeEventListener("abort", cancel);
    try {
      const resolved = await invoke<ManagedModel & { base_url: string }>("backend", {
        method: "managed_chat.resolve", params: { model_id: modelId, request_id: requestId },
      });
      signal?.throwIfAborted();
      if (resolved.model_id !== modelId) throw new Error("Managed model identity changed during request resolution");
      const base = new URL(resolved.base_url);
      if (base.protocol !== "http:" || base.hostname !== "127.0.0.1" || base.username || base.password) throw new Error("Invalid managed runtime endpoint");
      metadata.set(modelId, resolved);
      const original = new URL(input instanceof Request ? input.url : String(input));
      const target = new URL(base.toString().replace(/\/$/, "") + original.pathname.replace(/^\/v1/, "") + original.search);
      const response = await fetcher(input instanceof Request ? new Request(target, input) : target, { ...init, signal });
      if (!response.body) { cleanup(); return response; }
      const reader = response.body.getReader();
      return new Response(new ReadableStream({
        async pull(controller) {
          try {
            const result = await reader.read();
            if (result.done) { cleanup(); controller.close(); }
            else controller.enqueue(result.value);
          } catch (error) { cleanup(); controller.error(error); }
        },
        async cancel(reason) { cancel(); cleanup(); await reader.cancel(reason); },
      }), { status: response.status, statusText: response.statusText, headers: response.headers });
    } catch (error) { cleanup(); cancel(); throw error; }
  };
}
