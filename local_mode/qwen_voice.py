"""Qwen3 designed persona anchored by a fixed reference on a Vulkan TTS host."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
from threading import Event, Lock, Thread
from uuid import uuid4
import wave
import httpx
from local_mode.audio_cpp_install import DIRECTORY
from local_mode.downloads import cancellable_lock, check_cancelled, download_file
from local_mode.messages import OwnedRuntimeError, OwnedValueError
from local_mode.supertonic_voice import RussianVoice, validate_wav
from local_mode.speech_audio import play_wav, trim_wav_edges
from local_mode.speech_numbers import numbers_to_words
from local_mode.speech_text import SpeechPart, speech_parts

MODEL = DIRECTORY / "qwen3-voicedesign-q8_0.gguf"
BASE_MODEL = DIRECTORY / "qwen3-base-q8_0.gguf"
REFERENCE_DIRECTORY = DIRECTORY / "qwen3-anime-reference"
REFERENCE_TEXT = "Hello, I'm here with you. Let's take this one step at a time."
REFERENCE_LOCK = Lock()
FRAGMENT_LIMIT = 1000
VOICE_ID = "qwen3:anime-female"
VOICE_NAME = "Qwen3 — Anime female (fixed reference · English / Russian · Vulkan)"
INSTRUCTION = ("A cheerful young adult female anime character, with a soft, clear, bright and sweet "
               "voice, playful gentle intonation and natural pacing. Speak clearly, without whispering "
               "or exaggerated squeaking.")


def _assets(name: str) -> dict:
    return json.loads(Path(__file__).with_name(name).read_text(encoding="utf-8"))


def _download(assets: dict, destination: Path, cancel_event=None) -> None:
    download_file(f"https://huggingface.co/{assets['repo']}/resolve/{assets['revision']}/{assets['path']}",
                  destination, max_bytes=assets["size"], sha256=assets["sha256"], cancel_event=cancel_event)


def ensure_model(cancel_event=None) -> None:
    """VoiceDesign is needed only when constructing the synthetic reference."""
    _download(_assets("qwen_tts_assets.json"), MODEL, cancel_event)


def ensure_base_model(cancel_event=None) -> None:
    _download(_assets("qwen_base_assets.json"), BASE_MODEL, cancel_event)


def _reference_identity() -> dict:
    return {"version": 1, "source": "qwen3-voicedesign-synthetic", "file": "reference.wav",
            "model_sha256": _assets("qwen_tts_assets.json")["sha256"],
            "instruction": INSTRUCTION, "text": REFERENCE_TEXT, "language": "en", "seed": 42}


def _cached_reference() -> Path | None:
    path = REFERENCE_DIRECTORY / "reference.wav"
    try:
        if REFERENCE_DIRECTORY.resolve().parent != DIRECTORY.resolve():
            raise OwnedValueError("Invalid Qwen3 voice or speech fragment.")
        manifest_path = REFERENCE_DIRECTORY / "manifest.json"
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 16_384:
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in _reference_identity().items()):
            return None
        if path.is_symlink() or path.stat().st_size > 1_500_000:
            return None
        data = path.read_bytes()
        rate, frames = validate_wav(data)
        if not 2 <= frames / rate <= 30 or hashlib.sha256(data).hexdigest() != manifest.get("sha256"):
            return None
        return path
    except (OSError, ValueError, EOFError, wave.Error):
        return None


def ensure_reference(cancel_event=None) -> Path:
    """Generate once; never use microphone input or an arbitrary external WAV."""
    with cancellable_lock(REFERENCE_LOCK, cancel_event):
        cached = _cached_reference()
        if cached is not None:
            return cached
        # Check the owned destination before creating files or starting inference.
        if REFERENCE_DIRECTORY.resolve().parent != DIRECTORY.resolve():
            raise OwnedValueError("Invalid Qwen3 voice or speech fragment.")
        ensure_model(cancel_event)
        designer = _DesignVoice(cancel_event)
        finished = Event()
        def watch_cancel() -> None:
            while not finished.wait(.05):
                if cancel_event is not None and cancel_event.is_set():
                    designer.cancel()
                    return
        watcher = Thread(target=watch_cancel, name="Jarvis-Qwen-reference-cancel", daemon=True)
        watcher.start()
        try:
            check_cancelled(cancel_event)
            data = designer.wav(REFERENCE_TEXT)
            check_cancelled(cancel_event)
        except Exception:
            check_cancelled(cancel_event)
            raise
        finally:
            finished.set()
            watcher.join()
            designer.close()
        rate, frames = validate_wav(data)
        if not 2 <= frames / rate <= 30 or len(data) > 1_500_000:
            raise OwnedValueError("Invalid Qwen3 voice or speech fragment.")
        REFERENCE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        path = REFERENCE_DIRECTORY / "reference.wav"
        manifest = {**_reference_identity(), "sha256": hashlib.sha256(data).hexdigest(),
                    "sample_rate": rate, "frames": frames}
        temporary_audio = REFERENCE_DIRECTORY / (uuid4().hex + ".wav.tmp")
        temporary_manifest = REFERENCE_DIRECTORY / (uuid4().hex + ".json.tmp")
        try:
            temporary_audio.write_bytes(data)
            temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            check_cancelled(cancel_event)
            temporary_audio.replace(path)
            temporary_manifest.replace(REFERENCE_DIRECTORY / "manifest.json")
        finally:
            temporary_audio.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
        return path


def available() -> bool:
    """Configuration-only readiness; no weight hashing, download or inference."""
    return MODEL.is_file() or (BASE_MODEL.is_file() and _cached_reference() is not None)


def qwen_parts(text: str) -> list[SpeechPart]:
    """Keep paragraph context in one bounded generation, preserving line pauses."""
    parts: list[SpeechPart] = []
    for part in speech_parts(text, FRAGMENT_LIMIT):
        if (parts and parts[-1].pause_ms < 320
                and len(parts[-1].text) + len(part.text) + 1 <= FRAGMENT_LIMIT):
            previous = parts.pop()
            parts.append(SpeechPart(previous.text + " " + part.text, part.pause_ms))
        else:
            parts.append(part)
    return parts


class QwenVoice(RussianVoice):
    def __init__(self, cancel_event=None) -> None:
        ensure_base_model(cancel_event)
        self.reference = ensure_reference(cancel_event)
        check_cancelled(cancel_event)
        # The native Base reference speech/speaker encoders use CPU once per
        # cached reference. The heavy talker and decoder require Vulkan; no
        # CPU synthesis fallback is installed by this class.
        super().__init__(model_spec={"path": str(BASE_MODEL), "family": "qwen3_tts",
                         "session_options": {"qwen3_tts.voice_prompt_cache_slots": 1}},
                         cancel_event=cancel_event)

    def request_options(self, text: str, speaker: str) -> tuple[str, dict]:
        return ("ru" if re.search(r"[А-Яа-яЁё]", text) else "en"), {
            "seed": 42, "max_tokens": min(4096, max(512, len(text) * 2 + 128)),
            "x_vector_only_mode": False,
        }

    def wav(self, text: str, speaker: str = "anime-female") -> bytes:
        if speaker != "anime-female" or not text.strip() or len(text) > FRAGMENT_LIMIT:
            raise OwnedValueError("Invalid Qwen3 voice or speech fragment.")
        if self.process is None or self.process.poll() is not None:
            raise OwnedRuntimeError("The local Qwen3 speech server is stopped.")
        language, options = self.request_options(text, speaker)
        payload = {"model": self.model_id, "input": text, "language": language,
                   "response_format": "wav", "options": options}
        if hasattr(self, "reference"):
            payload.update(voice_ref={"type": "path", "path": str(self.reference)}, reference_text=REFERENCE_TEXT)
        self.generating.set()
        try:
            with self.client.stream("POST", self.base + "/v1/audio/speech", json=payload) as response:
                response.raise_for_status()
                audio = bytearray()
                for chunk in response.iter_bytes():
                    audio.extend(chunk)
                    if len(audio) > 32_000_000:
                        raise OwnedValueError("Qwen3 speech response is too large.")
            result = bytes(audio)
            rate, frames = validate_wav(result)
            # The native API returns WAV even when it hits max_tokens. Refuse a
            # capped result instead of presenting a silently cut-off paragraph.
            if frames / rate >= (options["max_tokens"] - 1) * .08:
                raise OwnedRuntimeError("Qwen3 speech reached its generation limit. Shorten the paragraph and try again.")
            return result
        except httpx.HTTPError as error:
            from local_mode.gemini import safe_error
            raise OwnedRuntimeError("Qwen3 speech request failed: {0}", safe_error(RuntimeError(str(error)))) from error
        finally:
            self.generating.clear()

    def play(self, text: str, stop: Event, speaker: str = "anime-female") -> None:
        language, _ = self.request_options(text, speaker)
        parts = qwen_parts(numbers_to_words(text) if language == "ru" else text)
        if not parts or stop.is_set():
            return
        def synthesize(phrase: str) -> bytes:
            if stop.is_set():
                return b""
            return trim_wav_edges(self.wav(phrase, speaker))
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Jarvis-Qwen-buffer")
        pending = pool.submit(synthesize, parts[0].text)
        try:
            for index, part in enumerate(parts):
                data = pending.result()
                if stop.is_set():
                    return
                if index + 1 < len(parts):
                    pending = pool.submit(synthesize, parts[index + 1].text)
                play_wav(data, stop, part.pause_ms)
        finally:
            if not pending.done():
                pending.cancel()
                self.cancel()
            pool.shutdown(wait=True, cancel_futures=True)


class _DesignVoice(QwenVoice):
    """Short-lived bootstrap engine, closed before the Base model starts."""
    def __init__(self, cancel_event=None) -> None:
        RussianVoice.__init__(self, model_spec={"path": str(MODEL), "family": "qwen3_tts"},
                              cancel_event=cancel_event)

    def request_options(self, text: str, speaker: str) -> tuple[str, dict]:
        return "en", {"instruct": INSTRUCTION, "seed": 42, "max_tokens": 512}
