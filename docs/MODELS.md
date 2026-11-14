# Models and managed downloads

Models & Providers selects shared defaults and independent autocomplete models. Integrations owns connections and credentials. Runtime pickers list configured cloud choices, discovered installed LM Studio/Ollama models and complete managed manifests. Unloaded models remain selectable and load on use. Discovery itself must not start inference.

## Managed library

Inspect a Hugging Face repository/revision in Model Library. Metadata includes pinned commit, files, published sizes and license/gated status. Choose an explicit compatible bundle and confirm download. Transfers support cancellation, partial resume and verified manifest publication. Installed weights remain on disk when unloaded. [Native download contract](MODEL_DOWNLOADS.md) defines validation and deletion behavior.

Managed identities use `jarvis-managed:<kind>:<repo>@<revision>`. Parent agents, subagents and autocomplete retain the selected identity. Each managed chat runtime uses an owned loopback server/alias rather than silently replacing a different model's process. Activation validates containment, pinned revision, complete manifests, sizes and hashes. Subsequent requests check recorded file identity without hashing multi-gigabyte weights on every turn.

Supported text/vision, embedding, Supertonic and paired Piper ONNX/JSON bundles have separate activation paths. Piper ONNX requires explicit CPU selection. Vision requires the correct projector; matching filenames is not proof of architecture compatibility. Text-only embeddings reject media. A downloaded format without an activation adapter is not runnable.

## Runtime and context

The existing manual llama.cpp setup accepts a GGUF path, optional projector and CPU/Vulkan choice; managed manifests provide the corresponding pinned paths. Servers bind loopback and only owned processes are stopped. Explicit local chat Vulkan and embedding local/GPU modes never retry on CPU. GPU initialization errors require an explicit CPU selection to use CPU. Embedding startup may try discovered Vulkan devices before inference; a failed inference is closed and reported without replay. The underlying engine may still use CPU for orchestration or unsupported operations; this policy prevents an implicit CPU-only server replacement. The configured context is conservative; Windows RAM/DXGI reporting is advisory and cannot guarantee free VRAM or an automatic task-specific allocation.

Embedding-model selection is shared with Documents and search tools. Changing identity does not reinterpret old vectors; use the explicit approved rebuild workflow. Existing indexes remain recoverable if a new rebuild fails.

The bounded semantic conformance check used the canonical local `embeddinggemma-2:Q8_0` engine on `Vulkan0` and cloud `gemini-embedding-2`, each with actual document indexing and a fresh semantic query. Local execution used two requests; Google used two embedding requests plus one token-count request. Unchanged source reimport reused identity/vectors without inference, and pre-cancellation preserved the index. This is one-document pipeline evidence, not retrieval-quality or inflight-cancellation certification. An embedding model discovered in LM Studio is not automatically an adapter for the separate canonical local engine.

## Boundaries

Gated repository authentication, embedding/vision/TTS shards, arbitrary model-family/projector compatibility and additional voice/quality packs are incomplete. Complete numbered LLM GGUF groups are supported by discovery/activation; real sharded inference remains unverified. A public repository and valid checksum do not establish runtime support. Models and runtime distributions keep their own licenses. Installed model data is not committed to Git.

## Confirmed local screen capture

For LM Studio on local HTTP port 1234, `screen_capture` requests confirmation for each frame and displays the fixed destination `http://127.0.0.1:1234` in its input. The actual model request is pinned to that IP, even when the integration uses localhost. Other providers/endpoints do not receive this tool. Choose an image-capable model; a text-only model is not made multimodal by capture.

Images are bounded JPEGs, kept in a session-scoped memory cache for at most one minute/four frames. Saved tool results contain only an opaque reference and coordinate metadata; recovery tools cannot retrieve the image. Reloaded/expired captures require fresh confirmation. Cloud model history projections withhold captured images; manually attached user images remain available. Local-provider HTTP clients ignore environment proxies and refuse cross-origin redirects. Camera capture is separate and is not enabled by this feature.

Use the returned original-screen bounds and resized-image dimensions to map coordinates before a separate computer-control action. Capturing does not click or run code. QA disables personal capture and uses synthetic pixels only. Legacy specialized Gemini vision helpers remain separate until their replacement is verified.

## Optional ROCm runtimes

