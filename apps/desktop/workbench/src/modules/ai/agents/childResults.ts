import type { JSONValue } from "ai";

export type ChildToolRecord = {
  child_tool_call_id: string;
  tool_name: string;
  output: unknown;
  output_bytes: number;
  truncated: boolean;
};
export type ChildResultArchive = {
  child_tool_results?: ChildToolRecord[];
  child_tool_results_omitted?: number;
  child_tool_result_limit_bytes?: number;
};
const RESULT_LIMIT_BYTES = 256000;
const RESULT_LIMIT_COUNT = 32;

/** Saved parent output retains bounded child evidence separately from model preview. */
export function archiveChildResults(
  steps: readonly {
    toolResults: readonly {
      toolCallId: string;
      toolName: string;
      output: unknown;
    }[];
  }[],
): ChildResultArchive {
  const all = steps.flatMap((step) => step.toolResults);
  let remaining = RESULT_LIMIT_BYTES;
  const records = all.slice(0, RESULT_LIMIT_COUNT).map((result) => {
    let text: string;
    try {
      text = JSON.stringify(result.output ?? null);
    } catch {
      text = JSON.stringify({ error: "Child output could not be serialized" });
    }
    const bytes = new TextEncoder().encode(text);
    const truncated = bytes.length > remaining;
    const retained = Math.min(bytes.length, remaining);
    const output = truncated
      ? {
          truncated: true,
          preview: new TextDecoder().decode(bytes.slice(0, retained), {
            stream: true,
          }),
          total_bytes: bytes.length,
          retained_bytes: retained,
        }
      : JSON.parse(text);
    remaining -= retained;
    return {
      child_tool_call_id: result.toolCallId,
      tool_name: result.toolName,
      output,
      output_bytes: bytes.length,
      truncated,
    };
  });
  return {
    child_tool_results: records,
    child_tool_results_omitted: Math.max(0, all.length - records.length),
    child_tool_result_limit_bytes: RESULT_LIMIT_BYTES,
  };
}

export function childModelOutput(
  output: unknown,
  outerCallId: string,
): { type: "json"; value: JSONValue } {
  const result =
    output && typeof output === "object"
      ? (output as Record<string, unknown>)
      : {};
  let remaining = 4000;
  const pending = Array.isArray(result.pending_actions)
    ? result.pending_actions.slice(0, 4).map((raw) => {
        if (!raw || typeof raw !== "object")
          return { error: "Invalid saved child handoff" };
        const action = raw as Record<string, unknown>;
        const serialized = JSON.stringify(action.args) ?? "";
        const includeArgs = serialized.length <= remaining;
        if (includeArgs) remaining -= serialized.length;
        return {
          status: action.status,
          child_tool_call_id: action.child_tool_call_id,
          name: action.name,
          ...(includeArgs
            ? { args: action.args }
            : { args_omitted: true, argument_characters: serialized.length }),
        };
      })
    : [];
  const records = Array.isArray(result.child_tool_results)
    ? result.child_tool_results
    : [];
  const projection = {
    ...(typeof result.error === "string"
      ? { error: result.error.slice(0, 4000) }
      : {}),
    summary:
      typeof result.summary === "string" ? result.summary.slice(0, 4000) : "",
    summary_truncated:
      typeof result.summary === "string" && result.summary.length > 4000,
    ...(pending.length ? { pending_actions: pending } : {}),
    child_tool_results_retained: records.length,
    child_tool_results_truncated: records.filter(
      (record) =>
        record &&
        typeof record === "object" &&
        (record as Record<string, unknown>).truncated,
    ).length,
    child_tool_results_omitted: result.child_tool_results_omitted ?? 0,
    child_tool_result_limit_bytes:
      result.child_tool_result_limit_bytes ?? RESULT_LIMIT_BYTES,
    full_output_tool_call_id: outerCallId,
    hint: "Full saved child evidence and exact pending arguments are in tool_result_read for this outer tool-call ID. Read omitted details by offset; never re-execute to recover output. Pending actions have not run; evaluate each once through parent jarvis_action and its approval policy. Child mutation resumption is not automatic.",
  };
  return { type: "json", value: JSON.parse(JSON.stringify(projection)) };
}
