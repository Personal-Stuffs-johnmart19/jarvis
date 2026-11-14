import type { McpConfig, McpPolicy, McpServer, McpStatus, SecretRef } from "./types";
export function canStopMcpServer(id: string, busy: string, status?: McpStatus): boolean {
  return (!busy || busy === id) && !!(status?.connected || status?.connecting);
}
export const MCP_SECRET_SERVICE = "app.jarvis.workbench";
export type SecretDraft = {
  name: string;
  value: string;
  reference?: SecretRef;
};
export type ServerDraft = {
  id: string;
  name: string;
  type: "stdio" | "http";
  command: string;
  args: string;
  cwd: string;
  url: string;
  enabled: boolean;
  policy: McpPolicy;
  policies: string;
  secrets: SecretDraft[];
};
export function serverDraft(server?: McpServer): ServerDraft {
  const transport = server?.transport;
  const refs =
    transport?.type === "http"
      ? transport.secret_headers
      : transport?.secret_env;
  return {
    id: server?.id ?? `server-${crypto.randomUUID().slice(0, 8)}`,
    name: server?.name ?? "",
    type: transport?.type ?? "stdio",
    command: transport?.type === "stdio" ? transport.command : "",
    args: transport?.type === "stdio" ? JSON.stringify(transport.args) : "[]",
    cwd: transport?.type === "stdio" ? (transport.cwd ?? "") : "",
    url:
      transport?.type === "http" ? transport.url : "http://127.0.0.1:8000/mcp",
    enabled: server?.enabled ?? false,
    policy: server?.default_policy ?? "ask",
    policies: JSON.stringify(server?.tool_policies ?? {}, null, 2),
    secrets: Object.entries(refs ?? {}).map(([name, reference]) => ({
      name,
      value: "",
      reference,
    })),
  };
}
export function compileServer(draft: ServerDraft) {
  const policies: unknown = JSON.parse(draft.policies || "{}");
  if (
    !policies ||
    typeof policies !== "object" ||
    Array.isArray(policies) ||
    Object.values(policies).some(
      (v) => !["ask", "allow", "deny"].includes(String(v)),
    )
  )
    throw new Error(
      "Tool policies must be a JSON object with ask, allow or deny values.",
    );
  let args: unknown = [];
  if (draft.type === "stdio") {
    args = JSON.parse(draft.args || "[]");
    if (!Array.isArray(args) || args.some((arg) => typeof arg !== "string"))
      throw new Error("Arguments must be a JSON array of strings.");
  }
  const references: Record<string, SecretRef> = Object.create(null);
  const writes: { service: string; account: string; value: string }[] = [];
  for (const row of draft.secrets) {
    const name =
      draft.type === "http" ? row.name.trim().toLowerCase() : row.name.trim();
    if (!name || Object.hasOwn(references, name))
      throw new Error("Credential names must be non-empty and unique.");
    if (row.value) {
      // Editing an imported provider reference creates an MCP-owned credential;
      // it must never overwrite the provider's original keyring entry.
      const reference = {
        service: MCP_SECRET_SERVICE,
        account: `mcp:${draft.id}:${draft.type}:${name}`,
      };
      references[name] = reference;
      writes.push({ ...reference, value: row.value });
    } else if (row.reference) references[name] = row.reference;
    else
      throw new Error(
        `Enter a credential value for ${name} or remove that row.`,
      );
  }
  const server: McpServer = {
    id: draft.id,
    name: draft.name.trim() || null,
    enabled: draft.enabled,
    default_policy: draft.policy,
    tool_policies: policies as Record<string, McpPolicy>,
    transport:
      draft.type === "stdio"
        ? {
            type: "stdio",
            command: draft.command.trim(),
            args: args as string[],
            cwd: draft.cwd.trim() || null,
            secret_env: references,
          }
        : { type: "http", url: draft.url.trim(), secret_headers: references },
  };
  return { server, writes };
}
export function replaceServer(config: McpConfig, server: McpServer): McpConfig {
  return {
    ...config,
    servers: [
      ...config.servers.filter((item) => item.id !== server.id),
      server,
    ],
  };
}
