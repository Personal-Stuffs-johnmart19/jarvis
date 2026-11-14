const readers = new WeakMap<object, string[]>();
/** Trusted execution context only; never part of a model-visible input schema. */
export const QUEUED_READ_CONTEXT = Symbol("queued read consumer");
/** Explicit canonical-handler opt-in; lack of approval alone does not mean read-only. */
export function queuedReadOnly<T extends { needsApproval?: unknown }>(action: T, pathField: string): T {
  if (action.needsApproval) throw new Error("Queued reads cannot require approval");
  readers.set(action, [pathField]);
  return action;
}
export const isQueuedReadOnly = (action: object) => readers.has(action);
export const queuedReadPaths = (action: object) => readers.get(action);
