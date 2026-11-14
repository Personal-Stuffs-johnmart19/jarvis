import type { ChildResultArchive } from "./childResults";
import type { ChildHandoff } from "./childCapabilities";
import type { ToolContext } from "../tools/context";
import type { SubagentType } from "./registry";

export type SubagentExecResult = ChildResultArchive & {
  summary: string;
  stepCount: number;
  durationMs: number;
  pending_actions?: ChildHandoff[];
};

export function isSubagentAbortError(
  error: unknown,
  abortSignal?: AbortSignal,
): boolean {
  if (abortSignal?.aborted) return true;
  const seen = new Set<object>();
  while (error && typeof error === "object" && !seen.has(error)) {
    seen.add(error);
    const err = error as { name?: unknown; code?: unknown; cause?: unknown };
    if (err.name === "AbortError" || err.code === "ABORT_ERR") return true;
    error = err.cause;
  }
  return false;
}

export async function executeSubagentTool<TConfig>(options: {
  type: SubagentType;
  prompt: string;
  description?: string;
  abortSignal?: AbortSignal;
  getSessionId?: () => string | null;
  getActiveSessionId?: () => string | null;
  captureConfig: () => TConfig;
  onStep?: (label: string) => void;
  run: (args: {
    type: SubagentType;
    prompt: string;
    config: TConfig;
    abortSignal?: AbortSignal;
    onStep: (label: string) => void;
  }) => Promise<SubagentExecResult>;
}): Promise<
  | (ChildResultArchive & {
      type: SubagentType;
      description?: string;
      summary: string;
      stepCount: number;
      durationMs: number;
      pending_actions?: ChildHandoff[];
    })
  | { error: string; type: SubagentType }
> {
  const {
    type,
    prompt,
    description,
    abortSignal,
    getSessionId,
    getActiveSessionId,
    captureConfig,
    onStep,
    run,
  } = options;
  abortSignal?.throwIfAborted();
  const originSessionId = getSessionId?.() ?? null;
  const config = captureConfig();
  const stepHandler = (label: string) => {
    if (!onStep || abortSignal?.aborted) return;
    if (
      originSessionId !== null &&
      getActiveSessionId &&
      getActiveSessionId() !== originSessionId
    ) {
      return;
    }
    onStep(label);
  };
  try {
    const r = await run({
      type,
      prompt,
      config,
      abortSignal,
      onStep: stepHandler,
    });
    abortSignal?.throwIfAborted();
    return {
      type,
      description,
      summary: r.summary,
      stepCount: r.stepCount,
      durationMs: r.durationMs,
      ...(r.child_tool_results ? {child_tool_results:r.child_tool_results,child_tool_results_omitted:r.child_tool_results_omitted,child_tool_result_limit_bytes:r.child_tool_result_limit_bytes} : {}),
      ...(r.pending_actions?.length ? { pending_actions: r.pending_actions } : {}),
    };
  } catch (e) {
    if (isSubagentAbortError(e, abortSignal)) {
      throw e;
    }
    return { error: String(e), type };
  }
}

/** A child has no parent message history, so its read cache must start empty. */
export function childReadContext(parent: ToolContext): ToolContext {
  const cwd = parent.getCwd();
  const workspace = parent.getWorkspaceRoot();
  return { ...parent, readCache: new Map(), getCwd: () => cwd, getWorkspaceRoot: () => workspace };
}
