# Verification and release boundaries

Updated 2026-11-14. Evidence applies to the named source, artifact and exercised paths. See [Development](DEVELOPMENT.md) for checks and [the roadmap](ECOSYSTEM_ROADMAP.md) for remaining work.

## Selected Windows portable

- Build source: `1bf166afc780e85c973199f2731a918246ee37e1`; `apps/desktop/` tree: `05a23cfcf90a670964fa4522e7cee5211a4d0580`.
- Portable: `jarvis-desktop-1f9eeda874796f6d3bfb8cf0d5f19632e9642a2cc0c235074d8e64d147c09dfa.exe` (27,307,520 bytes).
- SHA-256: `1f9eeda874796f6d3bfb8cf0d5f19632e9642a2cc0c235074d8e64d147c09dfa`.
- The optimized native build completed once. Later source `87438ec5c2c0a183ef97f9193db529189c8cfeb1` changes only the external `requirements/full.lock.txt`: the required Imaging/Storage Streams SDK pair and python-dateutil. Embedded application inputs are unchanged; Python dependencies remain separate from the EXE.
- Embedded Terax revision: `f7bee29d8690aa0afe37ed8b4c94daeb5b0211ad`. Required terminal CLI SHA-256: `2e2471fb833e097d0edd8f0dc525f1dcd02ed0dc3254110acbbb5a871bd8ed51`. Its [manifest](../integrations/companions.json), Apache license and [terminal notices](../third_party/terax/NOTICE.md) remain separate.

## Current acceptance

The owned hidden WebView2 launched this exact portable and verified its bundled assets: 254 byte-identical SHA-256 matches and three HTML documents with full DOM equality after Tauri packaging, covering all 257 frozen assets. No runtime asset-serving overrides were used.

Native bootstrap returned the application snapshot/message list. The bundled language-menu sentinel was opaque (alpha 255), topmost and within the viewport; English/Russian selection, Escape and focus restoration passed. Bundled recovery advice names `START.cmd`. There were zero provider POSTs and page errors. Six served shell/settings/embedded dark/light glass cases separately passed before packaging; the bundled run does not claim to repeat all six contexts.

Launcher checks passed 22 focused cases plus two owned repair/helper lifetime and remote-dispatch cases. Paths containing spaces, `!` and `&`, argument/environment preservation, failure reporting and helper exit were covered. The hidden-process check observed five child chains with zero new visible consoles. No complete installer/download or Explorer console-flash test was performed.

Offline frontend contracts (462) and TypeScript passed. The cleanup security scan covered 19 source files with zero issues/errors. [Fresh GitHub Python checks](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43550891640) passed after the fixture import correction; the [final Python dependency PR check](https://github.com/Personal-Stuffs-johnmart19/jarvis/actions/runs/43552160828) also passed. Exact-head source/Python CI and independent review passed for the four dependency PRs, with focused native MCP fixtures, unused-dependency graph checks and isolated Python namespace/dateutil/Matplotlib checks. No check was disabled to accept the updates.

## Limits

This acceptance does not re-certify earlier broad tool audits, live models, authenticated providers, personal browser/media, microphone, BLE hardware or OCR activation. Configured integrations and downloaded models need their own bounded execution evidence. [Speech](SPEECH.md) owns CPU/Vulkan and model-asset limits.

Synchronous network cancellation remains cooperative; remote MCP cancellation does not promise rollback. URL validation does not pin transport DNS/proxy destinations, and filesystem mutations are not a complete capability sandbox. Other operating systems require separate native GUI/lifecycle verification. Preserve private credentials, chats, models and user data outside the public tree; follow [Security](../SECURITY.md).
