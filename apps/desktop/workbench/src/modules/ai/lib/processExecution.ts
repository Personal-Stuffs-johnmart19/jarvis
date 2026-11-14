export const PROCESS_CANCELLED_ERROR = "Command cancelled";

export function makeProcessRequestId(): string {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), (word) =>
    word.toString(16).padStart(8, "0"),
  ).join("");
}

export function makeProcessAbortError(abortSignal?: AbortSignal): unknown {
  const reason: unknown = abortSignal?.reason;
  if (reason instanceof Error) return reason;
  return new DOMException("Command aborted", "AbortError");
}

export function isProcessAbortError(
  error: unknown,
  abortSignal?: AbortSignal,
): boolean {
  if (abortSignal?.aborted) return true;
  if (typeof error === "string" && error.includes(PROCESS_CANCELLED_ERROR)) {
    return true;
  }
  const seen = new Set<object>();
  while (error && typeof error === "object" && !seen.has(error)) {
    seen.add(error);
    const err = error as {
      name?: unknown;
      code?: unknown;
      message?: unknown;
      cause?: unknown;
    };
    if (err.name === "AbortError" || err.code === "ABORT_ERR") return true;
    if (
      typeof err.message === "string" &&
      err.message.includes(PROCESS_CANCELLED_ERROR)
    ) {
      return true;
    }
    error = err.cause;
  }
  return false;
}

export async function runCancellableProcess<T>(
  run: (requestId: string) => Promise<T>,
  cancel: (requestId: string) => Promise<void>,
  signal?: AbortSignal,
  createRequestId = makeProcessRequestId,
): Promise<T> {
  signal?.throwIfAborted();
  const requestId = createRequestId();
  const onAbort = () => {
    void cancel(requestId).catch(() => {});
  };
  signal?.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal?.aborted) {
      onAbort();
      signal.throwIfAborted();
    }
    const result = await run(requestId);
    signal?.throwIfAborted();
    return result;
  } catch (error) {
    if (isProcessAbortError(error, signal) && !(error instanceof Error)) {
      throw makeProcessAbortError(signal);
    }
    throw error;
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}
