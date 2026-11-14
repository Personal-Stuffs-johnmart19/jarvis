import { revokeMcpReceipts } from "../../mcp/client";
import { isQueuedReadOnly, queuedReadPaths } from "./queuedReadOnly";
import { isQueuedUtility, isQueuedMcp, revokeQueuedMcp, registerQueuedGateway } from "./queuedDispatch";
import { cataloguePage } from "./catalogue";
import { browserObservation } from "../lib/browserObservation";
import { asSchema, tool, type ToolSet, type JSONValue } from "ai";
import { z } from "zod";
import { invoke } from "@/host/core";
import { readOnlyRequest } from "../lib/readOnlyRequest";
const rpc = <T>(method: string, params: object = {}) =>
  invoke<T>("backend", { method, params });
type Descriptor = {
  name: string;
  description: string;
  parameters: unknown;
  category: string;
  executor: string;
  approval: string;
  available: boolean;
};
type Pending = { expires: number; name: string } & (
  | { kind: "backend"; token: string }
  | { kind: "invalid"; error: string }
  | {
      kind: "local";
      input: unknown;
      action: ToolSet[string];
      required: boolean;
    }
);
const pending = new Map<string, Pending>();
let published = "";
let catalog: Promise<Descriptor[]> | undefined;
export function revokePendingActions() {
  pending.clear();
  revokeMcpReceipts();
}
/** Remove one independent UI receipt without revoking approvals in the chat. */
export function revokePendingAction(toolCallId: string) {
  const receipt=pending.get(toolCallId);
  if(receipt?.kind==="local")revokeQueuedMcp(receipt.action,toolCallId);
  pending.delete(toolCallId);
}
function remoteCatalog() {
  if (catalog) return catalog;
  const current = (async () => {
      const items: Descriptor[] = [];
      let cursor: number | null = 0;
      while (cursor !== null && items.length < 500) {
        const page: { items: Descriptor[]; next_cursor: number | null } =
          await rpc<{ items: Descriptor[]; next_cursor: number | null }>(
            "services.catalog",
            { cursor, limit: 100 },
          );
        items.push(...page.items);
        cursor = page.next_cursor;
      }
      return items;
    })();
  catalog = current;
  void current.catch(() => { if (catalog === current) catalog = undefined; });
  return current;
}
export function buildJarvisTools(local: ToolSet = {}, publishWorkspace = true) {
  // Admission metadata and canonical execution share one immutable name binding.
  local = Object.freeze({...local});
  const nativeCatalog = Object.entries(local).map(([name, action]) => ({
    name,
    description: typeof action.description === "string" ? action.description : name,
    parameters: (() => {
      // These constructors use synchronous Zod schemas. Share the SDK's input
      // conversion, including defaults and object-property constraints.
      const schema = asSchema(action.inputSchema).jsonSchema;
      if (schema && typeof schema === "object" && "then" in schema && typeof schema.then === "function")
        throw new Error(`Workspace schema is asynchronous: ${name}`);
      return schema;
    })(),
    category: "workspace",
    executor: "native-host",
    approval: typeof action.needsApproval === "function" ? "dynamic" : action.needsApproval ? "required" : "none",
    available: true,
    ...(isQueuedReadOnly(action) ? { queue_read_only: true, queue_path_fields: queuedReadPaths(action) } : {}),
    ...(isQueuedUtility(action) ? { queue_dispatch: "utility", queue_path_fields: ["cwd"] } : {}),
    ...(name === "mcp_call" && isQueuedMcp(action) ? { queue_dispatch: "mcp", queue_path_fields: [] } : {}),
  }));
  const signature = JSON.stringify(nativeCatalog);
  if (publishWorkspace && signature !== published) {
    published = signature;
    void rpc("services.register_workspace", { items: nativeCatalog }).catch(
      (error) => {
        published = "";
        console.error(error);
      },
    );
  }
  return {
    jarvis_catalog: tool({
      description:
        "Discover browser_control for Chrome/YouTube/music, documents for uploaded files, and all other Jarvis services/workspace tools. Search by name/category; paginate with next_cursor. Broad searches return summaries; query an exact name for its full schema. No credentials. Call only names in the current callable tool list directly; use jarvis_action for other discovered entries.",
      inputSchema: z.object({
        query: z.string().max(100).default(""),
        category: z.string().max(64).default(""),
        cursor: z.number().int().min(0).default(0),
        limit: z.number().int().min(1).max(20).default(8),
      }),
      execute: async ({ query, category, cursor, limit }, options) => {
        const local = nativeCatalog.find(item => item.name === query.trim() && (!category || item.category === category));
        let remote: Descriptor[] = [];
        if (!local) {
          const current = remoteCatalog();
          try { remote = await readOnlyRequest(() => current, options.abortSignal); }
          catch (error) { if (options.abortSignal?.aborted && catalog === current) catalog = undefined; throw error; }
        }
        return cataloguePage(remote, nativeCatalog, {
          query,
          category,
          cursor,
          limit,
        });
      },
    }),
    jarvis_action: registerQueuedGateway(tool({
      toModelOutput: async ({input, output, toolCallId}) => {
        if (input.name === "browser_control")
          return {type: "json", value: (browserObservation(output, toolCallId) ?? null) as JSONValue};
        const convert = local[input.name]?.toModelOutput;
        if (convert) return await convert({input: input.args, output, toolCallId});
        return {type: "json", value: (output ?? null) as JSONValue};
      },
      description:
        "Execute any discovered Jarvis capability through the same handler, schema and approval policy as the GUI/direct workspace tool. Never supply tokens or approval fields.",
      inputSchema: z.object({
        name: z.string().max(128),
        args: z.record(z.string(), z.unknown()),
      }),
      needsApproval: async ({ name, args }, options) => {
        const now = Date.now();
        for (const [id, item] of pending)
          if (item.expires < now) pending.delete(id);
        if (pending.size >= 64) throw new Error("Too many pending actions");
        try {
          const action = local[name];
          if (action) {
            const input = (action.inputSchema as z.ZodType).parse(args);
            const required =
              typeof action.needsApproval === "function"
                ? await action.needsApproval(input, options)
                : Boolean(action.needsApproval);
            pending.set(options.toolCallId, {
              kind: "local",
              expires: now + 900000,
              name,
              input: structuredClone(input),
              action,
              required,
            });
            return required;
          }
          const receipt = await rpc<{
            token: string;
            requires_approval: boolean;
          }>("agent.prepare", { name, args });
          pending.set(options.toolCallId, {
            kind: "backend",
            expires: now + 900000,
            name,
            token: receipt.token,
          });
          return receipt.requires_approval;
        } catch (error) {
          // Invalid model arguments must be returned as tool feedback, never executed.
          pending.set(options.toolCallId, {
            kind: "invalid",
            expires: now + 900000,
            name,
            error: String(error).slice(0, 2000),
          });
          return false;
        }
      },
      execute: async ({ name }, options) => {
        const receipt = pending.get(options.toolCallId);
        pending.delete(options.toolCallId);
        if (!receipt || receipt.name !== name || receipt.expires < Date.now())
          throw new Error("Expired action; request it again");
        if (receipt.kind === "invalid")
          return {
            error: receipt.error,
            hint: "Use jarvis_catalog to discover the exact name and parameters, then request the action again. No action was executed.",
          };
        if (receipt.kind === "backend")
          return rpc("agent.execute", { token: receipt.token });
        if (
          !receipt.required &&
          !isQueuedMcp(receipt.action) &&
          typeof receipt.action.needsApproval === "function" &&
          (await receipt.action.needsApproval(receipt.input, options))
        )
          throw new Error("Action risk changed; request approval again");
        if (!receipt.action.execute)
          throw new Error("Capability has no executor");
        return receipt.action.execute(receipt.input, options);
      },
    }),local),
  };
}
