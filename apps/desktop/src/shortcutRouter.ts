// The workspace registry owns bindings; the host only routes trusted documents.
export function createShortcutRouter(allowed: (owner: Window) => boolean) {
  const handlers = new Set<{ owner: Window; handle: (event: KeyboardEvent) => void }>();
  return {
    register(owner: Window, handle: (event: KeyboardEvent) => void) {
      if (!allowed(owner)) throw new Error("Untrusted shortcut document");
      const entry = { owner, handle };
      handlers.add(entry);
      const dispose = () => { handlers.delete(entry); owner.removeEventListener("pagehide", dispose); };
      owner.addEventListener("pagehide", dispose, { once: true });
      return dispose;
    },
    dispatch(event: KeyboardEvent, source: Window) {
      if (!allowed(source) || event.defaultPrevented || event.isComposing) return;
      for (const entry of handlers) {
        // The originating document already handled its capture listeners.
        if (entry.owner === source || !allowed(entry.owner)) continue;
        entry.handle(event);
        if (event.defaultPrevented) break;
      }
    },
  };
}
