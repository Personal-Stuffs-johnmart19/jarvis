"""Supertonic 3 in an owned local Vulkan process without cloud API calls."""
from __future__ import annotations
from local_mode.speech_numbers import numbers_to_words
from concurrent.futures import ThreadPoolExecutor
from local_mode.speech_audio import trim_wav_edges
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
from threading import Event
import time
from uuid import uuid4
import wave
import httpx
from local_mode.audio_cpp_install import MODEL, RUNTIME, ensure_assets
from local_mode.config import ROOT
from local_mode.messages import OwnedRuntimeError, OwnedValueError, OwnedText
from local_mode.processes import run
from local_mode.downloads import check_cancelled
from local_mode.speech_text import speech_parts
from local_mode.speech_audio import play_wav
from local_mode.voice_settings import SUPERTONIC_SPEAKERS as VOICES
from typing import Callable



def process_environment() -> dict[str, str]:
    """Keep service credentials out of the native engine environment."""
    return {key: value for key, value in os.environ.items()
            if not any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}


def choose_gpu(output: str) -> tuple[int, str]:
    devices = [(int(index), name) for index, name in re.findall(
        r'^Vulkan:(\d+) "([^"\r\n]+)" \[GPU\]', output, re.M)]
    if not devices:
        raise OwnedRuntimeError("audio.cpp found no Vulkan GPU.")
    # Prefer a discrete GPU over an integrated device when available.
    integrated = re.compile(r"UHD|Iris|Radeon(?:\(TM\))? Graphics", re.I)
    return next((device for device in devices if not integrated.search(device[1])), devices[0])


