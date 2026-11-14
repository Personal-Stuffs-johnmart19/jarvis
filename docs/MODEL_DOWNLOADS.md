# Model downloads (native backend)

Module: `apps/desktop/src-tauri/workbench-core/src/modules/model_downloads.rs`.
Tauri commands registered in `workbench-core/src/lib.rs`.

## API
- `model_bundle_descriptors()` – returns explicit compatible layout descriptors.
- `model_fetch_repo_info(repo, revision?)` – HuggingFace `/api/models/..?blobs=true`; returns the pinned commit sha, license, gated flag, files with size and LFS SHA256.
- `model_match_bundles(kind, files)` – explicit layouts only:
  - `llm-gguf` (single `.gguf` or a complete numbered shard group); `embedding-gguf` (single non-sharded `.gguf`)
  - `vision-gguf-mmproj` (`.gguf` + `mmproj*.gguf`)
  - `tts-supertonic-gguf` (single-file `.gguf` voice model for audio.cpp runtime)
  - `tts-piper-onnx` (`<name>.onnx` + `<name>.onnx.json`; managed activation through the local Piper CPU runtime; requires valid metadata and matching files)
- `model_download_start(request)` – accepts `ModelDownloadRequest` (`kind`, `repo_id`, `revision`, `descriptor_id`, `files`, `request_id`). Registers cancellation and active target via RAII. If multiple variants match the descriptor, explicit `files` selection is required and revalidated against fresh metadata. Duplicate active targets fail busy safely.
- `model_download_cancel(task_id)` – cancels active download or registers early cancellation before metadata fetch.
- `model_delete(kind, repo_id, revision)`, `model_list_installed()`.

## Safety & Transfers
- HTTPS only, host allowlist (`huggingface.co`, `hf.co` + subdomains), no IP hosts or credentials; redirects are followed manually (max 5) and each hop is re-validated. Loopback HTTP exists only behind `allow_loopback_http` (tests/mirrors).
- Relative paths reject traversal, absolute/drive/backslash forms, reserved Windows names, `.part` and `manifest.json`. Symlinked ancestors/targets are refused; the model dir must canonicalize under the root.
- Transfers: `.part` files with HTTP Range resume (hash re-computed over the partial), absolute pinned deadline per chunk/header, size cap (published size or `max_file_bytes`), cooperative cancel (keeps partials for resume), SHA256 verified when the hub publishes one (the computed hash is always stored).
- `manifest.json` (owner-tagged) is written via temp+rename only after every file is verified. Delete removes only manifest-listed files, their `.part`, the manifest, then empty directories; stray user files stay.
- Active downloads registry uses RAII drop cleanup to ensure state is cleaned up when futures abort or drop.

## Limitations & Runtime Compatibility
- Complete numbered LLM GGUF shard groups are supported; embedding/vision/TTS shards are not. Real sharded inference remains unverified. Gated/authenticated repository token setup is not implemented. Remote SHA-256 verification uses the hub LFS digest when present; a locally computed hash alone does not authenticate files lacking a remote digest.
- Managed paired Piper ONNX/JSON activation validates metadata and exposes model-bound speakers. Activation requires an explicit CPU choice; model replacement refreshes an enabled stale managed speaker. Supertonic GGUF uses its existing audio.cpp activation path and remains the Vulkan default. Piper audio.cpp GGUF/Vulkan is not integrated.
- A crash between file rename and manifest write leaves an incomplete directory that is reused on retry but not listed/deletable until completed.
