/** Serialize native updates so fast slider changes cannot finish out of order. */
export function createZoomController(apply: (value: number) => Promise<void>) {
  let pending = Promise.resolve();
  return (value: unknown): Promise<void> => {
    const scale = typeof value === "number" && Number.isFinite(value)
      ? Math.max(0.5, Math.min(2, value)) : 1;
    pending = pending.catch(() => undefined).then(() => apply(scale));
    return pending;
  };
}
