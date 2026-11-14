import type { ModelMessage } from "ai";

const MARKER = "[context-summary";
type Part = {
  type: string;
  toolName?: string;
  toolCallId?: string;
  output?: unknown;
  [key: string]: unknown;
};
/** Conservative byte-based estimate, not a provider tokenizer or quota counter. */
export function estimateTokens(value: unknown): number {
  return Math.ceil(
    new TextEncoder().encode(
      typeof value === "string" ? value : (JSON.stringify(value) ?? ""),
    ).length / 3,
  );
}
export function replyTokenBudget(contextLimit: number): number {
  return Math.min(4096, Math.max(512, Math.floor(contextLimit * 0.15)));
}
export function historyBudget(
  contextLimit: number,
  reservedTokens = 0,
): number {
  const reply = replyTokenBudget(contextLimit);
  return Math.max(0, Math.floor(contextLimit * 0.9) - reply - reservedTokens);
}
function summary(part: Part, cap: number, recoveryTool?: "direct"): Part {
  const output = part.output as { type?: string; value?: unknown } | undefined;
  const value = output && "value" in output ? output.value : part.output;
  const text =
    typeof value === "string" ? value : (JSON.stringify(value) ?? "");
  if (text.length <= cap) return part;
  const status: Record<string, unknown> = {};
  if (value && typeof value === "object") {
    for (const key of [
      "ok",
      "error",
      "status",
      "exit_code",
      "timed_out",
      "truncated",
      "path",
      "destination",
      "id",
      "document_id",
      "next_cursor",
    ]) {
      const item = (value as Record<string, unknown>)[key];
      if (item !== undefined)
        status[key] =
          typeof item === "object" && item !== null
            ? JSON.stringify(item).slice(0, 400)
            : typeof item === "string"
              ? item.slice(0, 400)
              : item;
    }
  }
  const head = Math.ceil(cap * 0.65),
    tail = Math.floor(cap * 0.35);
  const candidate: Part = {
    ...part,
    output: {
      type: "text",
      value: `${MARKER} ${part.toolCallId ?? "unknown"}] Full: tool_result_read${recoveryTool === "direct" ? "" : " via jarvis_action"} (tool_call_id, offset, limit). Do not repeat side effects.\n${JSON.stringify(status)}\n${text.slice(0, head)}\n…[omitted ${text.length - head - tail} characters]…\n${text.slice(-tail)}`,
    },
  };
  // Recovery metadata is useful only when the projection actually saves space.
  return estimateTokens(candidate) < estimateTokens(part) ? candidate : part;
}
export type CompactResult = {
  messages: ModelMessage[];
  compacted: boolean;
  droppedCount: number;
  estimatedTokensBefore: number;
  estimatedTokensAfter: number;
  historyBudgetTokens: number;
  overBudget: boolean;
};
export const CONTEXT_FULL_MESSAGE =
  "The selected model’s context is full. Start a new chat with a shorter request, or select a model with a larger loaded context. Your saved chat and completed actions are preserved; do not repeat completed actions.";

/** Stop before another provider request when safe output projection cannot fit. */
export function assertContextBudget(
  compact: Pick<CompactResult, "overBudget">,
  message = CONTEXT_FULL_MESSAGE,
): void {
  if (compact.overBudget) {
    throw Object.assign(new Error(message), { code: "context_budget_exceeded" });
  }
}

export function compactModelMessages(
  messages: ModelMessage[],
  contextLimit: number,
): ModelMessage[] {
  return compactModelMessagesDetailed(messages, contextLimit).messages;
}
/** Change only tool output projections. User text, call inputs/IDs and approvals stay intact. */
export function compactModelMessagesDetailed(
  messages: ModelMessage[],
  contextLimit: number,
  options: { reservedTokens?: number; recoveryTool?: "direct" } = {},
): CompactResult {
  const budget = historyBudget(contextLimit, options.reservedTokens);
  const before = estimateTokens(messages);
  let working = messages;
  const changed = new Set<number>();
  if (before > budget) {
    // Keep the latest result batch of the active request intact (schemas/errors/data).
    let lastUser = -1,
      lastResult = -1;
    messages.forEach((message, index) => {
      if (message.role === "user") lastUser = index;
      if (
        message.role === "tool" &&
        Array.isArray(message.content) &&
        message.content.some((part) => part.type === "tool-result")
      )
        lastResult = index;
    });
    for (const cap of [600, 160, 40]) {
      if (estimateTokens(working) <= budget) break;
      working = messages.map((message, index): ModelMessage => {
        if (
          message.role !== "tool" ||
          !Array.isArray(message.content) ||
          (index === lastResult && index > lastUser)
        )
          return message;
        let local = false;
        const content = (message.content as Part[]).map((part) => {
          if (
            part.type !== "tool-result" ||
            (typeof (part.output as { value?: unknown })?.value === "string" &&
              String((part.output as { value: unknown }).value).startsWith(
                MARKER,
              ))
          )
            return part;
          const next = summary(part, cap, options.recoveryTool);
          if (next !== part) local = true;
          return next;
        });
        if (!local) return message;
        changed.add(index);
        return { ...message, content } as ModelMessage;
      });
    }
  }

  const after = estimateTokens(working);
  return {
    messages: changed.size ? working : messages,
    compacted: changed.size > 0,
    droppedCount: changed.size,
    estimatedTokensBefore: before,
    estimatedTokensAfter: after,
    historyBudgetTokens: budget,
    overBudget: after > budget,
  };
}
