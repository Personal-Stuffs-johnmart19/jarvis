# Open localization audit

See [current verification](VERIFICATION_CURRENT.md) for the accepted source and latest native/shortcut checks. Inventory checkpoints are historical candidates, not current defect counts.

The static JSX extraction is only one boundary. The app is not yet fully localized or guaranteed English-only at runtime. Keep these items open until their actual display paths are migrated and checked.

## Latest focused audit (2026-10-28)

Parallel frontend/backend audits repaired reactive search/voice tooltips, Vosk/Whisper owned failures, document/OCR errors through the isolated worker, and file-picker presentation. Quit dialogs use complete English/Russian templates rather than concatenated English fragments. Raw paths, OS voice IDs, provider details and user text remain unchanged. These fixes are not complete app-wide translation coverage.

Command-palette labels, groups, disabled reasons and search now follow live locale changes. Dynamic workspace names retain their original text, command IDs and callbacks. Isolated-worker crash, timeout, size-limit and invalid-protocol errors now carry English templates with separate Russian resources; malformed output and Python integer-limit errors are classified without exposing worker output.

Speech playback validation and output/driver failures now use owned English templates with separate Russian resources. Settings errors retain sanitized provenance through queued work and RPC; successful voice, microphone and key-status messages keep structured ownership for live language changes. Arbitrary raw diagnostics remain verbatim.

Confirmed remaining display work: built-in agent/theme/model descriptions, tool/approval labels and count suffixes, model-library/document-store failures and host journal prose. Translate each owning producer/consumer pair and test live locale switching; some descriptor values remain legitimate technical names. Local reports: `.cache/verification/quit-release-20261028/translations-{front,back}.md` and `branding-native-followup.md`.

## Remaining source boundaries

- Frontend descriptor candidates (object labels, titles/descriptions and placeholders). Some are engine/protocol metadata, so classify them before translating; never translate IDs or model instructions merely because they use a `description` property.
- Direct notification/confirmation literals and nested toast action/cancel labels and descriptions (including conditional branches) are checked by the audit and translated at invocation time. Save-conflict, close/save failure, formatter guidance, LSP and terminal recovery paths are migrated. Raw provider diagnostics remain unchanged. Review dynamically assembled notifications separately; do not store stale translated text.
- Review the 47 English-valued Russian entries individually. The current list is mostly names, URLs, placeholders, key hints and technical literals; equality alone is not a missing-translation defect.
- Backend non-English literal candidates exclude docstrings and distinguish known linguistic-data modules from message/language-rule review candidates. Russian recognition patterns, number pronunciation and spoken-language examples must retain their behavior.
- Core desktop events, tool results, CLI/help and agent progress now default to English; state/tool labels and owned result messages use separate resources. Legacy action adapters and generated/provider/native errors still need classification before claiming all backend output is English. Raw user/model/tool reports and persisted history are preserved, not machine-translated.

## Current migration status and next targets

- Scheduler, BrowserAPI, the Chrome profile adapter and shared player-control owned messages now use English templates with separate Russian resources. Explicit result presentation localizes only OwnedText values; raw provider/user text and catalog-key collisions remain verbatim. Default reminders carry a boolean provenance marker; old stored text is not reinterpreted or rewritten.
- Playback-queue validation, replies and status callbacks now carry English owned templates with separate Russian resources. Disabled-camera guidance uses the same owned-error boundary. Saved playlist names/URLs, provider diagnostics and camera permission behavior remain unchanged; focused offline tests cover these contracts.
- Desktop background speech/progress callbacks now render owned reports in the live interface locale before journaling/speech; the CLI has an explicit --language choice for background reports. The tool-error sanitizer retains owned provenance while redacting secret values. Other legacy producers and historical records still require classification; do not translate stored history by string matching.
- `local_mode/api_usage.py` has two Russian ledger-validation diagnostics, but its constructor catches them and records a flag. These are internal source cleanup, not confirmed visible UI errors.
- Keep recognition regexes, pronunciation data, OS device names and persisted user content unchanged. Migrate by consumer path with English/Russian regression coverage.

## Required checks

- Follow every remaining descriptor/notification to its UI consumer and translate only app-owned presentation text.
- Verify live language changes in shell, embedded workspace, dialogs and memoized components without discarding chat/editor state; then verify restart persistence.
- Keep arbitrary provider errors, OS device names, file contents and user-authored text unchanged.
- Extend future language catalogs with matching keys/placeholders. A passing static audit does not prove full runtime translation coverage.

## Reproduce

Generate the current counts and file/line inventory on demand; this guide does not duplicate counters that become stale after each extraction.

```powershell
node tools/audit_localization.mjs .cache/localization.json
python tools/audit_localization.py .cache/localization-backend.json
```

Reports include paths/line locations for review and are local generated artifacts. AST tests guard visible static JSX text, accessible attributes, catalog coverage and non-English frontend literals; descriptor/runtime candidates deliberately remain explicit review work. The backend scanner inventories Python source only, not all generated/provider/native errors. Review Rust command errors separately before claiming complete backend coverage.

## Owned error boundary

Embedding errors now carry explicit Jarvis provenance, stable English templates and separate interpolation values through Python, Rust and both chat/settings consumers. Only that structure is translated. Arbitrary provider/OS errors, including catalog-key collisions, remain verbatim. Focused Python and frontend tests pass; this is not a claim that all legacy adapters or Russian fallback prose have been audited.

Legacy Gemini helper defaults now use English source instructions and honor explicit requested languages/formats instead of appending a Russian-only directive. Search-grounding refusal uses the owned-error catalogue boundary. This does not establish selected-provider parity for legacy vision helpers.

Local audio.cpp installer and Supertonic validation/startup/HTTP failures now use English templates and explicit owned-error resources. HTTP status values stay separate; raw provider diagnostics remain in the sanitized diagnostic log. Local CPU/GPU fallback status now carries owned templates and raw diagnostic values through snapshots and live events to the shared speech card, allowing locale changes to re-render it. Callbacks run outside the speaker lock and obsolete notifications are discarded by the runtime. Gemini cloud speech statuses and validation errors also retain owned templates and separate diagnostic values. LocalSpeaker action/error messages now use owned English resources, and queue-full notifications release the speaker lock before delivery. Legacy speech adapters outside these paths remain audit work; this does not establish full Russian runtime coverage.

Browser profile/CDP validation and companion manifest/launch errors now use owned English templates with separate Russian resources. Existing ValueError/RuntimeError handling and profile isolation remain intact. Companion repair downloads use the canonical Jarvis repository with pinned checksum/path validation. Plain companion success text now defaults to English; complete legacy status-message provenance remains audit work.
