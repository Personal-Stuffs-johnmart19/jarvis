import { checkShellCommand } from "../lib/security";
import {
  runCancellableProcess,
  makeProcessRequestId as makeShellRequestId,
  isProcessAbortError as isShellAbortError,
  makeProcessAbortError as makeShellAbortError,
} from "../lib/processExecution";
export {
  PROCESS_CANCELLED_ERROR as SHELL_CANCELLED_ERROR,
  isProcessAbortError as isShellAbortError,
} from "../lib/processExecution";

export type ShellSessionRunOutput = {
  stdout: string;
  stderr: string;
  exit_code: number | null;
  timed_out: boolean;
  truncated: boolean;
  cwd_after: string;
};

export async function executeBashRunTool(options: {
  command: string;
  timeoutSecs?: number;
  abortSignal?: AbortSignal;
  getSessionId: () => string | null;
  getCwd: () => string | null;
  openShell: (cwd: string | null) => Promise<number>;
  runShell: (args: {
    shellId: number;
    command: string;
    cwd: string | null;
    timeoutSecs?: number;
    requestId: string;
  }) => Promise<ShellSessionRunOutput>;
  cancelShell: (requestId: string) => Promise<void>;
  createRequestId?: () => string;
}): Promise<({ command: string } & ShellSessionRunOutput) | { error: string }> {
  const {
    command,
    timeoutSecs,
    abortSignal,
    getSessionId,
    getCwd,
    openShell,
    runShell,
    cancelShell,
    createRequestId = makeShellRequestId,
  } = options;

  abortSignal?.throwIfAborted();
  const safety = checkShellCommand(command);
  if (!safety.ok) return { error: safety.reason };
  const sid = getSessionId();
  if (!sid) return { error: "no active chat session" };

  try {
    const cwd = getCwd();
    const shellId = await openShell(cwd);
    abortSignal?.throwIfAborted();

    const r = await runCancellableProcess(
      (requestId) =>
        runShell({ shellId, command, cwd, timeoutSecs, requestId }),
      cancelShell,
      abortSignal,
      createRequestId,
    );
    return {
      command,
      stdout: r.stdout,
      stderr: r.stderr,
      exit_code: r.exit_code,
      timed_out: r.timed_out,
      truncated: r.truncated,
      cwd_after: r.cwd_after,
    };
  } catch (e) {
    if (isShellAbortError(e, abortSignal)) {
      if (e instanceof Error) throw e;
      throw makeShellAbortError(abortSignal);
    }
    return { error: String(e) };
  }
}
