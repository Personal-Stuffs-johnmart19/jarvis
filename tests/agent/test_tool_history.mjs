import test from "node:test";
import assert from "node:assert/strict";
import { readToolResult } from "../../apps/desktop/workbench/src/modules/ai/lib/toolHistory.ts";

test("readToolResult avoids splitting UTF-16 surrogate pairs at pagination boundaries", () => {
  const messages = [
    {
      role: "assistant",
      parts: [
        {
          type: "tool-run_code",
          toolCallId: "emoji",
          state: "output-available",
          output: "ab😀cd",
        },
      ],
    },
  ];
  const first = readToolResult(messages, "emoji", 0, 3);
  assert.equal(first.text, "ab");
  assert.equal(first.next_offset, 2);
  const second = readToolResult(messages, "emoji", first.next_offset, 3);
  assert.equal(second.text, "😀c");
  assert.equal(second.next_offset, 5);
  const third = readToolResult(messages, "emoji", second.next_offset, 3);
  assert.equal(third.text, "d");
  assert.equal(third.next_offset, null);
  assert.equal(first.text + second.text + third.text, "ab😀cd");
});

test("single-unit pages advance and explicit offsets inside a pair return a whole character", () => {
  const messages = [{parts: [{type: "tool-run_code", toolCallId: "a", state: "output-available", output: "😀Z"}]}];
  const first = readToolResult(messages, "a", 0, 1);
  assert.equal(first.text, "😀");
  assert.equal(first.next_offset, 2);
  assert.equal(readToolResult(messages, "a", first.next_offset, 1).text, "Z");
  assert.equal(readToolResult(messages, "a", 1, 1).text, "😀");
  assert.equal(readToolResult(messages, "a", 99, 1).next_offset, null);
});
