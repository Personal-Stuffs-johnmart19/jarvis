# Architecture

`START.cmd` → `launch_local.py` → manifest-selected Tauri desktop → private stdio bridge → `local_mode.desktop_host`.

The desktop shell and embedded Files & Tools surface are React/TypeScript. Rust owns native windows, process trees, filesystem/PTY adapters, MCP transports, utility execution and managed downloads. Python owns application services, model/audio adapters, document extraction and persisted document data. There is no parallel Tk desktop.

| Area | Source |
| --- | --- |
| Main shell, shared settings and locales | `apps/desktop/src/` |
| Embedded chat, editor, files, terminal and agents | `apps/desktop/workbench/src/` |
| Native bridge, windows and application shutdown | `apps/desktop/src-tauri/src/` |
| Native utilities, model download and MCP modules | `apps/desktop/src-tauri/workbench-core/src/` |
| Shared service catalogue and desktop protocol | `local_mode/services.py`, `desktop_host.py` |
| Managed model identities and runtime selection | `local_mode/managed_chat.py`, `managed_rpc.py`, `local_chat.py` |
| Uploaded documents and source snapshots | `local_mode/document_store.py`, `rag_sources.py`, `terminal_sources.py` |
| Speech and voice controls | `local_mode/voice.py`, `voice_settings.py`, `tts_controls.py`, audio adapters |
| Browser/profile/media operations | `local_mode/browser_api.py`, `chrome_mcp.py`, `browser_profiles.py`, `media_player.py` |

## Shared execution

Cloud/local chats use a common capability catalogue and approval/result linkage. Filesystem location, selected model and persisted tool outcomes must remain consistent between surfaces. User/page/file content is untrusted input, never authorization to bypass policy. A model cannot confirm its own pending action.

Model retries do not authorize replaying side effects. Interrupted calls retain explicit unknown/interrupted outcomes so a subsequent turn can recover without silently repeating a mutation. Large older tool results can be compacted in outgoing context and recovered with `tool_result_read`; stored history, user inputs, IDs and approval states remain intact.

Foreground shell/utility/code commands own a Windows Job Object or Unix process group. Stop, timeout, wrapper exit and application shutdown clean up owned descendants. Active background terminal sessions remain alive until stopped. Cleanup never kills unrelated processes by executable name. This lifecycle management is not a security sandbox.

## State

Settings and user data live under `data/`; test data uses isolated `.cache/desktop-test` locations. Native endpoint/MCP secrets use keyring references. Uploaded text/index data uses `data/documents.sqlite`, with source copies retained separately. Model manifests bind installed weights to pinned identities. These are separate physical stores accessed through shared services; do not create a competing per-chat data store.

[Files](FILES.md), [models](MODELS.md) and [MCP](MCP.md) describe their specific contracts. Remaining provider/platform limitations belong in the [backlog](ECOSYSTEM_ROADMAP.md).

The shell, workspace and settings use one persisted shortcut registry. The trusted host routes keyboard events only between its own same-origin documents; editor/terminal bindings retain their focus scope, shortcut recording can consume keys, and page/document teardown releases handlers. Settings sections use validated navigation messages in a persistent iframe rather than reloading its URL. Cached theme colors and mode are applied before React; asynchronous preference/custom-theme hydration must not replace them with a temporary default. Confirmation UI belongs to Jarvis and cancels on Escape, unload or stale ownership instead of exposing framework-origin browser dialogs.

## Repository boundaries

The [contributor guide](../CONTRIBUTING.md) maps source, test, provisioning, dependency and release directories. Backend imports deliberately retain their checkout layout for portable startup. Python regressions live in tests/python, Node contracts in tests/agent, explicit desktop probes in tests/desktop, and measurements in benchmarks. Provisioning helpers are modules under tools/setup; setup.py is not used as an installer or packaging hook.
