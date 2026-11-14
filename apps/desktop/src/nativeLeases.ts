// Native callbacks live in the host. Release leases when an embedded document closes.
const owners = new WeakMap<
  Document,
  { closed: boolean; dispose: Set<() => unknown> }
>();
function ownerState(owner: Window) {
  const document = owner.document;
  let state = owners.get(document);
  if (!state) {
    state = { closed: false, dispose: new Set() };
    owners.set(document, state);
    const current = state;
    const close = () => {
      if (current.closed) return;
      current.closed = true;
      for (const dispose of current.dispose)
        void Promise.resolve()
          .then(dispose)
          .catch(() => {});
      current.dispose.clear();
    };
    owner.addEventListener("pagehide", close, { once: true });
    owner.addEventListener("unload", close, { once: true });
  }
  return state;
}
export function lease<T>(owner: Window, value: T): T {
  if (!(value instanceof Promise)) return value;
  const state = ownerState(owner);
  return value.then((dispose) => {
    if (typeof dispose !== "function") return dispose;
    let active = true;
    const release = () => {
      if (!active) return;
      active = false;
      state.dispose.delete(release);
      return dispose();
    };
    if (state.closed)
      void Promise.resolve()
        .then(release)
        .catch(() => {});
    else state.dispose.add(release);
    return release;
  }) as T;
}
export function owned<T extends object>(owner: Window, value: T, release = false): T {
  if (release)
    ownerState(owner).dispose.add(async () => {
      const resource = value as {
        save?: () => Promise<void>;
        close?: () => Promise<void>;
      };
      if (resource.save) await resource.save();
      if (resource.close) await resource.close();
    });
  return new Proxy(value, {
    get(target, key) {
      const entry = Reflect.get(target, key, target);
      return typeof entry === "function"
        ? (...args: unknown[]) =>
            lease(owner, Reflect.apply(entry, target, args))
        : entry;
    },
  });
}
