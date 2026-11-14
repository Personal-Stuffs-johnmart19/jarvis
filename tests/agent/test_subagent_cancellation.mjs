import test from "node:test";
import assert from "node:assert/strict";
import {
  executeSubagentTool,
  childReadContext,
  isSubagentAbortError,
} from "../../apps/desktop/workbench/src/modules/ai/agents/subagentExecution.ts";

test("propagates execute abortSignal into child runner and rethrows AbortError instead of swallowing as tool result", async () => {
  const controller = new AbortController();
  let seenSignal = null;

  await assert.rejects(
    () =>
      executeSubagentTool({
        type: "explore",
        prompt: "inspect files",
        abortSignal: controller.signal,
        captureConfig: () => ({ modelId: "m1" }),
        run: async ({ abortSignal }) => {
          seenSignal = abortSignal;
          controller.abort();
          abortSignal?.throwIfAborted();
          return { summary: "should not reach", stepCount: 1, durationMs: 5 };
        },
      }),
    (err) => isSubagentAbortError(err, controller.signal),
  );
  assert.equal(seenSignal, controller.signal);
});

test("returns structured error for non-abort failures without throwing", async () => {
  const res = await executeSubagentTool({
    type: "security",
    prompt: "audit auth",
    captureConfig: () => ({ modelId: "m1" }),
    run: async () => {
      throw new Error("provider rate limit");
    },
  });
  assert.deepEqual(res, {
    error: "Error: provider rate limit",
    type: "security",
  });
});

test("snapshots config at spawn and suppresses step updates after session switch or abort", async () => {
  let activeSessionId = "session-1";
  let liveModelId = "model-initial";
  const steps = [];
  const controller = new AbortController();

  const res = await executeSubagentTool({
    type: "code-review",
    prompt: "review diff",
    description: "Review PR",
    abortSignal: controller.signal,
    getSessionId: () => "session-1",
    getActiveSessionId: () => activeSessionId,
    captureConfig: () => ({ modelId: liveModelId }),
    onStep: (label) => steps.push(label),
    run: async ({ config, onStep }) => {
      liveModelId = "model-mutated-mid-run";
      assert.equal(config.modelId, "model-initial");
      onStep("code-review: read_file");
      activeSessionId = "session-2";
      onStep("code-review: grep");
      return { summary: "Looks good.", stepCount: 2, durationMs: 12 };
    },
  });

  assert.deepEqual(steps, ["code-review: read_file"]);
  assert.deepEqual(res, {
    type: "code-review",
    description: "Review PR",
    summary: "Looks good.",
    stepCount: 2,
    durationMs: 12,
  });
});

test("already-cancelled tasks never capture settings or start a child", async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(executeSubagentTool({
    type: "explore", prompt: "unused", abortSignal: controller.signal,
    captureConfig: () => assert.fail("captured settings after cancellation"),
    run: async () => assert.fail("started child after cancellation"),
  }), {name: "AbortError"});
});

test("late completion and progress after cancellation cannot become a successful tool result", async () => {
  const controller = new AbortController(); const steps = [];
  await assert.rejects(executeSubagentTool({
    type: "explore", prompt: "inspect", abortSignal: controller.signal,
    captureConfig: () => ({}), onStep: (step) => steps.push(step),
    run: async ({onStep}) => {
      controller.abort(); onStep("stale progress");
      return {summary:"late result",stepCount:1,durationMs:1};
    },
  }), {name:"AbortError"});
  assert.deepEqual(steps, []);
  const cycle = {}; cycle.cause = cycle;
  assert.equal(isSubagentAbortError(cycle), false);
  assert.equal(isSubagentAbortError({cause: new DOMException("Stopped", "AbortError")}), true);
});

test('child reads start with an isolated cache and retain their spawn workspace',()=>{
 let cwd='D:/project', root='D:/project';
 const parent={getCwd:()=>cwd,getWorkspaceRoot:()=>root,readCache:new Map([['a',{size:5,hash:42}]])};
 const child=childReadContext(parent);
 assert.equal(child.readCache.size,0);
 child.readCache.set('b',{size:6,hash:44});
 assert.equal(parent.readCache.has('b'),false);
 cwd='C:/different'; root='C:/different';
 assert.equal(child.getCwd(),'D:/project');
 assert.equal(child.getWorkspaceRoot(),'D:/project');
});
