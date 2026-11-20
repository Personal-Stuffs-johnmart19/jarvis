import { useEffect, useRef, useState } from "react";
import { invoke } from "@/host/core";
import { listen } from "@/host/event";
import { lmCatalog, ollamaCatalog, type LocalModel } from "../lib/lmstudio";
import { discoverManagedModels, invalidateManagedModelDiscovery, type ManagedModel } from "../lib/managedModels";

let lifecycleRevision = 0;
const lifecycleSubscribers = new Set<(revision: number) => void>();
let lifecycleSubscription: Promise<void> | undefined;
let stopLifecycle: (() => void) | undefined;
let refreshQueued = false;

function invalidateCatalogs() {
  pending.clear();
  pendingOllama.clear();
  invalidateManagedModelDiscovery();
  lifecycleRevision++;
  for (const notify of lifecycleSubscribers) notify(lifecycleRevision);
}

function requestCatalogRefresh() {
  // A picker refresh reaches all three catalogue hooks; publish one generation.
  if (refreshQueued) return;
  refreshQueued = true;
  queueMicrotask(() => { refreshQueued = false; invalidateCatalogs(); });
}

function subscribeLifecycle(callback: (revision: number) => void) {
  lifecycleSubscribers.add(callback);
  callback(lifecycleRevision);
  if (!lifecycleSubscription) {
    lifecycleSubscription = listen("jarvis://model-lifecycle", invalidateCatalogs).then(unlisten => {
      if (lifecycleSubscribers.size) stopLifecycle = unlisten;
      else { unlisten(); lifecycleSubscription = undefined; }
    }).catch(() => { lifecycleSubscription = undefined; });
  }
  return () => {
    lifecycleSubscribers.delete(callback);
    if (!lifecycleSubscribers.size && stopLifecycle) {
      stopLifecycle(); stopLifecycle = undefined; lifecycleSubscription = undefined;
    }
  };
}

export function useModelLifecycleRevision(refresh = 0) {
  const [revision, setRevision] = useState(lifecycleRevision);
  const previousRefresh = useRef(refresh);
  useEffect(() => subscribeLifecycle(setRevision), []);
  useEffect(() => {
    if (previousRefresh.current !== refresh) {
      previousRefresh.current = refresh;
      requestCatalogRefresh();
    }
  }, [refresh]);
  return revision;
}

type Catalog = { models: LocalModel[]; ids: string[] };
const empty: Catalog = { models: [], ids: [] };
const pending = new Map<string, Promise<Catalog>>();

/** Metadata only; concurrent selectors share discovery without loading weights. */
export function discoverLocalModels(baseURL: string, freshness = ""): Promise<Catalog> {
  const url = baseURL.trim();
  if (!url) return Promise.resolve(empty);
  const key = JSON.stringify([url, freshness]);
  const existing = pending.get(key);
  if (existing) return existing;
  const request = Promise.all([
    lmCatalog(url).catch(() => []),
    invoke<string[]>("lm_models", { baseUrl: url }).catch(() => []),
  ]).then(([models, ids]) => ({ models, ids }));
  pending.set(key, request);
  void request.finally(() => { if (pending.get(key) === request) pending.delete(key); });
  return request;
}

export function useLocalModelCatalog(baseURL: string, refresh = 0) {
  const url = baseURL.trim();
  const revision = useModelLifecycleRevision(refresh);
  const freshness = String(revision);
  const [result, setResult] = useState<{ url: string; catalog: Catalog }>();
  useEffect(() => {
    let active = true;
    if (!url) return;
    void discoverLocalModels(url, freshness).then((catalog) => {
      if (active) setResult({ url, catalog });
    });
    return () => { active = false; };
  }, [url, freshness]);
  return url && result?.url === url ? result.catalog : empty;
}

const emptyOllama: Awaited<ReturnType<typeof ollamaCatalog>> = [];
const pendingOllama = new Map<string, ReturnType<typeof ollamaCatalog>>();
export function useOllamaModelCatalog(baseURL: string, refresh = 0) {
  const url = baseURL.trim();
  const revision = useModelLifecycleRevision(refresh);
  const freshness = String(revision);
  const key = JSON.stringify([url, freshness]);
  const [result, setResult] = useState<{ url: string; models: typeof emptyOllama }>();
  useEffect(() => {
    let active = true;
    if (!url) return;
    let request = pendingOllama.get(key);
    if (!request) {
      request = ollamaCatalog(url).catch(() => []);
      pendingOllama.set(key, request);
      const current = request;
      void current.finally(() => { if (pendingOllama.get(key) === current) pendingOllama.delete(key); });
    }
    void request.then(models => { if (active) setResult({ url, models }); });
    return () => { active = false; };
  }, [url, key]);
  return url && result?.url === url ? result.models : emptyOllama;
}

export { discoverManagedModels } from "../lib/managedModels";
const emptyManaged: ManagedModel[] = [];
export function useManagedModelCatalog(refresh = 0) {
  const revision = useModelLifecycleRevision(refresh);
  const key = String(revision);
  const [models, setModels] = useState<ManagedModel[]>(emptyManaged);
  useEffect(() => {
    let active = true;
    void discoverManagedModels(key).then(next => { if (active) setModels(next); }).catch(() => { if (active) setModels(emptyManaged); });
    return () => { active = false; };
  }, [key]);
  return models;
}
