import type { runCancellableProcess } from "../ai/lib/processExecution";
import type {
  McpConfig,
  McpInput,
  McpStatus,
  McpTool,
  NativeInvoke,
} from "./types.ts";

type PhysicalOperation = {
  requestId: string;
  nonce: string;
  invoke: NativeInvoke;
  used: boolean;
};
const physicalOperations = new WeakMap<object, PhysicalOperation>();
/** Bind execution context and cleanup proof to one physical MCP operation.
 * Backend-owned identities let a replacement queue controller recover its probe. */
export function createMcpOperation(
  invoke: NativeInvoke,
  identity?: { requestId: string; cleanupNonce: string },
) {
  const requestId = identity ? identity.requestId : crypto.randomUUID().replaceAll("-", "");
  const cleanupNonce = identity ? identity.cleanupNonce : crypto.randomUUID().replaceAll("-", "");
  if (typeof requestId !== "string" || typeof cleanupNonce !== "string"
    || !/^[a-f0-9]{32}$/.test(requestId) || !/^[a-f0-9]{32}$/.test(cleanupNonce))
    throw new Error("Invalid MCP physical operation identity");
  const context = Object.freeze({});
  const operation: PhysicalOperation = {
    requestId,
    nonce: cleanupNonce,
    invoke,
    used: false,
  };
  physicalOperations.set(context, operation);
  return Object.freeze({
    context,
    async settle(): Promise<boolean> {
      const proof = await invoke<{ settled: boolean }>("mcp_cancel", {
        requestId: operation.requestId,
        cleanupNonce: operation.nonce,
      });
      return proof?.settled === true;
    },
  });
}

type Receipt = {
  expires: number;
  input: McpInput;
  info?: McpTool;
  error?: string;
};
const sharedReceipts = new Map<string, Receipt>();
let receiptGeneration = 0;
const connections = new WeakMap<NativeInvoke, Map<string, Promise<unknown>>>();
export function revokeMcpReceipts() {
  receiptGeneration++;
  sharedReceipts.clear();
}
export async function ensureMcpConnected(
  invoke: NativeInvoke,
  serverId: string,
) {
  let pending = connections.get(invoke);
  if (!pending) {
    pending = new Map();
    connections.set(invoke, pending);
  }
  const existing = pending.get(serverId);
  if (existing) return existing;
  const operation = invoke("mcp_connect", { serverId }).finally(() =>
    pending!.delete(serverId),
  );
  pending.set(serverId, operation);
  return operation;
}
export async function mcpCatalogue(
  invoke: NativeInvoke,
  input: {
    server_id?: string;
    query?: string;
    cursor?: number;
    limit?: number;
    include_parameters?: boolean;
  },
) {
  if (!input.server_id) {
    const [config, status] = await Promise.all([
      invoke<McpConfig>("mcp_config_get"),
      invoke<McpStatus[]>("mcp_status"),
    ]);
    return {
      servers: status
        .filter((s) => s.enabled)
        .map((s) => ({
          ...s,
          name:
            config.servers.find((server) => server.id === s.id)?.name ?? s.id,
        })),
      hint: "Choose an enabled server_id to connect and discover its tools. Credentials and disabled servers stay in Settings.",
    };
  }
  await ensureMcpConnected(invoke, input.server_id);
  return invoke("mcp_tools", {
    serverId: input.server_id,
    query: input.query ?? "",
    cursor: input.cursor ?? 0,
    limit: input.limit ?? 8,
    includeParameters: input.include_parameters ?? false,
  });
}
function canonical(input: unknown): string {
  return JSON.stringify(input, (_key, value: unknown) => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return Object.fromEntries(
        Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
      );
    }
    return value;
  });
}
/** Receipts outlive a toolset rebuild, but never cross chat sessions or changed arguments. */
export function createMcpExecutor(
  invoke: NativeInvoke,
  getSession: () => string | null,
  runRequest: typeof runCancellableProcess,
  receipts = sharedReceipts,
) {
  const key = (callId: string) => JSON.stringify([getSession(), callId]);
  return {
    revoke(callId: string): void {
      receipts.delete(key(callId));
    },
    preparation(callId: string): "ask" | "allow" | "unavailable" {
      const receipt = receipts.get(key(callId));
      if (!getSession() || !receipt || receipt.expires < Date.now() || receipt.error)
        return "unavailable";
      return receipt.info?.policy === "ask" ? "ask"
        : receipt.info?.policy === "allow" ? "allow" : "unavailable";
    },
    async prepare(input: McpInput, callId: string): Promise<boolean> {
      const session = getSession();
      const receiptKey = JSON.stringify([session, callId]);
      const generation = receiptGeneration;
      const now = Date.now();
      for (const [id, receipt] of receipts)
        if (receipt.expires < now) receipts.delete(id);
      if (receipts.size >= 64 && !receipts.has(receiptKey)) return false;
      const snapshot = structuredClone(input);
      const receipt: Receipt = { expires: now + 900000, input: snapshot };
      try {
        if (!session)
          throw new Error("An active chat session is required for MCP tools");
        const previous = receipts.get(receiptKey);
        if (previous && canonical(previous.input) !== canonical(snapshot))
          throw new Error(
            "MCP tool call ID was reused with different arguments",
          );
        await ensureMcpConnected(invoke, snapshot.server_id);
        if (getSession() !== session || receiptGeneration !== generation) return false;
        receipt.info = await invoke<McpTool>("mcp_tool_info", {
          serverId: snapshot.server_id,
          toolName: snapshot.tool_name,
        });
        if (!receipt.info.connection_generation)
          throw new Error("MCP connection is unavailable; request approval again");
        if (receipt.info.policy === "deny")
          throw new Error("MCP tool is denied by the configured policy");
      } catch (error) {
        receipt.error = String(error).slice(0, 2000);
      }
      if (getSession() !== session || receiptGeneration !== generation) return false;
      receipts.set(receiptKey, receipt);
      return !receipt.error && receipt.info?.policy === "ask";
    },
    async execute(
      input: McpInput,
      callId: string,
      signal?: AbortSignal,
      context?: unknown,
    ): Promise<unknown> {
      const receipt = receipts.get(key(callId));
      receipts.delete(key(callId));
      if (
        !receipt ||
        receipt.expires < Date.now() ||
        canonical(receipt.input) !== canonical(input)
      )
        return {
          ok: false,
          error:
            "Expired, changed or full-queue MCP action; request it again. No tool was executed.",
        };
      if (receipt.error || !receipt.info)
        return { ok: false, error: receipt.error ?? "MCP tool is unavailable" };
      try {
        const operation = context && typeof context === "object"
          ? physicalOperations.get(context) : undefined;
        if (operation && (operation.invoke !== invoke || operation.used))
          throw new Error("MCP physical operation was reused or belongs to another client");
        if (operation) operation.used = true;
        return await runRequest(
          (requestId) =>
            invoke("mcp_call_tool", {
              serverId: receipt.input.server_id,
              toolName: receipt.input.tool_name,
              arguments: receipt.input.arguments,
              requestId,
              ...(operation ? { cleanupNonce: operation.nonce } : {}),
              configRevision: receipt.info!.config_revision,
              connectionGeneration: receipt.info!.connection_generation,
              approved: receipt.info!.policy === "ask",
            }),
          (requestId) => invoke<void>("mcp_cancel", { requestId }),
          signal,
          operation ? () => operation.requestId : undefined,
        );
      } catch (error) {
        if (signal?.aborted)
          throw signal.reason instanceof Error
            ? signal.reason
            : new DOMException("MCP call aborted", "AbortError");
        return {
          ok: false,
          error: String(error).slice(0, 2000),
          hint: "Verify an uncertain outcome before retrying a mutation. Reconnect through mcp_catalog if needed.",
        };
      }
    },
  };
}

