import type { OwnedError } from "../../../src/displayError";

export type StartupState = { enabled: boolean | null; busy: boolean; error: OwnedError | null };
type StartupIO = { read: () => Promise<boolean>; change: (enabled: boolean) => Promise<void>; persist: (enabled: boolean) => Promise<void> };

/** Serialize native changes and retain confirmed OS state if preference synchronization fails. */
export function createStartupController(io: StartupIO, publish: (state: StartupState) => void) {
  let state: StartupState = { enabled: null, busy: false, error: null };
  const update = (patch: Partial<StartupState>) => { state = { ...state, ...patch }; publish(state); };
  async function run(next?: boolean) {
    if (state.busy || (next !== undefined && state.enabled === null)) return;
    let message = next === undefined ? "Could not read the startup setting: {0}" : "Could not change the startup setting: {0}";
    update({ busy: true, error: null });
    try {
      let enabled: boolean;
      if (next === undefined) enabled = await io.read();
      else { await io.change(next); enabled = next; }
      update({ enabled });
      message = "Could not synchronize the startup preference: {0}";
      await io.persist(enabled);
    } catch (error) {
      update({ error: { source: "jarvis", message, values: [String(error)] } });
    } finally {
      update({ busy: false });
    }
  }
  return { refresh: () => run(), setEnabled: (enabled: boolean) => run(enabled) };
}
/** One host-owned service survives settings iframe destruction and recreation. */
export function createStartupService(io: StartupIO) {
  let snapshot: StartupState = { enabled: null, busy: false, error: null };
  const listeners = new Set<() => void>();
  const controller = createStartupController(io, state => {
    snapshot = state;
    for (const listener of listeners) listener();
  });
  return {
    ...controller,
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}
export type StartupService = ReturnType<typeof createStartupService>;
