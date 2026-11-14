import { runCancellableProcess } from "../lib/processExecution";
import { tool } from "ai";
import { z } from "zod";
import { invoke } from "@/host/core";
import {
  createMcpExecutor,
  mcpCatalogue,
  mcpModelOutput,
} from "../../mcp/client";
import type { ToolContext } from "./context";
import { queuedMcp } from "./queuedDispatch";
import { readOnlyRequest } from "../lib/readOnlyRequest";

export function buildMcpTools(ctx: ToolContext) {
  const executor = createMcpExecutor(
    invoke,
    ctx.getSessionId,
    runCancellableProcess,
  );
  return {
    mcp_catalog: tool({
      description:
        "Discover configured MCP integrations. Without server_id list enabled servers; with server_id connect on demand and paginate tools. Request include_parameters=true for an exact tool schema. Credentials stay native. Use mcp_call through jarvis_action to execute a discovered tool.",
      inputSchema: z
        .object({
          server_id: z.string().min(1).max(128).optional(),
          query: z.string().max(256).optional(),
          cursor: z.number().int().min(0).optional(),
          limit: z.number().int().min(1).max(20).optional(),
          include_parameters: z.boolean().optional(),
        })
        .strict(),
      execute: async (input, options) => {
        try {
          return await readOnlyRequest(() => mcpCatalogue(invoke, input), options.abortSignal);
        } catch (error) {
          return { ok: false, error: String(error).slice(0, 2000) };
        }
      },
    }),
    mcp_call: queuedMcp(tool({
      description:
        "Execute a discovered MCP tool using the same session, configured policy and approval UI as all Jarvis tools. Never provide approval flags, credentials or configuration revisions. Verify uncertain side effects before retrying.",
      inputSchema: z
        .object({
          server_id: z.string().min(1).max(128),
          tool_name: z.string().min(1).max(128),
          arguments: z.record(z.string(), z.unknown()).default({}),
        })
        .strict(),
      needsApproval: (input, options) =>
        executor.prepare(input, options.toolCallId),
      execute: (input, options) =>
        executor.execute(input, options.toolCallId, options.abortSignal, options.context),
      toModelOutput: ({ output, toolCallId }) =>
        mcpModelOutput(output, toolCallId),
    }), { preparation: executor.preparation, revoke: executor.revoke }),
  };
}