type OutputPart =
  | { type: "text"; text: string }
  | { type: "image-data"; data: string; mediaType: string };
/** Keep full output in saved chat; send a bounded text preview and supported image parts. */
export function mcpModelOutput(
  output: unknown,
  callId: string,
): { type: "content"; value: OutputPart[] } {
  const result =
    output && typeof output === "object"
      ? (output as Record<string, unknown>)
      : {};
  const parts: OutputPart[] = [];
  let remaining = 12000;
  let imageBudget = 4 * 1024 * 1024;
  const addText = (value: string) => {
    const text = value.slice(0, remaining);
    remaining -= text.length;
    if (text) parts.push({ type: "text", text });
  };
  if (result.isError === true) addText("MCP tool reported an error.\n");
  if (Array.isArray(result.content)) {
    for (const item of result.content) {
      if (!item || typeof item !== "object") continue;
      const part = item as Record<string, unknown>;
      if (part.type === "text" && typeof part.text === "string")
        addText(part.text + "\n");
      else if (
        part.type === "image" &&
        typeof part.data === "string" &&
        typeof part.mimeType === "string" &&
        /^image\/(png|jpeg|webp|gif)$/.test(part.mimeType) &&
        part.data.length <= imageBudget &&
        part.data.length > 0 &&
        part.data.length % 4 === 0 &&
        /^[A-Za-z0-9+/]*={0,2}$/.test(part.data)
      ) {
        imageBudget -= part.data.length;
        parts.push({
          type: "image-data",
          data: part.data,
          mediaType: part.mimeType,
        });
      } else
        addText(
          `[MCP ${String(part.type ?? "resource")} content is retained in the saved tool result.]\n`,
        );
    }
  }
  if (!parts.length) addText(JSON.stringify(output) ?? String(output));
  parts.push({
    type: "text",
    text: `Full structured output is saved under tool call ${callId}. Use tool_result_read with tool_call_id and offsets for omitted details; do not re-execute the action to recover its output.`,
  });
  return { type: "content", value: parts };
}