ROCm speech recognition remains [roadmap work](ECOSYSTEM_ROADMAP.md), not an available Jarvis adapter. Detected hardware or another application's working environment does not add a model to Jarvis selectors. Any future adapter needs an isolated pinned runtime/GFX bundle, verified assets, owned processes, cancellation and explicit GPU selection. Keep external native libraries separate from Jarvis's Vulkan runtime; do not copy sibling-project environments or tuning flags into the portable.

## Local model request budgets

Models with up to 8,192 allocated context tokens use a compact advertised tool set. Other configured capabilities stay discoverable through `jarvis_catalog` and execute through the same `jarvis_action` handlers and approvals. Parent and child agents reserve only the advertised schemas; completed actions, original chat and required reasoning remain intact. Unknown/unloaded local models still use a conservative 4K allocation, rather than their advertised maximum.

For recognized GPT-OSS identities, an existing LM Studio `off` reasoning preference selects `low`; GPT-OSS supports low/medium/high, not `none`. Other models and the server-default preference retain their existing behavior. See the [official GPT-OSS model documentation](https://developers.openai.com/api/docs/models/gpt-oss-20b).

The current context meter estimates the same fixed instructions, advertised tool schemas and projected history/editor context used by the request guard, with reply allowance and safety margin shown separately. It tracks the selected configuration; cumulative provider-reported session usage remains a separate measure. The allocation percentage is approximate.

Automatic screen/camera uploads through legacy helpers are retired. Screen capture requires explicit vision capability from the exact selected LM Studio model, the permitted `http://127.0.0.1:1234` destination, and confirmation for each frame. Cloud image analysis uses manually attached images.

## Selected-provider text helpers

Legacy text-only code/document/planning helpers can use the selected chat provider through a bounded frontend bridge. Each helper request contains its own text prompt, without agent tools or chat history; its reported usage is added to that session once. The owner, session and model route are pinned for the request. A changed or detached route fails instead of silently sending a local-model request to Gemini. Text helpers reject image input and unsupported server tools; manual image attachments retain their separate path.

Queued planning uses the canonical tool index plus at most three exact schemas and at most two planning attempts. Dependent values require explicit `$result` references; permission to overwrite is never inferred. Provider JSON-schema normalization preserves this bounded contract. Actual selected LM Studio and Google checks each produced one exact pending HIGH-risk move card, then Deny preserved the owned files; neither check executed the move or added chat history.

Queued native file/search reads require explicit POSIX or drive-absolute targets. Original workspace, owner, session and provider route stay pinned, and canonical reads retain their filesystem environment across awaits. Stateless queue consumers receive contents without reading or updating the parent chat cache. Relative/default targets, native mutations, utility execution and MCP are outside this read-only handoff.

Queued approvals retain the exact pending action and completed work. Resumption requires the normal shared policy receipt and matching owner/session binding. Isolated queued handlers run in owned process trees, while persistent host-state actions keep their own lifecycle and cancellation limits. An interrupted action may already have changed state; inspect it before retrying. These contracts and the bounded local/Gemini helper samples in [verification](VERIFICATION_CURRENT.md) do not establish every tool's provider parity.

## Code execution outcomes

The canonical `code_helper.run` action runs an existing file with a literal argv array and normal approval. It makes no inference call itself. Exit status, stdout/stderr, cancellation, truncation and decoding loss are separate fields; incomplete output is never labelled complete. Python UTF-8 settings apply only to its owned child environment. Its owned process tree inherits the shared action's Stop/session cancellation; standalone callers must supply their own cancellation scope or timeout.

Queued failures preserve their actual result and completed work without model-driven recovery, generated-code substitutes or automatic replay. Validation failure, timeout and uncertain execution outcome have distinct localized feedback. Successful planning and summaries retain the selected provider bridge. Build now generates and saves once, reports execution_pending, and requires a separate approved run after review. It never repairs or executes unseen generated code. Write/edit/optimize persistence failures return typed failure. Publication preserves existing Windows DACLs and attempts rollback on partial replacement failure; POSIX mode and symlink preservation have separate platform checks. This does not establish every generated program or provider as correct.

## Fixed Qwen voice for long-form speech

The optional `qwen3:anime-female` voice uses pinned Base Q8 weights and a persistent synthetic reference. [Speech](SPEECH.md#optional-anime-style-voice-gpu-only) owns its current setup, asset downloads, CPU/Vulkan boundary, long-form behavior and cancellation contract.
