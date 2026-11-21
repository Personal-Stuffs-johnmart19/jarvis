# Verification and release boundaries

Updated 2026-11-21. Evidence applies to the named source, artifact and exercised paths. See [Development](DEVELOPMENT.md) for focused checks and [the roadmap](ECOSYSTEM_ROADMAP.md) for remaining work.

## Selected Windows portable

- Build source: `bd613cf39fa5b5b7e8b085e91fc63ffe5c80a6bc`; `apps/desktop/` tree: `3ebd4ec28beac189310a0c12d1e1239ddc7cd075`.
- Portable: `jarvis-desktop-327b7dcd8d90c4897ab8da5da44650ad4a29a4df37fe0391144120e2384895e2.exe` (27,313,152 bytes).
- SHA-256: `327b7dcd8d90c4897ab8da5da44650ad4a29a4df37fe0391144120e2384895e2`.
- One optimized build completed in 390.6 seconds (`./tools/checks/agent.ps1 -Build`). Subsequent QA fixture and release-document changes do not change embedded application inputs.
- The manifest selects the latest portable and retires obsolete root `jarvis-desktop-0b7374c7a8dc561ee4c6b9317998f23dd284c0478a75facb315acf0891484b0d.exe`. The required terminal companion remains `desktop-bin/companions/terax/2e2471fb833e097d/terax-cli.exe`, SHA-256 `2e2471fb833e097d0edd8f0dc525f1dcd02ed0dc3254110acbbb5a871bd8ed51`, with its upstream notices. Python packages, engines and model weights remain separate.

## Full-codebase audit and bundled QA verification

- **Native Rust host & workbench-core**: `jarvis-desktop` tests passed (22/22, 0 warnings); `jarvis-workbench-core` offline library tests passed 1,072/1,074 (only 2 non-elevated Windows environment/timing cases: `authorize_spawn_cwd_blocks_symlink_escape` OS 1314 privilege error and `timeout_cleans_up_handshake_and_active_call_without_replaying` fixture `pids.json` timing).
- **Desktop frontend & agent contracts**: `./tools/checks/agent.ps1` passed (`agent-unit` + `tsc --noEmit`), Vite production build passed, and `tools/audit_branding.mjs` reported 0 findings.
- **Python services & tools**: full `pytest` suite passed (1,735 tests, including 14 new audit regressions in `tests/python/test_audit_fixes.py`), and `local_mode.capabilities.report()["ok"]` is `True`.
- **Bundled portable UI QA**: `tests/desktop/qa_services.py --bundled-assets` passed end-to-end (exit 0, 0 page errors) against the manifest-selected portable EXE, covering native hardware/disk preflight, Ollama/LM Studio/managed model pickers, embedded autostart/shortcuts/settings persistence, WSL canonical reads, workspace/terminal RAG import & refresh, read-failure loop bounding, subagent & process-tree cancellation (`python`, `node`, `npm`), MCP stdio settings/approval/shutdown, local model unload, Russian/English live locale reload, and synthetic tool protocol recovery.
- **Security & tree hygiene**: managed staged Codacy/Opengrep scans + `tools/ci/check-security-review.mjs` reported 0 unreviewed findings across all staged source changes (5 exact audited native findings intact); `tools/ci/check-public-tree.mjs` and `git diff --check` passed.

## Limits

Bundled UI QA exercised the synthetic local OpenAI-compatible/Ollama/managed-catalogue fixtures and owned MCP stdio server; no real cloud model API calls, live providers, personal Chrome/media/microphone operations, OCR hardware runs, or fresh network installations were used. Two non-elevated Windows unit tests in `jarvis-workbench-core` require symlink privilege or dedicated timing isolation. Configured integrations and downloaded model weights remain separate and require their own bounded evidence; [Speech](SPEECH.md) owns CPU/Vulkan and model-asset limits.

Synchronous network cancellation remains cooperative; remote MCP cancellation does not promise rollback. URL validation does not pin transport DNS/proxy destinations, and filesystem mutations are not a complete capability sandbox. Other operating systems require separate native GUI/lifecycle verification. Keep private credentials, chats, models and user data outside the public tree; follow [Security](../SECURITY.md).
