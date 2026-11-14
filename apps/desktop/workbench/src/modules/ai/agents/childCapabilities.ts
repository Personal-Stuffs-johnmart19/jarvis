import { asSchema, type ToolSet } from "ai";

export type ChildHandoff = {
  status: "requires_parent_approval";
  child_tool_call_id: string;
  name: string;
  args: Record<string, unknown>;
  hint: string;
};

// Every other capability remains discoverable, but its policy is evaluated only
// on the parent's approval stream. A nested generateText has no UI approval sink.
const DIRECT_READS = new Set([
  "read_file",
  "list_directory",
  "grep",
  "glob",
  "data_query",
  "conversation_read",
  "jarvis_catalog",
  "mcp_catalog",
  "utility_catalog",
]);

export function childCapabilityTools(
  tools: ToolSet,
  handoffs: ChildHandoff[],
): ToolSet {
  return Object.fromEntries(
    Object.entries(tools).map(([name, action]) => [
      name,
      {
        ...action,
        needsApproval: undefined,
        execute: async (
          input: unknown,
          options: Parameters<NonNullable<typeof action.execute>>[1],
        ) => {
          options.abortSignal?.throwIfAborted();
          if (DIRECT_READS.has(name) && !action.needsApproval) {
            if (!action.execute) throw new Error("Capability has no executor");
            const result = await action.execute(input, options);
            options.abortSignal?.throwIfAborted();
            return result;
          }
          const request =
            name === "jarvis_action"
              ? (input as { name: string; args: Record<string, unknown> })
              : { name, args: input as Record<string, unknown> };
          if (
            !request ||
            typeof request.name !== "string" ||
            !request.args ||
            typeof request.args !== "object" ||
            Array.isArray(request.args)
          )
            return { error: "Invalid child action; no action was executed" };
          if (JSON.stringify(request).length > 48000)
            return { error: "Oversized child action; no action was executed" };
          const read = tools[request.name];
          if (name === "jarvis_action" && DIRECT_READS.has(request.name) && read && !read.needsApproval) {
            if (!read.execute) throw new Error("Capability has no executor");
            const schema = asSchema(read.inputSchema);
            if (!schema.validate)
              return { error: "Child read schema cannot be validated; no action was executed" };
            const validated = await schema.validate(request.args);
            options.abortSignal?.throwIfAborted();
            if (!validated.success)
              return { error: "Invalid child read arguments; no action was executed" };
            const result = await read.execute(validated.value, options);
            options.abortSignal?.throwIfAborted();
            return result;
          }
          if (handoffs.length >= 4)
            return {
              error: "Child handoff limit reached; no action was executed",
            };
          const handoff: ChildHandoff = {
            status: "requires_parent_approval",
            child_tool_call_id: options.toolCallId,
            name: request.name,
            args: structuredClone(request.args),
            hint: "No action was executed. Parent must evaluate this exact request once through jarvis_action and its normal approval policy. Do not claim completion. The child does not resume automatically.",
          };
          handoffs.push(handoff);
          return handoff;
        },
      },
    ]),
  ) as ToolSet;
}
