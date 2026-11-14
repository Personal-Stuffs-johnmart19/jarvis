# Terax AI attribution

Source: https://github.com/crynta/terax-ai
Embedded source and terminal CLI revision: f7bee29d8690aa0afe37ed8b4c94daeb5b0211ad
License: Apache-2.0 (included). Upstream copyright notices are retained.

Jarvis compiles adapted Terax frontend/native modules into its main desktop and
distributes the matching terminal control CLI with the Apache license.
`apps/desktop/workbench/SOURCE.json` records source identity;
`integrations/build_companions.ps1` builds the CLI from the embedded Rust source.
The retired standalone compatibility window and its older patch are no longer
distributed.

The earlier small manual workspace also adapts portable-pty session architecture.
`apps/desktop/src-tauri/src/job.rs` derives from upstream
`src-tauri/src/modules/proc/job.rs`; its source header identifies Jarvis changes.

## Main-window integration

Pinned frontend modules in `apps/desktop/workbench` and native modules in
`apps/desktop/src-tauri/workbench-core` compile into Jarvis itself. Adaptations
retain the Jarvis host IPC bridge, shared model/settings services, localization,
tool approvals, process ownership and safe shutdown. Upstream test-only frontend
files and standalone application bootstrap are excluded; Jarvis owns those
entrypoints and its offline regression suite. Original Apache-2.0 notices remain
in both source trees, including the imported CLI and terminal symbol fonts.

See `apps/desktop/workbench/README.md` for architecture and limits. The separately
verified `terax-cli.exe` uses the current embedded source for terminal hooks; its
exact revision and checksum are recorded in `integrations/companions.json`.
Terminal dependency licenses remain with the embedded frontend/native sources.
The former portable's exact [Ghostty Web](terminal/ghostty-web.txt),
[Ghostty](terminal/ghostty.txt), [restty](terminal/restty.txt) and
[xterm.js](terminal/xterm-js.txt) notices are preserved here with their copyright
text. Retiring the standalone GUI does not remove these notices from distribution.
