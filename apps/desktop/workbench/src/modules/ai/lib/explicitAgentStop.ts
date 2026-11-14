/** Explicit user cancellation only. Ordinary chat completion must not call this. */
const localStops = new Set<()=>void>();
export function createExplicitStopRegistry(allowed: (owner: Window) => boolean) {
  const entries = new Set<{ owner: Window; stop: () => void; dispose: () => void }>();
  return {
    register(owner: Window, stop: () => void) {
      if (!allowed(owner)) return () => {};
      const entry = { owner, stop, dispose: () => {} };
      const dispose = () => {
        entries.delete(entry);
        try { owner.removeEventListener("pagehide", dispose); } catch { /* A navigated owner is already inactive. */ }
      };
      entry.dispose = dispose;
      entries.add(entry);
      owner.addEventListener("pagehide", dispose, { once: true });
      return dispose;
    },
    notify(owner: Window) {
      if (!allowed(owner)) return;
      for (const entry of [...entries]) {
        if (!allowed(entry.owner)) { entry.dispose(); continue; }
        try { entry.stop(); } catch { /* Continue stopping independent local owners. */ }
      }
    },
  };
}
type StopRegistry = ReturnType<typeof createExplicitStopRegistry>;
function sharedStops(): StopRegistry | undefined {
  if (typeof window === "undefined") return;
  try {
    const host = window.parent === window ? window : window.parent;
    if (host.location.origin !== window.location.origin) return;
    return (host as unknown as { __JARVIS_NATIVE__?: { explicitStops?: StopRegistry } }).__JARVIS_NATIVE__?.explicitStops;
  } catch { return; }
}
export function subscribeExplicitAgentStop(stop:()=>void):()=>void {
  const shared = sharedStops();
  if (shared) return shared.register(window, stop);
  localStops.add(stop);
  return ()=>{localStops.delete(stop);};
}
export async function explicitAgentStop(
  stopChat: () => void | PromiseLike<void>,
  stopBackend: () => PromiseLike<unknown>,
): Promise<void> {
  const shared = sharedStops();
  if (shared) shared.notify(window);
  else for(const stop of [...localStops])try{stop();}catch{/* Continue stopping independent local owners. */}
  // A blocked or failed backend must not prevent the local SDK abort, or vice versa.
  await Promise.allSettled([
    Promise.resolve().then(stopBackend),
    Promise.resolve().then(stopChat),
  ]);
}
