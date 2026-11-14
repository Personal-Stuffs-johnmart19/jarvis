# Third-party software and model notices

Jarvis does not replace upstream licenses with its own. The exact dependency set is recorded in Python, pnpm and Cargo locks; downloaded model/runtime manifests retain pinned provenance. Model terms can differ from engine terms. Preserve accompanying notices when distributing artifacts.

The recorded Kaizumi base is [`c7f89259868999c92d04b100a82839de1df1846a`](https://github.com/MusayevDoniyor/kaizumi/tree/c7f89259868999c92d04b100a82839de1df1846a), with its MIT license retained at the repository root. Adapted [Terax AI](https://github.com/crynta/terax-ai) frontend/native/terminal CLI source is pinned to `f7bee29d8690aa0afe37ed8b4c94daeb5b0211ad` by [SOURCE.json](../apps/desktop/workbench/SOURCE.json) and the [CLI manifest](../integrations/companions.json). Those components remain Apache-2.0; their license does not cover every dependency or model.

| Component | License/provenance |
| --- | --- |
| Kaizumi base | Musayev Doniyor (Kaizo), MIT; original copyright and source identity retained in [attribution](../NOTICE.md) |
| Adapted Terax components | Apache-2.0; [notice and adaptation scope](../third_party/terax/NOTICE.md) |
| Embedded terminal dependencies | MIT; preserved [Ghostty Web](../third_party/terax/terminal/ghostty-web.txt), [Ghostty](../third_party/terax/terminal/ghostty.txt), [restty](../third_party/terax/terminal/restty.txt) and [xterm.js](../third_party/terax/terminal/xterm-js.txt) notices |
| Tauri and official plugins | MIT/Apache-2.0 |
| React, xterm.js, CodeMirror | MIT |
| Lucide | ISC |
| llama.cpp | MIT; runtime distributions retain native notices |
| Downloaded embedding/chat/vision weights | Selected repository revision, model card and license apply; engine licensing does not establish weight or dataset terms |
| EmbeddingGemma weights | Separate model terms; inspect the selected model's pinned card and license |
| audio.cpp / Supertonic | Engine and weights have separate terms; pinned assets in `local_mode/audio_cpp_assets.json`; retain Supertonic OpenRAIL-M/model notices |
| Silero speech weights | CC BY-NC-SA 4.0; noncommercial conditions apply; [upstream license](https://github.com/snakers4/silero-models/blob/master/LICENSE) |
| Piper engine | [OHF-Voice/piper1-gpl](https://github.com/OHF-Voice/piper1-gpl), GPL-3.0; optional dependency, not relicensed as MIT |
| Piper voices | Individual model/dataset terms; retain downloaded model cards, including the Denis voice card |
| faster-whisper / Whisper conversion | MIT engine; retain selected model's card and license |
| Vosk models | Individual model terms from the distribution; retain its notices |
| Ultralytics/YOLO | Upstream AGPL-3.0 terms |
| OpenCV, MediaPipe, ONNX, PyTorch | Respective upstream licenses |
| Optional legacy vision / wake-word model files | [Exact local integrity inventory and source/term limits](../models/README.md); [manifest](../models/manifest.json). YuNet directory: MIT; SFace directory: Apache-2.0; YOLO publisher: AGPL-3.0 or separate commercial terms. Exact local provenance is not authenticated; MediaPipe variants and openWakeWord feature-weight terms remain unresolved. Model binaries are separate local assets. |
| PDF rendering | [pypdfium2/PDFium](https://github.com/pypdfium2-team/pypdfium2), bundled notices apply |
| Local translation | Argos package/model notices plus CTranslate2 and SentencePiece terms; corpus licenses are separate |
| Native ZIP | [zip-rs notice](licenses/zip-rs-LICENSE.txt) |

Provider adapters use the AI SDK and Python provider libraries recorded in the locks. Browser automation uses Playwright/CDP and the Jarvis Chrome connection; MCP uses the native `rmcp` stdio/Streamable HTTP client. These are integration boundaries, not ownership claims over provider services, browsers or remote MCP servers. See the [current integration map](../README.md#source-and-integrations).

Google APIs and hosted providers are governed by their service terms; a library license does not grant account access or free quota. Downloaded models are not committed as Jarvis-owned source. Never distribute credentials, private settings, uploaded files or cached chats with a release.
