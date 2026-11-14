export type McpPolicy = "ask" | "allow" | "deny";
export type SecretRef = { service: string; account: string };
export type McpServer = {
  id: string;
  name?: string | null;
  enabled: boolean;
  default_policy: McpPolicy;
  tool_policies?: Record<string, McpPolicy>;
  transport:
    | {
        type: "stdio";
        command: string;
        args: string[];
        cwd?: string | null;
        secret_env?: Record<string, SecretRef>;
      }
    | { type: "http"; url: string; secret_headers?: Record<string, SecretRef> };
};
export type McpConfig = { version: number; servers: McpServer[] };
export type McpStatus = {
  id: string;
  name?: string | null;
  enabled: boolean;
  connected: boolean;
  connecting: boolean;
  tool_count: number;
};
export type McpTool = {
  server_id: string;
  name: string;
  description: string;
  input_schema: unknown;
  policy: McpPolicy;
  config_revision: string;
  connection_generation: string;
};
export type NativeInvoke = <T>(
  command: string,
  args?: Record<string, unknown>,
) => Promise<T>;
export type McpInput = {
  server_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
};
