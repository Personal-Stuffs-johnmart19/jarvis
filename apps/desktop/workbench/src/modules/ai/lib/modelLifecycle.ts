/** Native API contracts: lmstudio.ai/docs/developer/rest/unload and docs.ollama.com/api/generate. */
export async function unloadLocalModel(
  provider: "lmstudio" | "ollama", baseUrl: string, model: string,
  instances: string[], fetcher: typeof fetch, signal?: AbortSignal, confirm?: () => Promise<boolean>,
): Promise<void> {
  const base = new URL(baseUrl.trim().replace(/\/+$/, "").replace(/\/v1$/, ""));
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash)
    throw new Error("Invalid model server URL");
  const valid = (value: string) => value.length > 0 && value.length <= 512 && !/[\x00-\x1f\x7f]/.test(value);
  if (!valid(model) || instances.length > 32 || instances.some(value => !valid(value)))
    throw new Error("Invalid model identity");
  signal?.throwIfAborted();
  if (confirm && !await confirm()) return;
  signal?.throwIfAborted();
  const ids = provider === "lmstudio" ? [...new Set(instances)] : [model];
  for (const id of ids) {
    const timeout = AbortSignal.timeout(30000);
    const response = await fetcher(`${base.href.replace(/\/$/, "")}/api/${provider === "lmstudio" ? "v1/models/unload" : "generate"}`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(provider === "lmstudio" ? {instance_id:id} : {model, prompt:"", keep_alive:0, stream:false}),
      signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`Model unload returned HTTP ${response.status}`);
    }
    // These APIs have small acknowledgements; drain within a strict bound.
    const reader = response.body?.getReader();
    if (!reader) continue;
    let bytes = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > 65536) throw new Error("Model unload response exceeds 64 KiB");
      }
    } catch (error) {
      await reader.cancel().catch(() => undefined);
      throw error;
    } finally { reader.releaseLock(); }
  }
}
