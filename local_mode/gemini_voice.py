"""Explicit cloud TTS with shared credentials/routing and cancellable local fallback."""

from __future__ import annotations

import base64
from concurrent.futures import CancelledError
import io
import queue
import threading
import time
import wave
from typing import Callable

from google.genai import types
from google.genai.errors import APIError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from local_mode import gemini
from local_mode.api_usage import RequestUsage
from local_mode.speech_audio import play_wav
from local_mode.messages import OwnedText, OwnedValueError, OwnedError

FLASH = "gemini-3.8-flash-tts"
LITE = "gemini-3.8-flash-lite-tts"
PREVIEW = "gemini-3.1-flash-tts-preview"
LONG_TEXT = 600
MAX_TEXT = 16000
MAX_AUDIO = 24 * 1024 * 1024


def model_chain(text: str, selection: str = "auto") -> tuple[str, ...]:
    """Choose the primary model from the full response length, not its network chunks."""
    if selection not in {"auto", "flash", "lite", "preview"}:
        raise OwnedValueError("Unknown Gemini TTS mode.")
    primary = {"flash": FLASH, "lite": LITE, "preview": PREVIEW}.get(selection)
    primary = primary or (FLASH if len(text.strip()) > LONG_TEXT else LITE)
    if primary == PREVIEW:
        return (PREVIEW,)
    return (primary, LITE if primary == FLASH else FLASH, PREVIEW)


def chunks(text: str, limit: int = 1800) -> list[str]:
    """Bound request memory/time while preserving every word up to MAX_TEXT."""
    text = text.strip()
    if len(text) > MAX_TEXT:
        raise OwnedValueError("A single speech response is limited to 16000 characters.")
    result = []
    while text:
        end = min(len(text), limit)
        if end < len(text):
            boundary = max(
                text.rfind(". ", 0, end),
                text.rfind("! ", 0, end),
                text.rfind("? ", 0, end),
            )
            if boundary >= limit // 2:
                end = boundary + 1
            else:
                space = text.rfind(" ", 0, end)
                end = space if space > 0 else end
        result.append(text[:end].strip())
        text = text[end:].strip()
    return result


def validate_wav(data: bytes) -> bytes:
    if len(data) > MAX_AUDIO:
        raise OwnedValueError("Gemini TTS returned oversized audio.")
    with wave.open(io.BytesIO(data), "rb") as audio:
        if (
            audio.getnchannels() not in (1, 2)
            or audio.getsampwidth() != 2
            or not 8000 <= audio.getframerate() <= 96000
            or audio.getnframes() <= 0
        ):
            raise OwnedValueError("Gemini TTS returned an unsupported WAV.")
        if (
            len(audio.readframes(audio.getnframes()))
            != audio.getnframes() * audio.getnchannels() * 2
        ):
            raise OwnedValueError("Gemini TTS returned an incomplete WAV.")
    return data


def interaction_wav(response) -> bytes:
    audio = getattr(response, "output_audio", None)
    value = getattr(audio, "data", None)
    if not isinstance(value, str) or len(value) > MAX_AUDIO * 4 // 3 + 100:
        raise OwnedValueError("Gemini TTS returned no audio.")
    return validate_wav(base64.b64decode(value, validate=True))


def preview_wav(response) -> bytes:
    for candidate in response.candidates or []:
        for part in (candidate.content.parts or []) if candidate.content else []:
            blob = part.inline_data
            if blob is None or not blob.data:
                continue
            if (blob.mime_type or "").split(";")[0] in {"audio/wav", "audio/x-wav"}:
                return validate_wav(blob.data)
            if (blob.mime_type or "").lower().startswith("audio/l16") or (
                blob.mime_type or ""
            ).startswith("audio/pcm"):
                if len(blob.data) > MAX_AUDIO or len(blob.data) % 2:
                    raise OwnedValueError("Gemini TTS returned invalid PCM.")
                import re

                match = re.search(r"rate=(\d+)", blob.mime_type or "")
                rate = int(match.group(1)) if match else 24000
                output = io.BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(rate)
                    wav.writeframes(blob.data)
                return validate_wav(output.getvalue())
    raise OwnedValueError("Gemini TTS Preview returned no audio.")


