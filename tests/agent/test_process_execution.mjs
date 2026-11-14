import test from "node:test";
import assert from "node:assert/strict";
import { runCancellableProcess } from "../../apps/desktop/workbench/src/modules/ai/lib/processExecution.ts";

test("cancels only the targeted concurrent run and removes listeners after completion", async () => {
  const first = new AbortController(),
    second = new AbortController();
  const cancelled = [];
  let finishFirst, finishSecond;
  const a = runCancellableProcess(
    () =>
      new Promise((resolve) => {
        finishFirst = resolve;
      }),
    async (id) => {
      cancelled.push(id);
      finishFirst("late");
    },
    first.signal,
    () => "one",
  );
  const b = runCancellableProcess(
    () =>
      new Promise((resolve) => {
        finishSecond = resolve;
      }),
    async (id) => {
      cancelled.push(id);
    },
    second.signal,
    () => "two",
  );
  const rejected = assert.rejects(a, { name: "AbortError" });
  first.abort();
  finishSecond("ok");
  await rejected;
  assert.equal(await b, "ok");
  second.abort();
  assert.deepEqual(cancelled, ["one"]);
});
