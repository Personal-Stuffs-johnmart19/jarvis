import test from "node:test";
import assert from "node:assert/strict";
import { registerHooks } from "node:module";
const securityUrl = new URL("./security_fixture.mjs", import.meta.url).href;
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "../lib/security") return { url: securityUrl, shortCircuit: true };
    try {
      return next(specifier, context);
    } catch (error) {
      if (
        error.code === "ERR_MODULE_NOT_FOUND" &&
        specifier.startsWith(".") &&
        context.parentURL?.includes("/apps/desktop/workbench/src/")
      )
        return next(specifier + ".ts", context);
      throw error;
    }
  },
});
const { executeBashRunTool, isShellAbortError, SHELL_CANCELLED_ERROR } =
  await import(
    "../../apps/desktop/workbench/src/modules/ai/tools/shellExecution.ts"
  );

test("forwards abortSignal to native shell_run_cancel by requestId and rethrows abort instead of returning tool result", async () => {
  const controller = new AbortController();
  const cancelledIds = [];
  let seenArgs = null;

  await assert.rejects(
    () =>
      executeBashRunTool({
        command: "python -m unittest",
        timeoutSecs: 45,
        abortSignal: controller.signal,
        getSessionId: () => "session-1",
        getCwd: () => "/workspace",
        openShell: async () => 7,
        createRequestId: () => "req-shell-1",
        cancelShell: async (id) => {
          cancelledIds.push(id);
        },
        runShell: async (args) => {
          seenArgs = args;
          controller.abort();
          throw SHELL_CANCELLED_ERROR;
        },
      }),
    (err) => isShellAbortError(err, controller.signal),
  );

  assert.deepEqual(seenArgs, {
    shellId: 7,
    command: "python -m unittest",
    cwd: "/workspace",
    timeoutSecs: 45,
    requestId: "req-shell-1",
  });
  assert.deepEqual(cancelledIds, ["req-shell-1"]);
});

test("already-aborted signal never opens a shell session or invokes native run", async () => {
  const controller = new AbortController();
  controller.abort();

  await assert.rejects(
    () =>
      executeBashRunTool({
        command: "npm test",
        abortSignal: controller.signal,
        getSessionId: () => "session-1",
        getCwd: () => "/workspace",
        openShell: async () => assert.fail("opened shell after abort"),
        runShell: async () => assert.fail("ran command after abort"),
        cancelShell: async () => assert.fail("cancelled non-existent run"),
      }),
    { name: "AbortError" },
  );
});

test("abort during shellSessionOpen prevents native command execution", async () => {
  const controller = new AbortController();
  const cancelledIds = [];

  await assert.rejects(
    () =>
      executeBashRunTool({
        command: "npm run build",
        abortSignal: controller.signal,
        getSessionId: () => "session-1",
        getCwd: () => "/workspace",
        openShell: async () => {
          controller.abort();
          return 11;
        },
        runShell: async () => assert.fail("must not spawn command after abort"),
        cancelShell: async (id) => cancelledIds.push(id),
      }),
    { name: "AbortError" },
  );
  assert.deepEqual(cancelledIds, []);
});

test("late native completion after abort is rejected and never returned as successful output", async () => {
  const controller = new AbortController();
  const cancelledIds = [];

  await assert.rejects(
    () =>
      executeBashRunTool({
        command: "python script.py",
        abortSignal: controller.signal,
        getSessionId: () => "session-1",
        getCwd: () => "/workspace",
        openShell: async () => 3,
        createRequestId: () => "req-late-1",
        cancelShell: async (id) => cancelledIds.push(id),
        runShell: async () => {
          controller.abort();
          return {
            stdout: "partial",
            stderr: "",
            exit_code: 0,
            timed_out: false,
            truncated: false,
            cwd_after: "/workspace",
          };
        },
      }),
    { name: "AbortError" },
  );
  assert.deepEqual(cancelledIds, ["req-late-1"]);
});

test("non-abort shell failures and safety rejections still return structured error objects", async () => {
  const blocked = await executeBashRunTool({
    command: "rm -rf /",
    getSessionId: () => "session-1",
    getCwd: () => "/workspace",
    openShell: async () => assert.fail("must not open shell for blocked cmd"),
    runShell: async () => assert.fail("must not run blocked cmd"),
    cancelShell: async () => {},
  });
  assert.ok("error" in blocked && blocked.error.length > 0);

  const failed = await executeBashRunTool({
    command: "echo ok",
    getSessionId: () => "session-1",
    getCwd: () => "/workspace",
    openShell: async () => 2,
    runShell: async () => {
      throw new Error("no shell session");
    },
    cancelShell: async () => {},
  });
  assert.deepEqual(failed, { error: "Error: no shell session" });
});
