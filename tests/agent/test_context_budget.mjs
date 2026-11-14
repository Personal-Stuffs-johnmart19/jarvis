import test from "node:test";
import assert from "node:assert/strict";
import {
  compactModelMessagesDetailed,
  estimateTokens,
  historyBudget,
} from "../../apps/desktop/workbench/src/modules/ai/lib/compact.ts";
const result = (id, text) => ({
  role: "tool",
  content: [
    {
      type: "tool-result",
      toolCallId: id,
      toolName: "utility_run",
      output: {
        type: "json",
        value: { exit_code: 1, stderr: text, path: "D:/demo" },
      },
    },
  ],
});
test("budget includes overhead and UTF-8, and does not change small histories", () => {
  assert.ok(estimateTokens("ошибка") > estimateTokens("error"));
  assert.ok(historyBudget(8192, 2500) < historyBudget(8192));
  const messages = [
    { role: "user", content: "Keep my instructions" },
    result("a", "small error"),
  ];
  const compact = compactModelMessagesDetailed(messages, 8192, {
    reservedTokens: 2500,
  });
  assert.equal(compact.messages, messages);
  assert.equal(compact.compacted, false);
});
test("large logs shrink without changing requests, tool inputs, approvals or stored messages", () => {
  const call = {
    role: "assistant",
    content: [
      {
        type: "tool-call",
        toolCallId: "a",
        toolName: "utility_run",
        input: { tool: "git", args: ["status"] },
      },
      { type: "tool-approval-request", approvalId: "p", toolCallId: "a" },
    ],
  };
  const approval = {
    role: "tool",
    content: [
      { type: "tool-approval-response", approvalId: "p", approved: true },
    ],
  };
  const messages = [
    { role: "user", content: "Do not repeat actions" },
    call,
    approval,
    result("a", "first\n" + "build log\n".repeat(3000) + "\nfinal error"),
    { role: "user", content: "Fix it" },
  ];
  const saved = JSON.stringify(messages);
  const compact = compactModelMessagesDetailed(messages, 8192, {
    reservedTokens: 2500,
  });
  assert.ok(compact.estimatedTokensAfter < compact.estimatedTokensBefore / 4);
  assert.equal(JSON.stringify(messages), saved);
  assert.equal(compact.messages[0], messages[0]);
  assert.equal(compact.messages[1], call);
  assert.equal(compact.messages[2], approval);
  assert.equal(compact.messages.at(-1), messages.at(-1));
  const summary = compact.messages[3].content[0].output.value;
  assert.match(summary, /exit_code/);
  assert.match(summary, /final error/);
  assert.match(summary, /Do not repeat side effects/);
  assert.equal(compact.messages[3].content[0].toolCallId, "a");
  const again = compactModelMessagesDetailed(compact.messages, 8192, {
    reservedTokens: 2500,
  });
  assert.deepEqual(again.messages, compact.messages);
});
test("unavoidable large user input is retained and reported over budget", () => {
  const messages = [{ role: "user", content: "x".repeat(50000) }];
  const compact = compactModelMessagesDetailed(messages, 4096, {
    reservedTokens: 2000,
  });
  assert.equal(compact.messages, messages);
  assert.equal(compact.overBudget, true);
});

import { readToolResult } from "../../apps/desktop/workbench/src/modules/ai/lib/toolHistory.ts";
test("tool result recovery is paginated and excludes reasoning, inputs and unexecuted approvals", () => {
  const messages = [
    {
      parts: [
        { type: "reasoning", text: "private" },
        {
          type: "tool-run_code",
          toolCallId: "a",
          state: "output-available",
          input: { code: "never return" },
          output: { stdout: "0123456789", exit_code: 0 },
        },
        {
          type: "tool-run_code",
          toolCallId: "b",
          state: "approval-requested",
          input: { code: "pending" },
        },
      ],
    },
  ];
  const first = readToolResult(messages, "a", 0, 10);
  assert.equal(first.text, '{"stdout":');
  assert.equal(first.next_offset, 10);
  assert.ok(!JSON.stringify(first).includes("never return"));
  assert.ok(readToolResult(messages, "b", 0, 10).error);
  const next = readToolResult(messages, "a", 10, 100);
  assert.equal(next.next_offset, null);
  assert.match(first.text + next.text, /0123456789/);
});

