import { buildMcpTools } from "./mcp";
import { buildUtilityTools } from "./utilities";
import { buildDataServices } from "./dataServices";
import { buildJarvisTools } from "./jarvis";
import { buildBrowserTools } from "./browser";
import { buildWeatherTools } from "./weather";
import { buildManagedAgentTools } from "./agent";
import { buildEditTools } from "./edit";
import { buildFsTools } from "./fs";
import { buildSearchTools } from "./search";
import { buildShellTools } from "./shell";
import { buildSubagentTools } from "./subagent";
import { buildTerminalTools } from "./terminal";
import { buildTodoTools } from "./todo";

export { resolvePath, type ToolContext } from "./context";

/**
 * AI tool definitions.
 *
 * Approval policy:
 *  - Read-only tools (`read_file`, `list_directory`, `grep`, `glob`)
 *    auto-execute, but go through the security guard which refuses obvious
 *    secret paths (.env*, .ssh/, credentials, etc.).
 *  - Mutating tools (`write_file`, `edit`, `multi_edit`, `create_directory`,
 *    `run_command`) require explicit user approval — the AI SDK pauses on
 *    tool-call and surfaces a `tool-approval-request` part that the UI
 *    renders as a confirmation card.
 *  - `edit` / `multi_edit` additionally enforce a read-before-edit invariant
 *    (the model must have called read_file on the path earlier in the
 *    session).
 *
 * The model sees absolute paths only after they are resolved against the
 * active terminal's cwd (provided via `getCwd`); it should not invent paths
 * outside that.
 */
export function buildTools(ctx: import("./context").ToolContext, child = false): import("ai").ToolSet {
  const nativeTools = {
    ...buildDataServices(ctx),
    ...buildFsTools(ctx),
    ...buildEditTools(ctx),
    ...buildSearchTools(ctx),
    ...buildShellTools(ctx),
    ...(child ? {} : buildSubagentTools(ctx, (childContext) => buildTools(childContext, true))),
    ...buildTerminalTools(ctx),
    ...buildTodoTools(ctx),
    ...buildManagedAgentTools(ctx),
  } as const;
  // Keep the always-sent schema set small enough for local model contexts.
  // Every other handler remains available by exact name through the shared catalogue.
  const utilities = buildUtilityTools(ctx);
  const mcp = buildMcpTools(ctx);
  const catalogueTools = { ...nativeTools, ...utilities, ...mcp };
  const shared = buildJarvisTools(catalogueTools, !child);
  return {
    read_file: nativeTools.read_file,
    list_directory: nativeTools.list_directory,
    grep: nativeTools.grep,
    glob: nativeTools.glob,
    data_query: nativeTools.data_query,
    conversation_read: nativeTools.conversation_read,
    mcp_catalog: mcp.mcp_catalog,
    utility_catalog: utilities.utility_catalog,
    ...(child ? {} : { utility_run: utilities.utility_run, run_code: utilities.run_code }),
    ...shared,
    ...buildWeatherTools(shared.jarvis_action),
    ...(child ? {} : buildBrowserTools(shared.jarvis_action)),
  };
}
