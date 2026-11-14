/** Bound the pre-header wait; streamed content and tools retain their own budgets. */
export function modelFetch(fetcher: typeof fetch, headerMs = 45000): typeof fetch {
  return async (input, init) => {
    const request = new Request(input, init);
    request.signal.throwIfAborted();
    const deadline = new AbortController();
    const timer = setTimeout(() => deadline.abort(new DOMException("The model did not respond in time", "TimeoutError")), headerMs);
    try {
      return await fetcher(request, { signal: AbortSignal.any([request.signal, deadline.signal]) });
    } catch (error) {
      if (deadline.signal.aborted && !request.signal.aborted) throw deadline.signal.reason;
      throw error;
    } finally { clearTimeout(timer); }
  };
}
