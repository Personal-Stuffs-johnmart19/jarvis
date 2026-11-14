# Contributing to Jarvis

Start with [setup](docs/SETUP.md), the [repository map](docs/ARCHITECTURE.md) and [development workflow](docs/DEVELOPMENT.md). Jarvis is a checkout-based Windows application, not a package installed with `pip install .`.

## Ownership and layout

- `apps/desktop/`: React shell, embedded workspace and Rust host.
- `local_mode/`, `agent/`, `actions/`, `memory/`, `vision/`: Python service packages. Their established imports support the portable launcher; new shared application services belong in `local_mode`, not new root scripts.
- `integrations/`: the embedded workspace's terminal CLI packager and checksum manifest. `remote/`: the optional Windows BLE transport; Telegram/BLE startup is owned by `local_mode/remote.py`. No Android app is maintained in this tree.
- `tools/setup/`, `tools/checks/`, `tools/ci/`, `tools/dev/`: provisioning, checks, CI and isolated development dependencies.
- `tests/python/`, `tests/agent/`, `tests/desktop/`: Python regressions, Node contracts and explicit owned desktop/live probes. Python fixtures remain with their tests; shared desktop fixtures live in `tests/fixtures` when needed.
- `benchmarks/`: measurements rather than the default regression suite.
- `requirements/`: local, full and CI dependency inputs/locks. Root `requirements.txt` is the full-install entrypoint.
- `config/`: source examples only. `models/`: metadata and notices only. Credentials, weights, caches and user data stay local.
- `desktop-bin/`: only the selected portable and manifest-pinned companions.

Keep runtime entrypoints at the root for existing users. Backend packages deliberately retain a flat layout: moving them under `src` would introduce an installation requirement into portable startup. `pyproject.toml` configures tests without running an installer.

## Upstream and component provenance

Preserve the Kaizumi base attribution and recorded import revision in [NOTICE.md](NOTICE.md). The base [MIT license](LICENSE) does not replace component licenses. Adapted Terax frontend/native/CLI source is pinned by [SOURCE.json](apps/desktop/workbench/SOURCE.json); retain its Apache-2.0 license, source headers and [terminal notices](third_party/terax/NOTICE.md). Update source identity and the CLI manifest together when intentionally refreshing that upstream.

Provider adapters, browser automation, MCP and local runtimes are integrations with separate dependencies and policies; see [the integration map](README.md#source-and-integrations). Keep Python, pnpm and Cargo lock updates coherent with the actual consumers. Downloaded engines and model weights retain their own notices and terms; an application license is not a redistribution grant for those assets.

## Changes and checks

Use the pinned Node version and dependency locks. On Windows, run development commands through `tools/start_background.ps1`; keep helpers and descendants hidden. `tools/checks/agent.ps1` runs the offline Node contracts and TypeScript checks. Python CI selections are maintained in `tools/ci/test-python-services.ps1`. See the development guide for the prepared QA interpreter, UI tests and packaging.

Keep changes focused and review them for correctness, readability, architecture, security and performance. Fix failed affected checks; reuse passing evidence for unchanged inputs. Unit tests must not contact external services, operate personal Chrome or send global keyboard input. Real-provider probes require explicit flags and a small request budget.

## Commits

Commit complete, reviewable implementations with their relevant checks. Do not publish temporary debugging states, repeated rebuilds or unrelated local edits. Every Codex-assisted commit must include this exact trailer:

```text
Co-authored-by: codex <noreply@openai.com>
```

Preserve human and third-party attribution; the trailer does not replace upstream licenses. History rewrites require an external verified backup, explicit expected remote OIDs and `--force-with-lease`. Never publish recovery branches containing private history.

## Public repository hygiene

Follow [SECURITY.md](SECURITY.md). `tools/ci/check-public-tree.mjs` rejects private state and unmanifested model binaries in the tracked tree. CI runs a checksum-pinned Gitleaks scanner with redacted output. Intentional synthetic secret fixtures require a narrow inline explanation; never exempt whole test directories.

Model weights have separate distribution terms. Record their source and checksums in `models/manifest.json`, inspect the publisher's terms, and keep downloads out of Git. Unknown provenance is not permission to redistribute a binary.

## Native security findings

The security rules remain enabled for native code. `.codacy/native-audit.json` records individual inspected FFI and test-only syntax warnings with the containing function, rationale, complete source hash and exact span hash. It grants no file-wide or rule-wide exemption. Re-review the native contract before changing a disposition; fix real defects first.

CI retains the scanner's structured JSON, including execution errors. `tools/ci/check-security-review.mjs` rejects partial scans, missing execution evidence, zero-file runs, unreviewed findings and stale source/span fingerprints. It validates every audited file even when an incremental scan has no findings there. New source suppressions cannot silently preserve a stale audit. The raw report remains downloadable from the workflow artifact.

The pinned Codacy 0.25 runner has a tracked pnpm patch raising its hardcoded source-file limit from 150 to 256 KiB, so the Russian catalog is scanned. This changes no rules or skip checks. Review that patch when upgrading the development tool; do not silently drop locale coverage.
