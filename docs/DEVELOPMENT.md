# Development

## Setup and scope

TypeScript checks reject unused locals and parameters across the shell and embedded workspace. Remove dead imports/helpers rather than adding suppressions.

Use the Node version in `.node-version`; dot-source `tools/use_node.ps1` in PowerShell. Do not replace the Codex-managed runtime. Install the repository's locked dependencies in both `apps/desktop` and `tools/dev` rather than upgrading packages incidentally. The locale audit uses a development-only pinned parser; it is not bundled with the app. Python runtime locks and Cargo native-link constraints remain authoritative.

For a coherent change, keep one integration owner and one branch/PR. Work locally in bounded steps with explicit acceptance criteria and focused checks. Inspect results before integrating changes; preserve unrelated work.

## Background execution

Use PowerShell 7 and `tools/start_background.ps1` for every native development command. Its hidden-at-creation worker console is inherited by nested Python, Node, pnpm/cmd and compiler processes. The build/check entrypoints detect a missing or visible console and re-enter that worker before running native tools. No-window flags on only a parent do not protect grandchildren. The optional analyzer updater is disabled through CODACY_DISABLE_UPDATE_CHECK because it otherwise spawns a detached Node process. The focused Windows regression monitors real visible console windows and argument boundaries, including a venv launcher and a child started without windowsHide. Its dedicated CI runs only when launcher entrypoints change.

## Checks

| Change | Check |
| --- | --- |
| Shared agent/frontend | `./tools/checks/agent.ps1` (offline contracts and TypeScript) |
| UI/IPC behavior | `./tools/checks/agent.ps1 -UI` (own hidden WebView2, synthetic localhost service) |
| Python services | Focused pytest first; `-FullPython` for an affected full Python gate |
| Native handler | Focused Cargo tests with the project toolchain |
| Accepted portable release | `./tools/checks/agent.ps1 -Build -UI`, once after focused checks pass |
| Documentation | Local Markdown links, stale-reference scan and `git diff --check` |

Retain full logs under a timestamped `.cache/verification` directory. Inspect short failure excerpts and rerun only affected checks after a relevant change. Do not count denied commands, zero-file scans, skipped checks or aborted scenarios as passes. Do not weaken tests/constraints to meet a deadline.

Tests use synthetic providers by default. Deliberate real-model smokes are small and separate; no cloud matrices, personal browser/media/microphone operations or multi-gigabyte downloads during scaffolding.

## Automated review

GitHub source-quality and Python workflows run their configured offline checks. CodeRabbit PR reviews supplement these checks; a success status or coverage summary is not approval. Read actionable inline and outside-diff findings against the reviewed SHA, then fix them without repeated unchanged review loops.

`pnpm --dir tools/dev run quality` scans staged source with the managed security engine. Stage intended files and inspect nonzero file/tool counts. `lint` exposes detailed findings; `review` uses the configured local reviewer. Do not opt into paid credits/upgrades. Read package scripts for exact tool versions instead of relying on a historical installation report.

`tools/get_review_checkpoint.ps1 -SourceCommit <full-sha>` reads existing review/check evidence; it does not request a new review. Distinguish review coverage, approval, CI, source verification and binary provenance.

## Release

Finish the staged native/source security scan before packaging. Freeze frontend assets during native embedding. Build once per accepted batch, verify the bundled UI and publish only the manifest-selected root executable. Companion binaries and user model/data files are separate. A newer source commit does not imply the published EXE includes it. Record current source/hash/scope in [verification](VERIFICATION_CURRENT.md), not another append-only audit diary.

## Ordinary chat acceptance

The default Assistant handles everyday conversation as well as workspace tasks; explicitly saved personas remain selected. Ordinary tool activity is one collapsed disclosure. Answers, approvals, failures and denials stay visible. A catalogue lookup alone is not a completed answer.

Weather uses a direct alias over the canonical service policy and handler. The owned worker reads bounded Open-Meteo geocoding/current/today data and returns source URLs, timestamps and units; it does not launch a browser or infer conditions. Ambiguous or unavailable locations produce an honest failure.

