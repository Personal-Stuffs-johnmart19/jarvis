import test from "node:test";
import assert from "node:assert/strict";
import { assistantReply } from "../../apps/desktop/workbench/src/modules/ai/lib/assistantReply.ts";
import { createApprovalResponder } from "../../apps/desktop/workbench/src/modules/ai/lib/approvalResponse.ts";
test("tool-only turns use actual outcomes without exposing reasoning or claiming a game launched", () => {
  const message = (output) => [
    {
      id: "m",
      role: "assistant",
      parts: [
        { type: "reasoning", text: "private" },
        {
          type: "tool-jarvis_action",
          toolCallId: "a",
          state: "output-available",
          input: {},
          output,
        },
        { type: "step-start" },
      ],
    },
  ];
  assert.equal(
    assistantReply(message({ ok: true, message: "Steam did not launch" })),
    "Steam did not launch",
  );
  assert.match(assistantReply(message({ ok: true })), /The tool has finished/);
  assert.match(
    assistantReply(message({ ok: false, error: "not installed" })),
    /Failed to execute.*not installed/,
  );
  assert.equal(
    assistantReply([
      {
        id: "m",
        role: "assistant",
        parts: [{ type: "reasoning", text: "private" }],
      },
    ]),
    "",
  );
  assert.equal(
    assistantReply([
      {
        id: "m",
        role: "assistant",
        parts: [{ type: "tool-write_file", state: "output-denied" }],
      },
    ]),
    "Action rejected and not executed.",
  );
});
test("concurrent decisions from two UIs execute once and stale approvals are rejected", async () => {
  const pending = new Set(["same"]);
  let calls = 0;
  const respond = createApprovalResponder(
    (id) => pending.has(id),
    async () => {
      calls++;
      await Promise.resolve();
    },
  );
  assert.deepEqual(
    await Promise.all([respond("same", true), respond("same", false)]),
    [true, false],
  );
  assert.equal(calls, 1);
  assert.equal(await respond("old-session", true), false);
  pending.delete("same");
  assert.equal(await respond("same", true), false);
});
