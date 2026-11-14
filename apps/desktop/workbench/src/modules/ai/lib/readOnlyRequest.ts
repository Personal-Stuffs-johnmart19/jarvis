/** Cancel waiting for metadata without pretending a mutating operation stopped. */
export async function readOnlyRequest<T>(read: () => Promise<T>, signal?: AbortSignal): Promise<T> {
  signal?.throwIfAborted();
  if (!signal) return read();
  let onAbort: () => void = () => {};
  const cancelled = new Promise<never>((_, reject) => {
    onAbort = () => reject(signal.reason ?? new DOMException("Lookup cancelled", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
    if (signal.aborted) onAbort();
  });
  try { return await Promise.race([read(), cancelled]); }
  finally { signal.removeEventListener("abort", onAbort); }
}