def validate_wav(data: bytes) -> tuple[int, int]:
    if len(data) > 32_000_000:
        raise OwnedValueError("Speech response is too large.")
    with wave.open(io.BytesIO(data), "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getnframes() == 0:
            raise OwnedValueError("Invalid WAV from Supertonic.")
        rate, count = source.getframerate(), source.getnframes()
        if not 8000 <= rate <= 96000 or len(source.readframes(count)) != count * 2:
            raise OwnedValueError("Truncated WAV from Supertonic.")
        return rate, count


def inspect_devices(executable: Path, cancel_event=None):
    options = dict(capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=process_environment(), cwd=RUNTIME)
    if cancel_event is None:
        return run([str(executable), "--list-devices"], timeout=30, **options)
    check_cancelled(cancel_event)
    options.pop("capture_output")
    process = subprocess.Popen([str(executable), "--list-devices"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **options)
    deadline = time.monotonic() + 30
    try:
        while True:
            check_cancelled(cancel_event)
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, 30)
            try:
                stdout, stderr = process.communicate(timeout=0.1)
                check_cancelled(cancel_event)
                return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


class RussianVoice:
    def __init__(self, *, model_spec: dict | None = None, cancel_event=None) -> None:
        from local_mode.tts_models import model_configuration
        if model_spec is None:
            model_path, compute_mode, self.selection_identity = model_configuration()
        else:
            model_path, compute_mode, self.selection_identity = model_spec["path"], "vulkan", model_spec["family"]
        self.unavailable = False
        self.cancelled = False
        self.generating = Event()
        self.process: subprocess.Popen | None = None
        self.client = httpx.Client(trust_env=False, timeout=httpx.Timeout(90, connect=2))
        self.directory = tempfile.TemporaryDirectory(prefix="jarvis-tts-")
        self.log = None
        self.model_id = "jarvis-" + uuid4().hex
        try:
            check_cancelled(cancel_event)
            asset_options = {"include_default_model": self.selection_identity == "builtin"}
            if cancel_event is not None:
                asset_options["cancel_event"] = cancel_event
            ensure_assets(**asset_options)
            check_cancelled(cancel_event)
            executable = RUNTIME / "audiocpp_server.exe"
            if compute_mode == "cpu":
                self.device, self.gpu = -1, "CPU"
            else:
                result = inspect_devices(executable, cancel_event)
                if result.returncode:
                    raise OwnedRuntimeError("Could not inspect Vulkan devices.")
                self.device, self.gpu = choose_gpu(result.stdout + result.stderr)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            self.base = f"http://127.0.0.1:{port}"
            config = {"host": "127.0.0.1", "port": port, "backend": compute_mode,
                      "device": self.device, "threads": 4, "lazy_load": True,
                      "max_loaded_models": 1, "ui_management": False,
                      "models": [{"id": self.model_id, "family": "supertonic",
                                  "path": str(model_path), "task": "tts", "mode": "offline",
                                  "session_options": {"supertonic.style_cache_slots": 1},
                                  "default_voice_preset": {"voice_id": "F1"}}]}
            if model_spec is not None:
                config["models"] = [{"id": self.model_id, "task": "tts", "mode": "offline", **model_spec}]
            if compute_mode == "cpu":
                config.pop("device", None)
            config_path = Path(self.directory.name) / "server.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            log_path = ROOT / ".cache" / "supertonic-vulkan.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log = log_path.open("w", encoding="utf-8")
            check_cancelled(cancel_event)
            self.process = subprocess.Popen([str(executable), "--config", str(config_path), "--no-ui", "--log"],
                cwd=RUNTIME, env=process_environment(), stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                check_cancelled(cancel_event)
                if self.process.poll() is not None:
                    raise OwnedRuntimeError("The Vulkan server exited. See .cache/supertonic-vulkan.log.")
                try:
                    response = self.client.get(self.base + "/v1/models",
                        **({"timeout": httpx.Timeout(0.5, connect=0.2)} if cancel_event is not None else {}))
                    if response.status_code == 200 and any(
                        item.get("id") == self.model_id for item in response.json().get("data", [])):
                        check_cancelled(cancel_event)
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                if cancel_event is None:
                    time.sleep(0.1)
                else:
                    cancel_event.wait(0.1)
            raise OwnedRuntimeError("The Supertonic server did not start within 30 seconds.")
        except Exception:
            self.close()
            raise

    def request_options(self, text: str, speaker: str) -> tuple[str, dict]:
        from local_mode.tts_controls import request_options
        return request_options(text)

    def wav(self, text: str, speaker: str = "F1") -> bytes:
        language, options = self.request_options(text, speaker)
        if language == "ru":
            text = numbers_to_words(text)
        if speaker not in VOICES:
            raise OwnedValueError("Unknown Supertonic voice.")
        if not text.strip() or len(text) > 240:
            raise OwnedValueError("Supertonic requires a fragment of 1 to 240 characters.")
        if self.process is None or self.process.poll() is not None:
            raise OwnedRuntimeError("The Supertonic server is stopped.")
        self.generating.set()
        try:
            with self.client.stream("POST", self.base + "/v1/audio/speech", json={
                    "model": self.model_id, "input": text, "voice": speaker,
                    "language": language, "response_format": "wav",
                    "options": options}) as response:
                if response.status_code != 200:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk[:8192-len(body)])
                        if len(body) >= 8192: break
                    reason = body.decode("utf-8", errors="replace")
                    low = reason.lower()
                    template = ("Supertonic returned HTTP {0}: insufficient GPU memory. Details: .cache/supertonic-last-error.txt"
                                if any(word in low for word in ("out of memory", "allocation", "out_of_device_memory"))
                                else "Supertonic returned HTTP {0}: unsupported Vulkan operation or driver. Details: .cache/supertonic-last-error.txt"
                                if any(word in low for word in ("unsupported", "not supported", "shader", "pipeline"))
                                else "Supertonic returned HTTP {0}: GPU synthesis failed. Details: .cache/supertonic-last-error.txt")
                    from local_mode.gemini import safe_error
                    diagnostic = ROOT / ".cache" / "supertonic-last-error.txt"
                    diagnostic.write_text(safe_error(RuntimeError(reason)), encoding="utf-8")
                    raise OwnedRuntimeError(template, response.status_code)
                data = bytearray()
                for part in response.iter_bytes():
                    data.extend(part)
                    if len(data) > 32_000_000:
                        raise OwnedValueError("Supertonic WAV size limit exceeded.")
            result = bytes(data)
            validate_wav(result)
            return result
        except httpx.HTTPError as error:
            raise OwnedRuntimeError("The local Supertonic connection was interrupted; check .cache/supertonic-vulkan.log.") from error
        finally:
            self.generating.clear()

    def play(self, text: str, stop: Event, speaker: str = "F1", *,
             fallback: Callable[[str], bytes] | None = None,
             on_fallback: Callable[[str], None] = lambda message: None) -> None:
        # Synthesize short sentences together; the model handles punctuation.
        # Preserve paragraph boundaries separately.
        from local_mode.speech_text import SpeechPart
        from jsonschema import ValidationError
        try:
            language, _ = self.request_options(text, speaker)
            prepared = numbers_to_words(text) if language == "ru" else text
        except (OSError, ValueError, ValidationError):
            # wav() reports invalid controls through the existing CPU fallback.
            prepared = text
        parts = []
        for part in speech_parts(prepared, 160):
            if parts and parts[-1].pause_ms < 500 and len(parts[-1].text) + len(part.text) + 1 <= 160:
                previous = parts.pop()
                parts.append(SpeechPart(previous.text + " " + part.text, part.pause_ms))
            else:
                parts.append(part)
        if not parts or stop.is_set():
            return

        def synthesize(phrase: str) -> bytes:
            if stop.is_set():
                return b""
            try:
                if self.unavailable:
                    raise OwnedRuntimeError("Silero CPU fallback is already active.")
                data = self.wav(phrase, speaker)
            except Exception as error:
                if fallback is None:
                    raise
                if stop.is_set():
                    return b""
                if not self.unavailable:
                    self.unavailable = True
                    self.close()
                    from local_mode.gemini import safe_error
                    on_fallback(OwnedText("Supertonic is unavailable: {0} Switched to Silero CPU.", safe_error(error)))
                data = fallback(phrase)
            return trim_wav_edges(data)

        # Prefetch only one fragment while the current fragment plays.
        # Avoid queuing an entire response in GPU memory.
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Jarvis-TTS-buffer")
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

    def cancel(self) -> None:
        """Cancelled inference must release the GPU before the HTTP timeout."""
        if self.generating.is_set() and self.process is not None and self.process.poll() is None:
            self.cancelled = True
            try:
                self.process.terminate()
            except OSError:
                pass

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        self.client.close()
        if self.log is not None:
            self.log.close()
        self.directory.cleanup()
