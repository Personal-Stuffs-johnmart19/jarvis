# Jarvis

A Windows desktop assistant with shared chat, files, editor, terminals, tools and voice. React/TypeScript runs inside Tauri; native services and the Python backend share model configuration, data access and approvals.

## Start

1. Keep the checkout in a permanent folder.
2. Run **INSTALL.cmd** for first-time setup.
3. Launch **START.cmd** or **Jarvis.cmd**.
4. Configure credentials/endpoints in **Settings → Integrations**, then select a model in **Models & Providers**.
5. Configure speech separately in **Speech & Voice**. The camera is optional and off by default.

The installer checks Python dependencies and WebView2. Runtime/model downloads require a connection; configured local models can run without a hosted model API. Rust/Node build tools are for development, not normal portable startup. The manifest-selected EXE and Python services are both part of the application.

## Capabilities and limits

- Common cloud/local chat model selection, tool catalogue and approval flow across Chat and Files & Tools.
- Windows drives/WSL workspaces, editor context, file attachments and an uploaded document library.
- Explicit embedding/RAG, source citations and approved index rebuilds.
- Configured LM Studio, Ollama, managed llama.cpp, compatible endpoints and MCP connections.
- Separate recognition, synthesis-model and speaker settings; engine-specific controls and model resources.
- Owned command process cleanup, utility discovery/execution and controlled installation settings.

Model modalities, tools, runtimes and credentials determine what is usable. Installed/downloadable does not imply universal compatibility. Current limitations are documented rather than advertised as completed features.

## Documentation

- [Documentation index](docs/README.md)
- [Setup and diagnostics](docs/SETUP.md)
- [CLI and environment](docs/CLI.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Open issues and unfinished work](docs/ECOSYSTEM_ROADMAP.md)
- [Localization audit](docs/LOCALIZATION_AUDIT.md)
- [Verified source and portable provenance](docs/VERIFICATION_CURRENT.md)
- [Development and review workflow](docs/DEVELOPMENT.md)
- [Third-party notices](docs/THIRD_PARTY.md)

English is the documentation/source interface language. Russian and future interface translations belong in separate catalogs; remaining runtime translation gaps are tracked in the audit. Do not distribute credentials, private chats, uploaded files or model caches with the repository.

## Development and distribution

See [CONTRIBUTING.md](CONTRIBUTING.md) for the repository map, checks and commit conventions, and [SECURITY.md](SECURITY.md) before sharing diagnostics or publishing a fork. Optional model weights are installed separately; their identities and provenance limits live in [models/manifest.json](models/manifest.json) and [the model guide](models/README.md).

## Source and integrations

Jarvis builds on [Kaizumi](https://github.com/MusayevDoniyor/kaizumi) by Musayev Doniyor (Kaizo), with the recorded base import at [`c7f8925`](https://github.com/MusayevDoniyor/kaizumi/tree/c7f89259868999c92d04b100a82839de1df1846a). The base MIT copyright and license remain in [LICENSE](LICENSE).

The embedded workspace, native workspace services and terminal control CLI adapt [Terax AI](https://github.com/crynta/terax-ai) at [`f7bee29`](https://github.com/crynta/terax-ai/tree/f7bee29d8690aa0afe37ed8b4c94daeb5b0211ad), under Apache-2.0. Jarvis supplies its desktop entrypoints, shared Python services, settings, approvals and localization. [Source metadata](apps/desktop/workbench/SOURCE.json), [CLI manifest](integrations/companions.json) and [adaptation notices](third_party/terax/NOTICE.md) record that boundary.

| Integration | Current scope |
| --- | --- |
| Model providers | AI SDK adapters, the shared Google backend, configured local servers and OpenAI-compatible endpoints; see [Integrations](docs/INTEGRATIONS.md) |
| Browser | Chrome extension/token connection and Playwright/CDP automation; see [Browser](docs/BROWSER.md) |
| MCP | Native `rmcp` client for owned stdio servers and Streamable HTTP, with per-tool policies; see [MCP](docs/MCP.md) |
| Local inference and speech | Separately installed runtimes and models, including llama.cpp and audio.cpp; see [Models](docs/MODELS.md) and [Speech](docs/SPEECH.md) |
| Optional remote input | Configured Telegram and Windows BLE services; no Android client is distributed |

Dependency locks record library versions. [NOTICE.md](NOTICE.md) and [third-party notices](docs/THIRD_PARTY.md) preserve component attribution; runtime, model and hosted-service terms apply separately. The maintained project is [Personal-Stuffs-johnmart19/jarvis](https://github.com/Personal-Stuffs-johnmart19/jarvis).
