import test from "node:test";
import assert from "node:assert/strict";
import { assistantReply } from "../../apps/desktop/workbench/src/modules/ai/lib/assistantReply.ts";

test("assistantReply ignores intermediate prose before a final tool-only step", () => {
  const messages = [
    {
      id: "a1",
      role: "assistant",
      parts: [
        { type: "step-start" },
        { type: "text", text: "Let me write the file first." },
        {
          type: "tool-write_file",
          toolCallId: "call_1",
          state: "output-available",
          output: { ok: true, path: "/tmp/out.txt", bytesWritten: 10 },
        },
        { type: "step-start" },
        { type: "text", text: "" },
      ],
    },
  ];
  assert.equal(
    assistantReply(messages),
    "The tool has finished working. Result details are available in the workspace.",
  );

});

test("assistantReply surfaces earlier tool reply/error when final tool output is generic", () => {
  const messages = [
    {
      id: "a2",
      role: "assistant",
      parts: [
        { type: "step-start" },
        {
          type: "tool-run_subagent",
          toolCallId: "sub_1",
          state: "output-available",
          output: { summary: "Found 3 callers in src/index.ts" },
        },
        {
          type: "tool-todo_write",
          toolCallId: "todo_1",
          state: "output-available",
          output: { ok: true, count: 2, inProgress: null },
        },
      ],
    },
  ];
  assert.equal(assistantReply(messages), "Found 3 callers in src/index.ts");
});

test("a successful retry supersedes an earlier error and old turns never leak", () => {
  const tool = (output) => ({type: "tool-write_file", state: "output-available", output});
  assert.match(assistantReply([{role: "assistant", parts: [tool({error: "retry me"}), tool({ok: true})]}]), /The tool has finished/);
  assert.equal(assistantReply([
    {role: "assistant", parts: [tool({error: "old error"})]},
    {role: "user", parts: [{type: "text", text: "New request"}]},
    {role: "assistant", parts: [{type: "reasoning", text: "private"}]},
  ]), "");
});
test("a new request with no assistant result cannot reuse the previous reply", () => {
  assert.equal(assistantReply([
    {role: "assistant", parts: [{type: "text", text: "Old reply"}]},
    {role: "user", parts: [{type: "text", text: "New request"}]},
  ]), "");
});