test("the latest result batch of an active request is never compacted", () => {
  const latest = result("fresh", "schema or latest failure ".repeat(3000));
  const messages = [
    { role: "user", content: "Current task" },
    result("old", "old log ".repeat(3000)),
    latest,
  ];
  const compact = compactModelMessagesDetailed(messages, 4096, {
    reservedTokens: 2500,
  });
  assert.equal(compact.messages.at(-1), latest);
  assert.ok(compact.compacted);
});

test("compaction never inflates short results when preserved input exceeds budget", () => {
  const messages = [
    result("short", "x".repeat(170)),
    { role: "user", content: "preserved ".repeat(3000) },
  ];
  const compact = compactModelMessagesDetailed(messages, 4096);
  assert.ok(compact.estimatedTokensAfter <= compact.estimatedTokensBefore);
  assert.equal(compact.messages[0], messages[0]);
  assert.equal(compact.overBudget, true);
});

import { assertContextBudget, CONTEXT_FULL_MESSAGE } from "../../apps/desktop/workbench/src/modules/ai/lib/compact.ts";

test("irreducible input stops before provider execution and preserves saved requests and approvals", () => {
  const messages = [
    { role: "user", content: [{ type: "text", text: "x".repeat(30000) }, { type: "file", data: "file-data", mediaType: "text/plain" }] },
    { role: "assistant", content: [{ type: "tool-call", toolCallId: "executed", toolName: "utility_run", input: { args: ["status"] } }, { type: "tool-approval-request", approvalId: "approved", toolCallId: "executed" }] },
    { role: "tool", content: [{ type: "tool-approval-response", approvalId: "approved", approved: true }] },
    result("executed", "completed"),
  ];
  const saved = JSON.stringify(messages);
  const compact = compactModelMessagesDetailed(messages, 4096, { reservedTokens: 2000 });
  let providerCalls = 0;
  assert.throws(() => {
    assertContextBudget(compact);
    providerCalls++;
  }, error => error.code === "context_budget_exceeded" && error.message === CONTEXT_FULL_MESSAGE);
  assert.equal(providerCalls, 0);
  assert.equal(JSON.stringify(messages), saved);
  assert.deepEqual(compact.messages, messages);
});

test("oversized fresh discovery stops without replaying or modifying the result", () => {
  const latest = result("catalog", "latest schema ".repeat(4000));
  const messages = [{ role: "user", content: "Use the selected action" }, latest];
  const compact = compactModelMessagesDetailed(messages, 4096);
  assert.equal(compact.messages.at(-1), latest);
  assert.throws(() => assertContextBudget(compact, "Localized context full"), { message: "Localized context full" });
});

test("fixed prompt overhead alone cannot create an artificial history allowance", () => {
  assert.equal(historyBudget(4096, 4096), 0);
  const compact = compactModelMessagesDetailed([{ role: "user", content: "Hi" }], 4096, { reservedTokens: 4096 });
  assert.throws(() => assertContextBudget(compact), { code: "context_budget_exceeded" });
});

test("fitting projections continue normally without changing the saved full output", () => {
  const messages = [result("old", "old output ".repeat(4000)), { role: "user", content: "Continue using the completed result" }];
  const saved = JSON.stringify(messages);
  const compact = compactModelMessagesDetailed(messages, 8192, { reservedTokens: 2000 });
  assert.equal(compact.compacted, true);
  assert.doesNotThrow(() => assertContextBudget(compact));
  assert.equal(JSON.stringify(messages), saved);
});

import { replyTokenBudget } from "../../apps/desktop/workbench/src/modules/ai/lib/compact.ts";
test("generation has the same bounded reply allowance reserved by context accounting", () => {
  for (const context of [2048, 8192, 32768, 131072]) {
    const reply = replyTokenBudget(context);
    assert.ok(reply >= 512 && reply <= 4096);
    assert.equal(historyBudget(context, 100), Math.max(0, Math.floor(context * .9) - reply - 100));
  }
});