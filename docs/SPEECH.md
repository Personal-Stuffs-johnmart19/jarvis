# Speech and voice

Open **Settings → Speech & Voice** to choose microphone, synthesis model and speaker. Chat-model selection does not select recognition or synthesis. Device names originate from the operating system and are not translated by Jarvis.

## Recognition

The shared Jarvis recognition path and optional Whisper providers have separate runtime/credential requirements. Only usable providers belong in runtime selectors; configure missing connections in Integrations. The existing local path uses Vosk speech boundaries and a CPU Whisper adapter, with a lightweight Vosk option. Wake-word mode filters recognized commands; it is not a cloud Live API session. Text and recognized commands enter the same shared agent/approval flow.

## Synthesis

Local adapters include audio.cpp/Supertonic, the optional fixed-reference audio.cpp/Qwen3 voice, Silero, Piper and installed Windows voices. Gemini synthesis uses its configured cloud credentials and sends response text to Google. Missing runtimes/assets can require first-use installation; a catalogue entry alone does not mean assets are ready. Synthesis and speaker are separate settings. Optional quality/voice packs are supported only when an adapter explicitly implements them.

Use the resource links in Speech settings/Model Library to inspect compatible models and their licenses. Supported Supertonic GGUF and paired Piper ONNX/JSON bundles can be activated through the managed library. Piper ONNX is an explicit CPU compatibility option; the selected model and speaker are separate, model-bound settings. Managed speakers retain their configured language instead of applying Russian pronunciation to all text. Engine and model licenses are distinct; see [third-party notices](THIRD_PARTY.md).

Only controls supported by the selected engine are exposed. Do not assume universal style, pitch or intonation support. Speech preview language is synthesis input, separate from interface language. Custom preview text is user data.

## Lifecycle

F7 pauses/resumes playback; F9 stops synthesis/playback and queued speech. They do not control browser music. Microphone protection prevents generated speech from immediately becoming another command. Stale capture/synthesis results must be ignored after cancellation. PCM amplitude drives the visualizer without opening a separate microphone.

Cloud synthesis and fallback use the implementation's bounded retry/cancellation policy. A completed network request after Stop must not start playback. Do not describe quota, free-tier eligibility or every GPU as guaranteed: credentials, account policy, drivers, runtime and selected model all affect availability.

Remaining runtime language/restart coverage is tracked in the [localization audit](LOCALIZATION_AUDIT.md); the [localization guide](LOCALIZATION.md) describes the string boundary.

Prefer Vulkan/GPU when supported; Supertonic remains the Vulkan default. CPU-only models stay explicitly labelled and opt-in. Piper audio.cpp GGUF GPU integration is still pending; upstream capability does not prove support in Jarvis. See [audio.cpp Piper](https://github.com/0xShug0/audio.cpp/blob/main/docs/models/piper_tts.md) and [Piper voice loading](https://github.com/OHF-Voice/piper1-gpl/blob/main/src/piper/voice.py).

## Optional anime-style voice (GPU only)

In **Settings → Speech & Voice**, the Qwen3 speaker is **Qwen3 — Anime female (fixed reference · English / Russian · Vulkan)**. Its saved identity remains `qwen3:anime-female`. VoiceDesign creates one app-owned synthetic reference from a fixed adult anime-character style instruction. Subsequent speech uses Qwen3-TTS Base Q8 with that reference and transcript. No microphone sample or personal recording is used; the style does not imply a licensed character voice or guaranteed subjective quality.

English and Russian input are supported; Cyrillic text selects Russian and other text selects English. Mixed-language utterances use one language hint. The synthesis language is independent of the UI language. This optional voice does not replace the saved voice or the default engine.

The voice is listed when VoiceDesign weights exist, or when Base weights and a valid cached reference are available. Starting synthesis verifies the required pinned assets and can download missing/corrupt weights with cancellation: Base is approximately 2.70 GB; constructing a new reference also requires approximately 2.82 GB of VoiceDesign weights. `local_mode/qwen_base_assets.json` and `local_mode/qwen_tts_assets.json` own the exact revisions, sizes and SHA-256 values. Weights remain under `models/audio_cpp/`; the reference WAV and manifest remain in `qwen3-anime-reference/` beneath that directory. A valid reference bypasses VoiceDesign generation and download checks. Model license: Apache-2.0 ([upstream model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign)); audio.cpp has its own license.

Qwen's talker and waveform decoder require Vulkan and a detected GPU, with no automatic CPU synthesis or different-voice fallback. The smaller reference encoders run on CPU once per Base engine session, then reuse their encoded-prompt cache. The reference WAV and manifest persist across restarts; that in-memory cache does not. Stop cancels active inference; switching between Qwen and Supertonic retires the previous audio.cpp process. Supertonic speed/step controls do not apply to Qwen.

Long-form speech uses coherent passages up to 1,000 characters while preserving explicit line/paragraph pauses. A response at the generation ceiling is rejected rather than played as complete. The workflow follows [Qwen's design-to-clone guidance](https://github.com/QwenLM/Qwen3-TTS#voice-design-then-clone) and the pinned [audio.cpp Base/reference contract](https://github.com/0xShug0/audio.cpp/blob/v0.9.2-drama3-base-small/docs/models/qwen3_tts.md). Earlier bounded local samples are retained in `.cache/verification/qwen-longform/`; they do not certify every GPU, latency or voice quality.
