/** Model-only excerpt; the SDK saves the original result for explicit recovery. */
import type { JSONValue, ToolSet } from "ai";

export function browserObservation(output: unknown, toolCallId: string, limit = 6000) {
  if (!output || typeof output !== "object" || Array.isArray(output)) return output;
  const result = output as Record<string, unknown>;
  if (typeof result.text !== "string" || result.text.length <= limit) return output;
  let end = limit;
  const code = result.text.charCodeAt(end - 1);
  if (code >= 0xd800 && code <= 0xdbff) end--;
  return {
    ...result,
    text: result.text.slice(0, end),
    model_excerpt: true,
    omitted_text_characters: result.text.length - end,
    full_result: {
      tool_call_id: toolCallId,
      read: "jarvis_action: tool_result_read",
      hint: "Read only the missing range with offset/limit; do not repeat navigation or playback.",
    },
  };
}

/** Apply the per-run allocation only to model projections, retaining full handlers/results. */
export function budgetBrowserObservations(tools: ToolSet, contextLimit: number): ToolSet {
  const limit = contextLimit <= 4096 ? 1000 : contextLimit <= 8192 ? 3000 : 6000;
  const result = {...tools};
  for (const name of ["browser_control", "jarvis_action"]) {
    const original = tools[name];
    if (!original) continue;
    result[name] = {
      ...original,
      toModelOutput: async (options: {input: unknown; output: unknown; toolCallId: string}) => {
        const input = options.input;
        if (name === "browser_control" || (input && typeof input === "object" && "name" in input && input.name === "browser_control"))
          return {type:"json" as const, value:(browserObservation(options.output, options.toolCallId, limit) ?? null) as JSONValue};
        if (original.toModelOutput) return await original.toModelOutput(options);
        return {type:"json" as const, value:(options.output ?? null) as JSONValue};
      },
    };
  }
  return result;
}
