# Verification and release boundaries

Updated 2026-11-13. Check entrypoints are in [Development](DEVELOPMENT.md) and [Contributing](../CONTRIBUTING.md). Evidence applies to the named inputs and exercised paths; a catalogue inventory does not certify every model, authenticated integration or platform.

## Selected portable

- Product source: `5651497d3420fadfc21016f8bf4ac3498abe673e`, including the file-policy, pinned-document, agent-schema/retry and gateway-presentation fixes. The release commit changes only the portable, manifest and this record.
- Embedded upstream: `f7bee29d8690aa0afe37ed8b4c94daeb5b0211ad`. Shared services, host IPC, approvals, localization and process ownership remain Jarvis adaptations.
- Portable: `jarvis-desktop-3c70618153cc1b4ab9bf709fb84e5868825c325689566a408171205839aeb04f.exe`.
- SHA-256: `3c70618153cc1b4ab9bf709fb84e5868825c325689566a408171205839aeb04f`.
- Optimized Windows build and checksum verification passed. The final bundled archive fixture launched this exact package headlessly from an unrelated directory without `JARVIS_ROOT`.
- The terminal CLI remains `2e2471fb833e097d0edd8f0dc525f1dcd02ed0dc3254110acbbb5a871bd8ed51`, with only the CLI and Apache license in its companion manifest. Embedded workspace bootstrap remains; the unused separate GUI is retired. Terminal notices are retained under [third_party](../third_party/terax/NOTICE.md).

Only the latest root portable and required companion CLI are tracked. Source/test-only changes do not automatically require rebuilding unchanged product inputs.

## Current tool audit

The actual owned UI catalogue contained 129 unique rows: 91 Python capabilities and 38 native workspace entries. Pagination, unavailable-button state and “Use in chat” routing from Chat and Files & Tools passed against the real catalogue. A deterministic production gateway/native file read passed exact-schema comparison, one-use receipt/replay protection, session save/load and source preservation. These checks used the prior selected host with unchanged gateway/executor paths, not model-generated calls.

All 38 workspace catalogue schemas now exactly match the installed SDK's input schemas. Six previously serialized defaulted fields as required. Parent/full/compact/child registration, exact gateway lookup, default omission and approval admission have offline regressions. Unknown calls stop immediately. Repeated SDK-invalid arguments stop after two consecutive failures, allowing one useful correction; actual SDK fixtures cover parent and child bounds, a successful correction and preserved results. No model API was called.

Shared chat presentation now unwraps canonical gateway actions while retaining outer call, approval and receipt IDs. Focused hidden English/Russian fixtures cover read/list/search/shell output and command suggestions, explicit Insert, late failures, denials and malformed/control-character outputs. Nothing is inserted or executed automatically.

Actual native IPC checks passed Python, Node, rg, Git, pip, Cargo, uv and npm on bounded owned inputs/version commands through the unchanged canonical executor. Their output/time caps and zero provider requests are recorded. Optional utility installation/readiness is separate from handler registration; no compiler or tool was installed for this audit.

Final bundled archive acceptance passed a safe create/list/extract/readback, exclusion of a protected descendant, direct protected-source rejection, protected-member list rejection and protected-member extraction rejection. Existing source/output bytes were preserved, failed operations published no output, and no provider requests or page errors occurred.

## File, document and approval boundaries

Python and native file/archive paths reuse the canonical credential policy, including credential backups, directory aliases and Git credential files. Controlled reads validate the actual regular-file object before bytes are consumed; source changes fail rather than silently substituting another file. Document hashes, parsers, page counts and translation/embedding fallback reuse pinned bytes. Focused fixtures cover preservation, substitution, size limits, supported formats and literal data. They do not perform semantic inference.

File action aliases share normalization with risk classification and dispatch. The actual schema/admission regression confirms extraction/organization aliases require the same confirmation as their canonical operations. Controlled failures retain owned provenance and English/Russian rendering; external/provider/user text stays literal.

Legacy document URL reads disable automatic redirects, validate every resolved address before each bounded hop, close responses and bound streamed decompressed bytes before decoding. This is a preflight guard: transport DNS/proxy destinations are not pinned, so complete DNS-rebinding protection is not established. General filesystem mutations are not a full directory-capability sandbox.

## Quality gates

The integrated offline agent suite and TypeScript gate passed once, followed by production frontend/native compilation. Focused Python, native, actual-SDK and headless UI checks passed; only changed/failed cases were rerun. Independent agents reviewed file, document, native, schema and presentation boundaries. No personal Chrome, media, microphone, credential store or live inference operation was used.

All 143 managed security rules remain enabled. The staged source scan covered 46 files with four exact previously reviewed native syntax findings, zero analyzer errors and no unreviewed findings. All 122 existing source/span dispositions remain valid; only one file fingerprint changed after its four reviewed spans were confirmed byte-identical. No exception or suppression was added. The initial integration gate's EOF-whitespace failure was corrected without repeating its passed unit/TypeScript stages.

The staged public tree passed its history guard. A final redacted Gitleaks scan covered all unique staged blobs, including the selected portable and CLI, with zero findings and no secret values printed. GitHub CI is checked after publication; focused local results are not a substitute for that status.

## Retained acceptance for unchanged paths

These earlier bounded results were not replayed during this tool audit:

| Area | Verified result and limit |
| --- | --- |
| PC Status | The previous selected package passed the real chip/prompt/shared SDK path: three synthetic requests, one real memory-status execution, visible useful answer and persisted reload, without shell/approval/page errors. This is application-routing evidence, not live model inference. |
| Navigation and preferences | Earlier owned checks covered six main entries and 18 settings sections with controlled backend responses. General motion sync/persistence/OS preference and translated owned-description search passed; this does not establish every native integration's execution. |
| Launchers and cancellation | Windows setup parsing, guarded environment reuse, packaged chat/approval/Quit checks and owned process cleanup passed. Kernel termination signals, not exit codes alone, determine Windows cancellation. No complete fresh network installation was performed. |
| Weather | Thirty-one offline cases and one real Vorzel, Kyiv region geocode/forecast passed; two provider GETs, no model inference. Ambiguous places request clarification. |
| Music | One live selected-profile Chrome check confirmed advancing ordinary YouTube playback, sound enabled, no ads/player errors, then verified pause. It does not measure speaker output or other credentials. |
| Media and speech | Granted PNG and muted WAV metadata loading passed and failed after revocation; playback/PDF visuals were not covered. Current Qwen labels/help were checked without synthesis; [Speech](SPEECH.md) owns its asset and CPU/Vulkan boundaries. |

## Remaining boundaries

Configured credentials, authenticated HTTP MCP deployments, personal accounts, arbitrary local models and newly installed utilities require their own bounded execution evidence. Registration and a test reference are not execution. An earlier Gemini 3.8 request did not return a meaningful response; another provider's result does not establish that model's behavior or authorize changing user selection.

Synchronous SDK/network cancellation remains cooperative until worker exit. Remote MCP cancellation does not promise rollback. Archive/search operations have no general inflight cancellation endpoint. Native macOS/Linux GUI behavior and stateful host lifecycles need their own evidence. See [the roadmap](ECOSYSTEM_ROADMAP.md).

Public source excludes private credentials, chats, task records and model weights. Cleaning current history neither revokes credentials nor erases older clones, host caches or pull-request objects. Follow [Security](../SECURITY.md) before changing repository visibility.
