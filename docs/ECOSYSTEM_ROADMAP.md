# Jarvis roadmap and remaining limits

Updated 2026-11-13. Exact native/frontend and external Python source identities belong to [verification](VERIFICATION_CURRENT.md). Confirmed release defects and acceptance live in the active [plan](DEVELOPMENT_PLAN.md); completed fixes live in Git history and [verification](VERIFICATION_CURRENT.md).

## Release work

The confirmed file/Plan, MCP/queue-control, schema-ranking and live-feedback defects are fixed, reviewed and pushed. Packaged Approve/Deny/Stop and Quit checks passed; verification records the selected portable. Completed defects are not backlog items.

## Optional product work

| Area | Supported boundary and next increment |
| --- | --- |
| Native queued file mutations | Direct canonical writes and Plan review remain available with normal parent approval. Future native queue admission needs task-owned original-content proof and physical commit/cancellation receipts; it must not treat a deferred proposal as a written file. |
| Child mutations | Normal parent handoff is intentional. Automatic child resumption needs one-use parent approval and exact call/result linkage. |
| Session transfer | Queue routing is pinned to the original live owner/session/model/workspace. Restart/cross-window transfer requires explicit revalidation; approvals cannot be borrowed. |
| Browser evidence | Observations retain a 20,000-character cap; child evidence is bounded to 32 records / 256,000 UTF-8 bytes. Broader recovery needs a bounded access-controlled store and honest truncation. |
| Multimodal | Manual images and per-frame confirmed capture to the exact capable local destination remain separate. Optional camera/gesture/detection features must preserve these boundaries. |
| Speech | Piper ONNX remains explicitly CPU-only. Additional GGUF/Vulkan voices/quality packs need verified compatible assets and actual GPU execution. Preserve working GPU engines. |
| Model formats | Gated authentication, arbitrary projector architectures and embedding/vision/TTS shard groups require explicit contracts. Language-model GGUF discovery/activation does not certify every model's inference. |
| OpenClaw | OpenAI-compatible gateway support exists. Non-key sign-in and authenticated deployments require supported provider flows without importing unrelated consumer credentials. |
| Resources | Download pacing and shared GPU admission need a measured contention reproduction. Reuse existing resumable verified transfers, owned processes and QA tools. |

## Verification boundaries

These limit claims; they do not excuse leaving a reproduced supported-feature bug unfixed.

| Area | Evidence required for broader claims |
| --- | --- |
| Tools/providers | Capability-by-capability execution, missing-setup behavior, approvals and Stop. Inventory and denied plans are not execution. Use bounded owned fixtures rather than a cloud-model matrix. |
| Stateful host actions | Verify each lifecycle; do not claim hard cancellation or replay uncertain effects because a shared process primitive passed. |
| Utility recipes | Controlled install/error/elevation/cancel/PATH checks when a recipe changes. Do not install every compiler merely to test a list. |
| MCP | Authenticated HTTP deployments and other hosts remain separate from owned stdio/HTTP fixtures. |
| Files/RAG | Full-corpus ranking/cancellation now has offline regressions. Real semantic samples prove a pipeline, not broad ranking quality or every provider. |
| Locales/UI | Translate owned templates, not IDs or user/device/OS/provider text. Check actual consumers and dynamic layouts when owners change. Equal technical translations are not automatically bugs. |
| Platforms | Windows is the portable release path. Targeted WSL tests do not certify full Linux GUI or native macOS menu/Quit. |
| Dependencies | Node audits/changed-graph CI do not certify every language graph. Open dependency PRs are active work, not merged cleanup candidates. |

## Development boundaries

One integration owner, hidden processes, exact source/artifact identities, separate failure evidence and reuse of passed unchanged checks. External projects remain unchanged; independently implement behavior rather than copying GPL/AGPL code. Optional ROCm speech recognition needs a pinned supported runtime/GFX bundle, verified assets, cancellation and strict GPU policy before support is claimed.