class GeminiVoice:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._client = None
        self._cooldown: dict[str, float] = {}
        self._request_slots = threading.BoundedSemaphore(2)

    def cancel(self) -> None:
        # Closing an HTTP client may wait on a transport lock. Never hold the
        # speaker/UI lock or the TTS worker while waiting for that close.
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            def close():
                try:
                    client.close()
                except Exception:
                    pass
            threading.Thread(target=close, name="Jarvis-TTS-close", daemon=True).start()

    close = cancel

    def request(self, model: str, text: str, stop: threading.Event) -> bytes:
        deadline = time.monotonic() + 60
        while not self._request_slots.acquire(timeout=0.05):
            if stop.is_set():
                raise CancelledError()
            if time.monotonic() >= deadline:
                raise TimeoutError("Previous TTS requests are still closing")
        result = queue.SimpleQueue()
        def generate():
            try:
                result.put((True, self._request(model, text, stop)))
            except BaseException as error:
                result.put((False, error))
            finally:
                self._request_slots.release()
        threading.Thread(target=generate, name="Jarvis-TTS-request", daemon=True).start()
        while True:
            try:
                ok, value = result.get(timeout=0.05)
                if ok:
                    if stop.is_set():
                        raise CancelledError()
                    return value
                raise value
            except queue.Empty:
                if stop.is_set():
                    raise CancelledError()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Cloud TTS request timed out")

    def _request(self, model: str, text: str, stop: threading.Event) -> bytes:
        @retry(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            sleep=stop.wait,
            retry=retry_if_exception(
                lambda e: not stop.is_set()
                and (
                    gemini.is_transient(e)
                    and not (isinstance(e, APIError) and e.code == 429)
                )
            ),
            reraise=True,
        )
        def attempt() -> bytes:
            if stop.is_set():
                raise CancelledError()
            with gemini.create_client(retry_attempts=0) as client:
                with self._lock:
                    self._client = client
                try:
                    if stop.is_set():
                        raise CancelledError()
                    with RequestUsage(model, "tts") as usage:
                        if model == PREVIEW:
                            response = client.models.generate_content(
                                model=model,
                                contents=text,
                                config=types.GenerateContentConfig(
                                    response_modalities=["AUDIO"],
                                    speech_config=types.SpeechConfig(
                                        voice_config=types.VoiceConfig(
                                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                                voice_name="Kore"
                                            )
                                        )
                                    ),
                                    http_options=types.HttpOptions(
                                        timeout=45000,
                                        retry_options=types.HttpRetryOptions(
                                            attempts=1
                                        ),
                                    ),
                                ),
                            )
                            usage.response = response
                            data = preview_wav(response)
                        else:
                            try:
                                response = client.interactions.create(
                                    model=model,
                                    input=[
                                        {
                                            "type": "user_input",
                                            "content": [{"type": "text", "text": text}],
                                        }
                                    ],
                                    response_format={"type": "audio"},
                                    generation_config={
                                        "speech_config": [{"voice": "Kore"}]
                                    },
                                    store=False,
                                    timeout=45,
                                )
                            except Exception as error:
                                # The Interactions SDK has a separate public error hierarchy.
                                code = getattr(error, "status_code", None)
                                if isinstance(code, int):
                                    raise APIError(
                                        code,
                                        {
                                            "error": {
                                                "code": code,
                                                "message": "Gemini TTS request failed",
                                            }
                                        },
                                    ) from error
                                raise
                            usage.response = response
                            data = interaction_wav(response)
                        if stop.is_set():
                            raise CancelledError()
                        return data
                finally:
                    with self._lock:
                        if self._client is client:
                            self._client = None

        return attempt()

    def play(
        self,
        text: str,
        stop: threading.Event,
        selection: str = "auto",
        *,
        fallback: Callable[[str], bytes],
        on_status: Callable[[str], None],
    ) -> None:
        models = model_chain(text, selection)
        for part in chunks(text):
            if stop.is_set():
                return
            data = None
            for model in models:
                if self._cooldown.get(model, 0) > time.monotonic():
                    continue
                try:
                    on_status(OwnedText("Gemini TTS: {0}", model))
                    data = self.request(model, part, stop)
                    break
                except CancelledError:
                    return
                except Exception as error:
                    if stop.is_set():
                        return
                    self._cooldown[model] = time.monotonic() + 300
                    if isinstance(error, OwnedError):
                        on_status(OwnedText(error.template, *error.values))
                    else:
                        diagnostic = gemini.safe_error(error)
                        on_status(diagnostic if isinstance(diagnostic, OwnedText)
                                  else OwnedText("Gemini TTS: {0}", diagnostic))
                    if isinstance(error, APIError) and error.code in {401, 403}:
                        break
            if stop.is_set():
                return
            if data is None:
                on_status(OwnedText("Gemini TTS is unavailable. Using local CPU speech."))
                data = fallback(part)
            if not stop.is_set():
                play_wav(data, stop)