The Gemini bridge opens SSE immediately, returns Google-compatible error envelopes, and makes one selected-model SDK attempt without hidden model fallback or Tenacity retries. Its first meaningful response is bounded to 60 seconds; heartbeat comments do not count as model text. Cancellation signals the SDK and transport, prevents subsequent route retries, and keeps capacity occupied until the worker actually exits. An in-progress synchronous network read remains cooperative. Gemini 3.8/3.6 use documented low thinking for interactive latency; Flash-Lite retains its provider default ([Google model guidance](https://aistudio.google.com/docs/latest-model)).

Acceptance must include an ordinary UI conversation with a useful final response after a real tool result and a persisted follow-up, with the selected model identified. Queued/synthetic tool tests or a greeting on a different model cannot prove that path. Keep deliberate live smokes small, distinguish started requests from completed usage entries, and retain timeout/failed-run evidence. All other regressions run offline.
## Dependency audit overrides

pnpm 11 reads overrides from each project `pnpm-workspace.yaml`, not a package.json `pnpm` field. The unused shadcn generator dependency is removed; its exact MIT stylesheet and upstream notice remain local. Do not restore its obsolete body-parser override. Keep scoped Codacy notifier, minimatch and XML adapter patches until their parents resolve patched versions. The actual matcher/filter and four parser constructors have compatibility fixtures; an audit score alone does not justify arbitrary major upgrades. Audit both changed graphs and retain the Node/pnpm pins. CI audits changed Node graphs.

## Compatibility holds

SymPy 1.14 requires mpmath `<1.4`; retain the compatible lock. Google GenAI 2.32 requires websockets `<17`; retain that bound until its parent supports a newer major. Playwright 1.65 no longer depends on pyee, so its previous compatibility hold is retired. Google SDK/Tauri transitive bounds also constrain upgrades. Dependabot exclusions are compatibility holds, not an instruction to bypass a failed resolver. Revisit exclusions when parent bounds change. Python CI resolves both runtime lockfiles; native graph changes need the corresponding Cargo resolution checks.

## Maintenance boundaries

Before removing code, trace static imports, runtime registrations, build aliases, entrypoints and tests. Dynamic action dispatch, service worker subprocesses and optional integrations may have no direct import. Preserve public compatibility paths still used by shipped artifacts, upstream licenses and provenance. Keep generated caches, downloaded models and private user data outside source cleanup.

Delete tests only with the retired behavior they cover or proven duplicate coverage. Keep failure, authorization, cancellation, process ownership and data-preservation regressions. Test names should describe the current subsystem rather than an obsolete migration. Machine-specific benchmark result snapshots belong in local verification logs; maintained benchmark runners and synthetic fixtures belong in source.

Use the canonical guides in `docs/`; secondary package readmes should explain their boundary and link to those guides instead of duplicating changing feature lists.

For old portable cleanup, validate `desktop-bin/manifest.json` and its checksum first. Remove only obsolete root versioned EXEs after checking running executable paths; never recurse over `*.exe`. Companion binaries are separate manifest-pinned dependencies. Remove abandoned reference clones only after checking callers and active processes, and unlink reparse points without following their targets. Keep toolchains, models and user data.

## Document chunk settings

Documents → embedding settings exposes shared character-based chunk size (256–8192) and overlap (0 to size minus one). The same persisted values apply to uploaded files and document tools used from chat. Saving never calls a model or changes existing vectors; rebuild explicitly to apply new settings to indexed documents. Index metadata records the settings used. Highly overlapping inputs are rejected after at most 512 spans, before provider calls. The limits bound work, rather than promising a fit for every model tokenizer. Agent-side setting mutations retain approval requirements.

## Split GGUF language models

The model library recognizes complete canonical `name-00001-of-0000N.gguf` groups within one directory and pinned repository revision. It downloads every part as one language-model variant; incomplete or mixed groups cannot activate. Every part retains manifest checksum verification and inference-time size/mtime checks. Split metadata must match the verified group. Only the first part is passed to llama.cpp, which loads its peers (upstream [loader contract](https://github.com/ggml-org/llama.cpp/blob/master/src/llama-model-loader.cpp)). Discovery does not hash weights or scan tokenizer arrays. This format support does not imply support for every architecture, gated downloads, multimodal pairing or sharded embedding/TTS engines.

## Vision projector selection

Repositories containing multiple single-file vision projectors require an explicit projector choice before base-model variants are offered. The native downloader independently revalidates the exact selected base/projector filenames against fresh pinned-revision metadata. The UI lists the actual pair and its combined size; no lexicographically first projector is silently chosen. Select the pair documented by the publisher: filename/layout checks do not prove architecture compatibility. Only one variant per kind/repository/revision is stored. A different installed file pair is rejected rather than silently reused or overwritten; unload and remove that installation before downloading another pair. Sharded projectors remain unsupported.

## Background-only commands on Windows

From PowerShell 7 (`pwsh`), use `tools/start_background.ps1 -FilePath <executable> -Arguments @(<arguments>) -LogPath <absolute-log> -WorkingDirectory <workspace>` for test/build helpers. The launcher creates its Windows console hidden at process creation, and ordinary descendants inherit it. `CreateNoWindow` alone does not propagate; native commands must run through this launcher. Output streams go to the log and `.err`; completion is recorded in `.result.json`. Nested fixture subprocesses must also use `CREATE_NO_WINDOW` / `windowsHide`; the parent flag is not a substitute.

## Product branding

App-owned menu labels, dialogs, image alternatives and companion display labels use Jarvis. Run `node tools/audit_branding.mjs` for the frontend display-text/catalog guard (also included in offline agent tests). It does not certify dynamic provider/OS/user text or native messages; inspect those producers when changing them. Existing persistence keys, terminal protocols and third-party package names retain compatibility. Upstream attribution and pinned companion provenance remain in bundled license/notice files; do not strip or rename them during UI cleanup.

## History provenance

Public history contains a complete source baseline and verified distribution commits. Upstream copyright, licenses and source identity remain in NOTICE.md; no intermediate debugging or private recovery branches are published. Rewriting requires an external verified backup and explicit force-with-lease OIDs. Preserve validation identities when moving files or reconstructing commits; a new hash does not imply another build or repeated test.

## Shared interaction motion and zoom checks

The shell and embedded documents use shared motion tokens in `src/motion.css`. Keep feedback short, animate opacity/transform rather than layout, and avoid per-token or whole-editor remount animations. Both the user's reduced-motion preference and live OS changes apply to CSS, canvas effects and embedded surfaces. The explicit app setting is separate from the effective OS preference.

These choices follow [Fluent motion](https://fluent2.microsoft.design/motion), [web.dev animation performance](https://web.dev/articles/animations-guide) and [W3C reduced motion](https://www.w3.org/WAI/WCAG22/Techniques/css/C39). Motion communicates changes; it must not delay an action, hide focus or be the only indication of status.

Use actual native `Webview.setZoom()` for 200%/400% checks in the own hidden QA host, independently of viewport resizing or text scaling. The focused Quit fixture checks settings overflow at both zoom levels; it does not certify every app tab. Continue with keyboard-resizable panes, focus/escape behavior, wrapping toolbars, relative typography and reachable dialog actions in their owning UI changes. RuinedFooocus's keyboard splitter/reflow patterns are references, not a reason to import its Gradio UI or add another zoom system.

Run `qa_services.py --quit-only --bundled-assets` through the hidden worker for release checkpoints. `JARVIS_QA_SKIP_ZOOM=1` reuses unchanged passing zoom evidence while diagnosing another failed case. The fixture uses only its own PTYs and synthetic error/status inputs; no personal browser, microphone or cloud model is involved.

Use `qa_services.py --palette-only [--bundled-assets]` for the focused command-palette locale regression in the same isolated hidden host. It checks live labels/search/disabled reasons and preserves an unsaved editor buffer without a model call. Keep raw workspace names and command IDs unchanged.

## Tool efficiency measurements

Run `python benchmarks/browser_efficiency.py --iterations 250 --output .cache/verification/browser-performance/report.json` through the hidden worker. The deterministic fixture compares the same English/Russian HTML and distinguishes advertised native navigation, legacy evaluation with an included snapshot, and legacy evaluation needing a separate observation. Report call count, all response bytes, model-result bytes and p50/p95 routing/JSON timing. `ceil(characters / 4)` is a labelled estimate, not a tokenizer count or billed usage. These offline timings exclude the browser, network and model.

Use `qa_services.py --layout-picker-only [--bundled-assets]` for the shared language picker and native zoom geometry. `JARVIS_QA_LAYOUT_GEOMETRY_ONLY=1` reuses earlier picker interaction evidence while checking a changed layout; skipped interaction checks must not be reported as newly passed. `tests/desktop/qa_browser_settings.py` mounts the real browser settings component with mocked RPC in an owned headless browser; it deliberately never writes credentials into personal Chrome profiles. Its Vite fixture is excluded from production build inputs.

For browser routing regressions, `node benchmarks/smoke_browser_routing.mjs --local-model` explicitly runs a bounded GPT-OSS 20B session at LM Studio `http://127.0.0.1:1234`. It uses the real shared instructions/direct browser/canonical action adapters with simulated browser IPC, checks that generic CLI schemas are not advertised, skips routine profile discovery, and requires selecting an observed video before confirmed playback. It never operates personal Chrome or plays audio. Report real model calls/usage separately from simulated browser behavior; this is not an extension connectivity test. Use `--offline-fixture` instead of `--local-model` to check dispatch with zero inference. `music_play` selects an observed ordinary video; `media_play` starts an existing player. Normal offline tests never invoke the real-model mode.
## Bounded agent work and release goals

Follow [OpenAI Goals guidance](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex): define a measurable, bounded outcome, required evidence and completion conditions before starting. Use short scoped [subagent tasks](https://learn.chatgpt.com/docs/agent-configuration/subagents), one integration owner, parallel independent work and the user's selected agent model/effort. Share file/evidence pointers rather than repeatedly sending full history. Keep fixtures offline and reserve deliberate real-model calls for an unresolved inference concern. Separate synthetic byte/estimate/latency measurements from actual model usage. Completion requires verified source/artifact publication and honest remaining limits, not a claim that every possible provider/language/platform works.
Normal browser actions use the compact direct `browser_control` tool and saved profile. Advanced operations retain the full canonical schema through `jarvis_catalog`/`jarvis_action`. Browser model text excerpts are bounded to 1000/3000/6000 UTF-16 characters for up to 4K/up to 8K/larger contexts without splitting a surrogate pair; the saved tool result remains available through `tool_result_read` without repeating navigation/playback. Parent and child agents enforce the same bounded reply allowance used in context accounting. If preserved input still cannot fit after tool-result projection, stop locally with a translated actionable error rather than sending an oversized request or discarding saved history.

`tests/desktop/qa_tool_disclosure.py` is a focused headless source fixture for collapsed generic tool details, late errors, keyboard disclosure, reasoning choices and English/Russian approvals. It uses synthetic props only and makes no model/service requests.
Use `qa_services.py --fresh-context-only [--bundled-assets]` for the actual production transport/toolset regression: a fresh `123` must reach a synthetic provider while the model is unloaded (conservative 4K fallback), then an approved Python follow-up must return 42 with loaded 4K metadata. The provider asserts the compact tool profile and reply allowance. It makes three synthetic model requests and never uses personal Chrome, audio or cloud inference. Wait for cold WebView navigation with a Playwright API that pumps events; polling cached page URLs with `time.sleep` can remain stuck at about:blank.

At 4K, the shared model-facing set contains read_file, jarvis_catalog, jarvis_action, browser_control, weather_report and approved local screen_capture where applicable. Full handlers remain registered for history/approvals and optional tools are discoverable through the catalogue; unavailable schemas are not advertised every step. Instructions are concise; small-context dynamic model IDs use the compact base prompt. Never infer allocated context from a model's maximum support: unloaded/unknown/ambiguous allocations remain conservative. UI and request paths resolve exact loaded instance IDs/canonical model keys using the same helper.

## Selected-provider helper and queued-task checks

Canonical service/receipt checks live in `tests/python/local/test_services.py` and `tests/python/test_shared_agent.py`; file and Office handlers have focused tests under `tests/python`. Run only the cases affected by a change. These offline fixtures do not establish semantic inference, native utility execution or authenticated MCP behavior.

`qa_services.py --selected-helper-only` exercises the native `code_helper.explain` receipt, selected frontend provider, stateless generation, linked completion and usage accounting with one synthetic response. The owned UTF-8 input stays unchanged and no chat messages are created. For a deliberate real check, add `--selected-helper-provider local|google --live`; omission of `--live` rejects the request before host/API startup. Add `--selected-helper-preflight` to validate options without starting the server or host. Real helper checks admit one native inference POST, one cloud SDK attempt and a 45-second deadline, with no provider fallback. A single exact, empty-body loopback LM metadata GET is separately allowed for context discovery. Reports retain wire lengths, usage and bounded command/method/reason-only guard diagnostics.

The focused helper/task fixtures accept `JARVIS_QA_EXECUTABLE` only for the exact repository debug `apps/desktop/src-tauri/target/debug/jarvis-desktop.exe`. They use a unique `JARVIS_QA_PROFILE` and verify the native app-config identity before inference. Served frontend assets and debug-host evidence do not verify the selected portable's embedded assets.

`qa_services.py --queued-task-only` is synthetic only. Its owned fixture uses the existing HIGH-risk file move policy for actual Approve/Deny cards; changing a safety classification to manufacture a confirmation is not QA. Completion requires the original job/checkpoint, exact filesystem result, and preserved completed-step results. Isolated helpers use owned process trees; stateful host actions retain their own cancellation contracts. A completed or interrupted side effect must never be repeated merely to recover its output.

Set `JARVIS_QA_QUEUED_READ=1` before the same command with `--bundled-assets` to check one exact owned native file read. Clear it for the separate Approve/Deny scenario. The read fixture requires one plan, one native read, one summary and one saved result, with zero approvals/history/mutations; no real model is used. Primed parent-cache and mid-await WSL changes have separate canonical-handler tests. Retain failed evidence and keep filesystem/origin/nonce guards strict.

`qa_services.py --queued-plan-only --selected-helper-provider local|google --live` is a separate opt-in planner check: one selected-provider request must produce the exact owned HIGH-risk move card, then Deny must preserve the source and create no destination. It proves admission, planning and denial, not execution of that move. The planner advertises the canonical tool index and at most three exact schemas, permits at most two planning attempts, and uses explicit `$result` references for dependencies. It must not invent overwrite permission or make a second model call merely to translate a plan.

`python tests/desktop/qa_embedding_conformance.py --provider local|google --preflight` verifies installed identity/assets without inference; `--self-test` checks transport guards and canonical pre-cancellation offline. Only explicit `--live --output .cache/verification/<new-report>.json` performs semantic indexing and a fresh query against retained owned stores. The budget is two embedding inferences per provider, plus one Google count call; retries, downloads, CPU startup and fallback are refused. Unchanged reimport and pre-cancellation must add zero calls and preserve vectors. One-document retrieval and pre-cancellation do not certify ranking quality or inflight cancellation.

File/Office checks must inspect actual outputs and preservation on failure. FileFlow keeps typed owned errors separate from literal file contents, validates derived paths/Windows aliases and resolved operation keys, and preflights ZIP extraction at 10,000 entries / 512 MiB with CRC checks before exclusive writes. Office generation serializes into staging before publication; failures leave no new output and preserve existing destinations. Focused PPTX/XLSX readback supplements these failure-path checks.

The context meter and pre-provider guard share the canonical request projection and budget calculation, including fixed instructions, advertised schemas and permitted editor/environment context. Reply allowance and safety margin have separate rows; session totals remain cumulative provider-reported usage. Model, endpoint, instructions and editor changes invalidate the current projection. Oversized editable instructions receive specific localized guidance only when the unchanged base profile fits; otherwise the generic context-full error remains truthful. The displayed allocation percentage is an estimate, not a provider tokenizer count.

Set `JARVIS_QA_QUEUED_FAILURE=1` with `qa_services.py --queued-task-only --bundled-assets` for the current failure checkpoint. It precreates one SHA-bound owned script, permits one exact plan and normal approval, then verifies one write, exit code 1, retained output and zero recovery/summary calls. It is mutually exclusive with the read/live scenarios. The planner response is checked before dispatch; keep its one-request and filesystem guards strict. Normal offline CI includes the pure guard fixture; no model API is called.

## Current focused safeguards

See [the current implementation plan](DEVELOPMENT_PLAN.md) for accepted feature boundaries. Code build generates/saves only; a separately approved run executes it. Explicit GPU selection does not silently launch a CPU server. Named Piper/Whisper companion files receive nonempty UTF-8/JSON format validation before reuse/publication; these checks do not replace trusted publisher checksums.

Explicit Quit queries native download task identities independently of mounted settings. The final native check rejects newly started tasks and seals new registrations before exit. Keep downloading cancels Quit; normal window close still hides to the tray. The focused native test command is `cargo test --manifest-path apps/desktop/src-tauri/workbench-core/Cargo.toml --locked --offline quit_activity_tests --lib` through the hidden launcher; that crate is not a testable workspace member through the desktop manifest. `qa_services.py --quit-only --bundled-assets` checks the packaged UI/native contract. Its active-download prompt fixture is synthetic; registration/race ownership has separate native tests. Skip unrelated zoom repetition with `JARVIS_QA_SKIP_ZOOM=1` when zoom behavior is unchanged.