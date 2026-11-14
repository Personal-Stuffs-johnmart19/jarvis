import { estimateTokens, historyBudget, replyTokenBudget } from "./compact";

export function contextBudgetKey(modelId: string, configuration: unknown) {
  return JSON.stringify([modelId, configuration]);
}

/** The same reservation math drives request admission and the estimated meter. */
export function contextBudgetEstimate(contextLimit: number, fixedTokens: number, messages: unknown) {
  const historyTokens = estimateTokens(messages);
  const replyTokens = replyTokenBudget(contextLimit);
  const safetyTokens = contextLimit - Math.floor(contextLimit * 0.9);
  const inputTokens = fixedTokens + historyTokens;
  return { contextLimit, fixedTokens, historyTokens, inputTokens, replyTokens, safetyTokens,
    allocatedTokens: inputTokens + replyTokens + safetyTokens,
    historyBudgetTokens: historyBudget(contextLimit, fixedTokens),
    overBudget: historyTokens > historyBudget(contextLimit, fixedTokens) };
}

export function currentContextBudget<T>(value: {key:string;value:T} | undefined, key: string): T | undefined {
  return value?.key === key ? value.value : undefined;
}
