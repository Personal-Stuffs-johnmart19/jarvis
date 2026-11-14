/** Report the actual loaded context; an unloaded model has no allocated GPU context. */
export function localContextLimit(model?: {
  max_context_length?: number;
  loaded_instances: { config: { context_length: number } }[];
}) {
  return (
    (model?.loaded_instances.length ? Math.min(...model.loaded_instances.map(instance => instance.config.context_length)) : undefined) ??
    Math.min(4096, model?.max_context_length ?? 4096)
  );
}


type LoadedContextModel = {
  type?: string;
  capabilities?: { vision?: boolean };
  key: string;
  max_context_length?: number;
  loaded_instances: { id: string; config: { context_length: number } }[];
};

/** Inference IDs identify loaded instances; catalogue keys identify their model.
 * Never infer identity from a basename: different publishers can use the same name.
 */
export function lmstudioModelMetadata(models: readonly LoadedContextModel[], modelId: string) {
  const unknown = { contextLimit: 4096, supportsVision: false };
  const id = modelId.trim();
  if (!id) return unknown;
  const instances = models.flatMap(model => model.loaded_instances
    .filter(instance => instance.id === id)
    .map(instance => ({ model, instance })));
  const canonical = models.filter(model => model.key === id);
  // Conflicting catalogue/instance identities cannot establish which model is used.
  if (instances.length) {
    const owners = new Set(instances.map(match => match.model));
    if (owners.size !== 1 || canonical.some(model => !owners.has(model))) return unknown;
    const model = instances[0].model;
    return {
      contextLimit: Math.min(...instances.map(match => match.instance.config.context_length)),
      supportsVision: model.type === "llm" && model.capabilities?.vision === true,
    };
  }
  if (canonical.length !== 1) return unknown;
  const model = canonical[0];
  return {
    contextLimit: localContextLimit(model),
    supportsVision: model.type === "llm" && model.capabilities?.vision === true,
  };
}

export function lmstudioContextLimit(models: readonly LoadedContextModel[], modelId: string) {
  return lmstudioModelMetadata(models, modelId).contextLimit;
}

/** Running-state absence is not evidence of a large available context. */
export function ollamaContextLimit(model?: { loaded: boolean | null; context_length: number | null }) {
  return model?.loaded === true ? (model.context_length ?? 4096) : 4096;
}

/** Bounded metadata body; cancellation is owned by the supplied native-backed fetch. */
export async function localMetadata(url: string, fetcher: typeof fetch, signal?: AbortSignal): Promise<unknown> {
  signal?.throwIfAborted();
  const response = await fetcher(url, { signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(5000)]) : AbortSignal.timeout(5000) });
  if (!response.ok) { void response.body?.cancel().catch(() => {}); throw new Error(`Model server returned HTTP ${response.status}`); }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Empty model catalogue response");
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let bytes = 0, text = "";
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      bytes += part.value.byteLength;
      if (bytes > 1024 * 1024) throw new Error("Model catalogue exceeds 1 MiB");
      text += decoder.decode(part.value, { stream: true });
    }
    text += decoder.decode();
    return JSON.parse(text);
  } catch (error) {
    void reader.cancel().catch(() => {});
    throw error;
  } finally { reader.releaseLock(); }
}
