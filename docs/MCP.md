# Model Context Protocol (MCP) Configuration & Policy

## Overview
Jarvis provides native persistence, security validation, and policy enforcement for Model Context Protocol (MCP) servers stored in `mcp-servers.json`.

Transports and tool invocations adhere to strict boundary rules:
- **Zero Raw Secret Persistence**: Authentication tokens and passwords must reside in system keyring references (`KeyringSecretRef`).
- **Policy Precedence**:
  1. Server `enabled == false` &rarr; `Deny` (always rejects execution).
  2. Per-tool explicit policy override (`Allow`, `Ask`, `Deny`).
  3. Server default policy (`Ask`, `Allow`, `Deny`).
  *Note*: Malicious or permissive tool annotations cannot downgrade policy below configured restrictions.
- **Transport Validation**:
  - `stdio`: Executable and argument limits. Shell metacharacters and concatenation are prohibited. Configured working directories (`cwd`) must be absolute directories and cannot be symlinks or junctions.
  - `http`: HTTPS is mandatory except for loopback addresses (`localhost`, `127.0.0.1`, `::1`). Embedded credentials (`user:pass@`) and secret query parameters are strictly rejected.
- **Storage & Integrity**:
  - `mcp-servers.json` atomic file writes using sibling temp files and syncs.
  - Symlinks and junctions are refused on both load and write.
  - Size capped at 1 MiB (`MAX_CONFIG_FILE_BYTES`) and max 128 servers.
  - Imports disable servers by default (`enabled: false`) to avoid unreviewed execution.

## Integration Status
- **Implemented**: Native configuration schema, validation, policy resolution, and atomic disk persistence.
- **Implemented**: Official pinned `rmcp` client transports for owned stdio processes and Streamable HTTP (JSON/SSE), native keyring credential resolution, paginated tool discovery, bounded calls, per-tool policies and configuration revision checks.
- **Implemented**: Shared Settings → Integrations supports adding/editing stdio and HTTP servers, disabled imports, keyring credentials, connect/disconnect, paged discovery and per-tool policies. The common agent catalogue exposes `mcp_catalog` and `jarvis_action` → `mcp_call` in every shared chat.
- Approval receipts bind session, call ID, exact arguments and configuration revision. Stop uses the common cancellation path. Full results remain saved; model projections bound text and aggregate image payloads without replaying actions.
- Offline JavaScript checks cover the real tool factory and common action wrapper, receipts, cancellation, settings parsing and credential isolation. Live remote-server compatibility and packaged UI verification remain separate release checks.

## Lifecycle and verification
- Connect/disconnect, calls and cancellation are native commands. Network work happens outside the shared manager mutex; independent servers can run concurrently.
- Windows stdio servers use Job Objects; Unix servers use process groups. Disconnect, app exit, dropped handshakes/calls, timeout and Stop terminate owned stdio descendants. Window reload/destruction cancels calls belonging to that window.
- HTTP session-recovery replay is disabled. Cancellation/timeouts report an unknown outcome rather than retrying mutations. Remote HTTP operations cannot be forcibly terminated like owned local processes; verify an uncertain outcome before retrying.
- Importing a configuration does not start its servers. Credentials are resolved from Jarvis-owned keyring references, never returned in tool metadata or stored as raw values in configuration.
- Offline native fixtures cover authenticated stateless HTTP, stdio discovery, approvals, process-tree cleanup, dropped futures, timeouts, scoped cancellation, reconnect and independent-server concurrency. Fixtures use local Python and loopback only, without a model or external API.
