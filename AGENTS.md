# Jarvis development

- Prefer verified Vulkan/GPU execution wherever the selected engine supports it. Keep CPU-only engines explicitly labelled and opt-in; do not silently replace a working GPU model with CPU.

- Run all development CLI commands through `tools/start_background.ps1`; do not invoke Python, pnpm, git or other native commands directly from the outer tool shell. The launcher creates its worker console hidden at creation so ordinary descendants inherit it; CreateNoWindow alone does not propagate. Build/check entrypoints self-route through this worker. Verify `.result.json` and actual window visibility with `tests/desktop/check_background_launcher.py`; a launch flag or stale result is not proof.

- Keep source searches scoped to the relevant `apps`, `local_mode`, `tests`, `benchmarks` or `docs` area. Exclude `.cache`, `.venv`, `node_modules`, `target`, `dist` and versioned EXEs unless inspecting a specific dependency/artifact.
- For shared agent/frontend changes start with `./tools/checks/agent.ps1`. See `docs/DEVELOPMENT.md` for targeted Python/Rust checks, UI QA and release packaging. Run relevant checks; do not repeat already-passing suites without a change or unresolved concern.
- Use synthetic `tests/desktop/qa_services.py` for UI/protocol coverage. Real-model smoke is deliberate and small; do not run a cloud-model test matrix. Never operate personal Chrome, media or microphone as incidental QA.
- Iterate on frontend assets with the own hidden QA WebView2. Build the final Rust host once after focused checks pass; do not modify dist while Rust embeds its assets. Verify bundled assets before publishing.
- Keep complete logs in separate `.cache/verification` run directories. Print short summaries; inspect only relevant failure excerpts. Preserve failed evidence and record retries separately.
- Cloud/local models use the same capability catalogue, handlers and approvals. Preserve call/result/approval linkage and original saved chat when reducing model context. Do not repeat side effects merely to recover output.
- Keep English source UI strings and separate locale files. Avoid unrelated formatting or generated-file churn.
- Stage only the manifest-selected final root `desktop-bin/jarvis-desktop-<sha256>.exe`. Never use a recursive `*.exe` cleanup: companion executables are separate artifacts.

## Automated development tools
- Use the configured external apps instead of repeating broad manual analysis: Codacy Analysis CLI (`pnpm --dir tools/dev run quality`) scans staged source files with the managed Opengrep engine (stage intended files first; a zero-file run is not verification of unstaged changes); GitHub Source quality runs offline unit/TypeScript checks and incremental security scans.
- CodeRabbit 0.9.0 is installed and authenticated. Use one scoped background review for a substantial change, consume its structured findings, and fix actionable defects. Never pass `--use-credits`, start paid upgrades, or wait idly for the review. Do not repeatedly review unchanged code.
- Optional detailed linting: `pnpm --dir tools/dev run lint`. The generated policy focuses correctness/security/performance; the obsolete React-in-JSX-scope rule is excluded because this project uses the automatic JSX runtime. Do not suppress actual findings just to make checks green.
- Keep full native rebuild/bundled UI runs for accepted release checkpoints or changes needing native verification; use the existing served UI loop for frontend iteration. Do not rerun unrelated checks on documentation/tooling-only edits.

- Project development Node is pinned in `.node-version`. Dot-source `tools/use_node.ps1` in Windows tool sessions; build/check scripts do this automatically. Never overwrite the Codex-managed runtime.

## Release batch fast path
- Follow docs/DEVELOPMENT.md: bounded local changes, one integration owner and coherent PR/release batch, one final portable build after focused source checks.
- Prefer GitHub CI and CodeRabbit PR reviews for repeated rechecks. Inspect outside-diff comments and reviewed commit coverage. Use the local reviewer only when remote review is unavailable or a specific unresolved concern justifies it.



## Public source and commits
- Keep the source/test/tooling boundaries in CONTRIBUTING.md. Never track credential stores, private task records or model weights.
- Every Codex-assisted implementation commit includes the exact trailer: Co-authored-by: codex <noreply@openai.com>. Preserve upstream copyright and notices.
