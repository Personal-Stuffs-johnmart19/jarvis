# Release maintenance plan

Updated 2026-11-13. Current source, artifact, checks and limits belong to [verification](VERIFICATION_CURRENT.md). Completed task lists have been removed; optional increments remain in [the roadmap](ECOSYSTEM_ROADMAP.md).

## Current priority

Maintain the verified Windows release path. Start new work from a current supported-feature reproduction; use [verification](VERIFICATION_CURRENT.md) for exact artifact identities and unresolved provider boundaries. Optional product expansion remains separate from release defects.

## Next change contract

1. Start with a current reproduction and owning consumer. Do not reopen completed screenshots or infer a bug from catalogue counts.
2. Use disjoint implementation owners and independent five-axis review. Keep all native commands behind `tools/start_background.ps1`; preserve unrelated edits and live services.
3. Use owned offline fixtures. Real-provider samples are deliberate and bounded; never use personal Chrome, microphone or audio incidentally.
4. Run affected checks, retain failed evidence separately, and reuse passed evidence for unchanged inputs. Push coherent feature commits. Freeze assets during native embedding; rebuild only after changed product inputs.
5. Verify the bundled execution path before publishing. Keep only the manifest-selected root EXE and required companion binaries; never recursively remove executables.

This follows [OpenAI long-task guidance](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex): explicit outcomes, bounded milestones and maintained evidence.

## Integration invariants

- Native queue controls must agree across frontend consumers, native admission/reserved capacity and Python priority dispatch. `test_queued_ui_bridge_admission.py` checks actual consumers and saturated ordinary capacity. A mocked controller alone cannot prove the native bridge admits its requests.
- Explicit registered canonical tool names take precedence during schema selection. Keep the three-schema and7,600-character bounds; never add discovery/model calls to compensate for a failed execution or replay uncertain side effects.
- Immutable receipts pin owner/session/workspace/configuration and actual MCP connection. Stop must retain capacity until physical cleanup is proven; a rejected promise is not settlement. Remote cancellation does not promise rollback.
- Pending Plan review is distinct from persistence. Bind original content and workspace, retain failed proposals, and preserve actual outcomes after dispatch. Preflight conflict detection is not atomic compare-and-write.
- Keep fixed instructions and advertised schemas small enough for an actual approved call and result. Runtime and meter must count the same text; never invent a larger allocation, discard saved history or replay completed actions to recover output.
- Translate owned templates and retain provenance until rendering. Raw user/device/OS/provider values remain unchanged. Locale changes must not replay requests.

## Retained research decisions

Unsloth Studio (`475ceee98d8d141423201193c9cf2be2da30e600`, AGPL-3.0-only), RuinedFooocus (inspected `ebe4fc2`, GPLv3) and ComfyUI (`989d93d0d968e704c589ce0146bb18c77f6b50cc`, GPLv3) informed independent download/Quit, device, validation and process-ownership fixes. Their source was not transplanted. The separately retained shadcn stylesheet is MIT, byte-preserved and distributed with its notice.

Reuse Context7 `/ggml-org/llama.cpp` for specific device gaps and `/microsoft/playwright-python` for relevant fixture gaps. Explicit GPU requests fail closed; packaged checks preserve CSP and immutable IPC. Download pacing and GPU admission need a measured contention problem before adding subsystems.

Detailed research and failed/successful source evidence remain in `.cache/verification/nov12-release/`, `release-polish2/`, `release-polish3/` and `power-resume-20261112/`. Interrupted/corrupt logs are not passing checks.
