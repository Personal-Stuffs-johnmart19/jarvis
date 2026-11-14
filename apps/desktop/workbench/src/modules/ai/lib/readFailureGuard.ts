/** Per-turn read/search budget. Never retries or replays tool execution. */
const READ_ARGUMENTS = {
  read_file: ["path", "offset", "limit"],
  list_directory: ["path"],
  grep: ["pattern", "root", "glob", "case_insensitive", "max_results"],
  glob: ["pattern", "root", "max_results"],
  jarvis_catalog: ["query", "category", "cursor", "limit"],
  mcp_catalog: ["server_id", "query", "cursor", "limit", "include_parameters"],
  utility_catalog: ["query", "category", "cursor", "limit", "available_only"],
} as const;
const READ_TOOLS = new Set(Object.keys(READ_ARGUMENTS));
type Result = { type: string; toolName?: string; input?: unknown; output?: unknown };
function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : undefined;
}
/** Match one shared alias to its actual read handler, never arbitrary alias chains. */
function readTarget(part: Result) {
  let name = part.toolName;
  let input = record(part.input);
  if (name === "jarvis_action") {
    name = typeof input?.name === "string" ? input.name : undefined;
    input = record(input?.args);
  }
  if (!name || !READ_TOOLS.has(name) || !input) return;
  const args = input;
  const catalogue = name.endsWith("_catalog");
  const required = name === "read_file" || name === "list_directory" ? "path" : "pattern";
  if (!catalogue && typeof args[required] !== "string") return;
  const integer = (key: string, minimum: number, maximum = Infinity) => args[key] === undefined
    || Number.isInteger(args[key]) && (args[key] as number) >= minimum && (args[key] as number) <= maximum;
  if (name === "read_file" && (!integer("offset", 0) || !integer("limit", 1, 10000))) return;
  if (name === "grep" || name === "glob") {
    if (args.root !== undefined && typeof args.root !== "string") return;
    if (!integer("max_results", 1, name === "grep" ? 500 : 2000)) return;
    if (name === "grep" && (args.case_insensitive !== undefined && typeof args.case_insensitive !== "boolean"
        || args.glob !== undefined && (!Array.isArray(args.glob) || !args.glob.every(value => typeof value === "string")))) return;
  }
  // The real Zod handlers strip unknown keys; they must not change lookup identity.
  const fields = READ_ARGUMENTS[name as keyof typeof READ_ARGUMENTS];
  return { name, input: Object.fromEntries(fields.filter(key => args[key] !== undefined).map(key => [key, args[key]])) };
}
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value).sort(([a],[b]) => a.localeCompare(b)).map(([key,v]) => `${JSON.stringify(key)}:${stable(v)}`).join(",")}}`;
  return JSON.stringify(value) ?? "undefined";
}
export function createReadFailureGuard() {
  const failures = new Map<string, number>();
  let consecutive = 0;
  let limited = false;
  return {
    get limited() { return limited; },
    observe(parts: readonly Result[]) {
      for (const part of parts) {
        if (!["tool-result", "tool-error"].includes(part.type)) continue;
        const target = readTarget(part);
        if (!target) continue;
        const output = part.output;
        const record = output && typeof output === "object" ? output as Record<string, unknown> : {};
        const failed = part.type === "tool-error" || record.ok === false || Boolean(record.error);
        const empty = ["entries", "hits", "paths", "matches", "items", "servers", "tools"].some(key => Array.isArray(record[key]) && record[key].length === 0);
        const unchanged = target.name === "read_file" && record.unchanged === true;
        if (!failed && !empty && !unchanged) { consecutive = 0; failures.clear(); continue; }
        consecutive++;
        const key = `${target.name}:${stable(target.input)}`;
        const count = (failures.get(key) ?? 0) + 1;
        failures.set(key, count);
        // One corrected attempt remains possible; varying failed paths is bounded too.
        if (count >= 2 || consecutive >= 4) limited = true;
      }
    },
  };
}
export const READ_FAILURE_MESSAGE = "Information lookup made no progress after repeated failed, empty or unchanged results. Tools are disabled for this final response. Answer using verified results and provided context; explain what could not be retrieved and ask one useful clarification only if needed. Do not invent current facts, claim a new read occurred or report unfinished work as complete.";
