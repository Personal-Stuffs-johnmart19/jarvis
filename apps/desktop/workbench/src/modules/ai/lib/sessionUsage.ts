export type SessionUsage = {
  tokens: {
    inputTokens: number;
    outputTokens: number;
    cachedInputTokens: number;
  };
  lastInputTokens: number;
  lastCachedTokens: number;
};
const count = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
export function sessionUsage(value?: SessionUsage): SessionUsage {
  return {
    tokens: {
      inputTokens: count(value?.tokens?.inputTokens),
      outputTokens: count(value?.tokens?.outputTokens),
      cachedInputTokens: count(value?.tokens?.cachedInputTokens),
    },
    lastInputTokens: count(value?.lastInputTokens),
    lastCachedTokens: count(value?.lastCachedTokens),
  };
}
export function addSessionUsage(
  value: SessionUsage | undefined,
  delta: {
    inputTokens: number;
    outputTokens: number;
    cachedInputTokens: number;
    lastInputTokens: number;
    lastCachedTokens: number;
  },
): SessionUsage {
  const prev = sessionUsage(value);
  return {
    tokens: {
      inputTokens: prev.tokens.inputTokens + count(delta.inputTokens),
      outputTokens: prev.tokens.outputTokens + count(delta.outputTokens),
      cachedInputTokens:
        prev.tokens.cachedInputTokens + count(delta.cachedInputTokens),
    },
    lastInputTokens: count(delta.lastInputTokens),
    lastCachedTokens: count(delta.lastCachedTokens),
  };
}
