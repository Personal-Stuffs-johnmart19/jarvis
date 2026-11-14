"""Local speech engines in one cancellable worker."""
from __future__ import annotations
import queue
import logging
from concurrent.futures import Future
import threading
import time
from typing import Any, Callable
from local_mode.speech_text import speech_parts, sapi_xml
from local_mode.messages import OwnedText, OwnedError, OwnedRuntimeError, OwnedValueError
from local_mode.speech_audio import play_wav, PlaybackStop
from local_mode.voice_settings import DEFAULT_VOICE, SILERO_SPEAKERS, neural_voices, available_neural_voices, gemini_tts_configured, read_voice, save_voice, validate_voice


def _cpu_audio(engines: dict[str, Any], text: str, report: Callable[[str], None]) -> bytes:
    """Shared CPU fallback for cloud/Vulkan speech without duplicate model loading."""
    if not engines.get("silero_failed"):
        try:
            if "silero" not in engines:
                from local_mode.silero_voice import RussianVoice
                engines["silero"] = RussianVoice()
            return engines["silero"].wav(text, "xenia")
        except Exception as error:
            engines["silero_failed"] = True
            from local_mode.gemini import safe_error
            report(OwnedText("Silero CPU synthesis failed: {0} Using Piper CPU fallback.", safe_error(error)))
    if "piper" not in engines:
        from local_mode.piper_voice import RussianVoice
        engines["piper"] = RussianVoice()
    return engines["piper"].wav(text)


class LocalSpeaker:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.error = ""
        self.on_error: Callable[[str], None] = lambda text: None
        self.on_status: Callable[[str], None] = lambda text: None
        self.backend_status = ""
        self._vulkan_engine = None
        self._cloud_engine = None
        try:
            self.voice = read_voice()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.voice = DEFAULT_VOICE
            from local_mode.gemini import safe_error
            self.error = OwnedText("Could not read speech settings: {0}", safe_error(error))
        self._queue: queue.Queue[tuple[str, str, int, Future] | None] = queue.Queue(maxsize=16)
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._paused = threading.Event()
        self._active = threading.Event()
        self._cancel = PlaybackStop(self._paused)
        self._generation = 0
        self._activity_revision = 0
        self._phase = "ONLINE"
        self._mic_ready_at = 0.0
        self._emitted_audio = False
        self._audio_level = 0.0
        self._level_at = 0.0

    @property
    def busy(self) -> bool:
        """Include synthesis and queued work so the microphone cannot hear the response."""
        return bool(self._queue.unfinished_tasks)

    @property
    def cancel_revision(self) -> int:
        with self._lock:
            return self._generation

    @property
    def activity_revision(self) -> int:
        with self._lock:
            return self._activity_revision

    @property
    def microphone_blocked(self) -> bool:
        with self._lock:
            return self.busy or time.monotonic() < self._mic_ready_at

    @property
    def state(self) -> str:
        with self._lock:
            if not self.busy:
                return "ONLINE"
            if self._active.is_set() and self._cancel.is_set():
                return "STOPPING"
            if self.paused:
                return "PAUSED"
            return self._phase if self._phase != "ONLINE" else "SYNTHESIZING"

    def _playback_state(self, state: str) -> None:
        with self._lock:
            self._phase = state
            if state == "SPEAKING":
                self._emitted_audio = True
            elif self._emitted_audio:
                # The audio buffer is closed; allow the short room echo to fade.
                self._mic_ready_at = time.monotonic() + 0.4

    def _playback_level(self, level: float, generation: int) -> None:
        with self._lock:
            if generation == self._generation:
                self._audio_level = max(0.0,min(1.0,level))
                self._level_at = time.monotonic()

    @property
    def audio_level(self) -> float:
        """Audible PCM level; paused, stopped and stale samples return zero."""
        with self._lock:
            if (self.paused or self._cancel.is_set() or not self.busy or
                    self._phase != 'SPEAKING' or time.monotonic()-self._level_at>.35):
                return 0.0
            return self._audio_level

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self) -> bool:
        with self._lock:
            if self._closing.is_set() or (not self._active.is_set() and self._queue.empty()):
                return False
            self._paused.set()
            return True

    def resume(self) -> None:
        self._paused.clear()

    def toggle_pause(self) -> bool:
        if self.paused:
            self.resume()
        else:
            self.pause()
        return self.paused

    @property
    def label(self) -> str:
        return next((item["name"] for item in neural_voices() if item["id"] == self.voice), "Windows SAPI")

    @staticmethod
    def voices() -> list[dict[str, str]]:
        result = available_neural_voices()
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        try:
            engine = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
            result.extend({"id": "sapi:" + item.Id, "name": "Windows — " + item.GetDescription(),
                           "language": item.GetAttribute("Language")} for item in engine.GetVoices())
        finally:
            comtypes.CoUninitialize()
        return result

    def _notify_error(self, text: str) -> None:
        try:
            self.on_error(text)
        except Exception:
            # Delivery is auxiliary: retain the synthesis failure and keep processing.
            logging.getLogger(__name__).warning("Speech error callback failed; synthesis failure retained.")

    def _report_status(self, text: str, generation: int, voice: str) -> None:
        with self._lock:
            if generation != self._generation or voice != self.voice or self._closing.is_set():
                return
            self.backend_status = text
        # Runtime snapshots acquire the runtime lock before reading speaker state.
        # Never invoke external callbacks while holding the speaker lock.
        self.on_status(text)

    def configure(self, voice: str, *, preserve_playback: bool = False) -> str:
        """Persist before changing live settings or cancelling the previous speech."""
        voice = validate_voice(voice)
        if voice.startswith("gemini:") and not gemini_tts_configured():
            raise OwnedValueError("Configure the shared Google API key before selecting Gemini TTS.")
        with self._lock:
            if self._closing.is_set():
                raise OwnedRuntimeError("Speech is already closing.")
            save_voice(voice)
            changed = voice != self.voice
            if voice != self.voice and not preserve_playback:
                self.cancel()
            self.voice = voice
            if changed:
                self.backend_status = ""
            self.enabled = True
            self.error = ""
        if changed:
            self.on_status("")
        return OwnedText("Speech settings saved. The next response will use the selected voice.")

    def select(self, voice: str) -> Any:
        key = voice.strip().casefold()
        if key in {"list", "список"}:
            return self.voices()
        if key in {"off", "выключить"}:
            self.enabled = False
            self.cancel()
            return OwnedText("Speech is disabled.")
        if key in {"auto", "ru", "on", "русский"}:
            return self.configure(DEFAULT_VOICE)
        if key == "silero":
            return self.configure("silero:xenia")
        if key in {"piper", "piper-ru"}:
            return self.configure("piper:denis")
        if key in SILERO_SPEAKERS:
            return self.configure("silero:" + key)
        if key in {"supertonic", "vulkan"}:
            return self.configure("supertonic:F1")
        if key.startswith(("silero:", "piper:", "supertonic:", "gemini:", "qwen3:")):
            return self.configure(key)
        matches = [item for item in self.voices() if voice == item["id"] or key == item["name"].casefold()]
        if len(matches) != 1:
            raise OwnedValueError("Voice not found. Open speech settings or request the voice list.")
        return self.configure(matches[0]["id"])

    def prepare(self) -> None:
        """Warm the selected GPU/CPU without opening an audio device."""
        self._enqueue("", prepare_only=True)

    def say(self, text: str, *, voice: str | None = None) -> Future | None:
        return self._enqueue(text, voice) if text.strip() else None

    def say_if_current(self, text: str, revision: int) -> Future | None:
        """Check ownership and enqueue atomically; publish errors after unlocking."""
        return self._enqueue(text, revision=revision) if text.strip() else None

    def cancel_if_current(self, revision: int) -> int:
        with self._lock:
            if revision == self._generation:
                self.cancel()
                return self._generation
            return revision

    def _enqueue(self, text: str, voice: str | None = None, *,
                 revision: int | None = None, prepare_only: bool = False) -> Future | None:
        with self._lock:
            if (not self.enabled or self._closing.is_set()
                    or (revision is not None and revision != self._generation)
                    or (prepare_only and not self.voice.startswith("supertonic:"))):
                return None
            selected = validate_voice(voice or self.voice)
            limit = 16000 if selected.startswith(("gemini:", "qwen3:")) else 4000
            future = Future()
            try:
                self._queue.put_nowait((text[:limit], selected, self._generation, future))
                self._activity_revision += 1
            except queue.Full:
                self.error = OwnedText("The speech queue is full.")
                notification = self.error
            else:
                if self._thread is None or not self._thread.is_alive():
                    self._thread = threading.Thread(target=self._loop, name="Jarvis-TTS", daemon=True)
                    self._thread.start()
                return future
        # External callbacks may acquire the desktop runtime lock.
        self._notify_error(notification)
        return None

    def _loop(self) -> None:
        engines: dict[str, Any] = {}
        supertonic_identity = None
        com_initialized = False
        try:
            while True:
                item = self._queue.get()
                future = None
                try:
                    if item is None:
                        return
                    text, voice, generation, future = item
                    with self._lock:
                        if generation != self._generation or self._closing.is_set() or not self.enabled:
                            continue
                        self._cancel = PlaybackStop(self._paused, self._playback_state,
                            lambda level, revision=generation: self._playback_level(level,revision))
                        self._phase = "SYNTHESIZING"
                        self._emitted_audio = False
                        cancel = self._cancel
                        if text:
                            self._active.set()
                    backend, selected = voice.split(":", 1)
                    if backend == "gemini":
                        from local_mode.gemini_voice import GeminiVoice
                        if "gemini" not in engines:
                            engines["gemini"] = GeminiVoice()
                        self._cloud_engine = engines["gemini"]
                        def cloud_status(message: str) -> None:
                            self._report_status(message, generation, voice)
                        def local_fallback(phrase: str) -> bytes:
                            return _cpu_audio(engines, phrase, cloud_status)
                        engines["gemini"].play(text, cancel, selected,
                            fallback=local_fallback, on_status=cloud_status)
                    elif backend == "qwen3":
                        # Keep only one audio.cpp model resident; never silently substitute a voice.
                        previous = engines.pop("supertonic", None)
                        if previous is not None:
                            previous.close()
                        supertonic_identity = None
                        if engines.get("qwen3") is not None and engines["qwen3"].cancelled:
                            engines.pop("qwen3").close()
                        if "qwen3" not in engines:
                            from local_mode.qwen_voice import QwenVoice
                            engines["qwen3"] = QwenVoice(cancel_event=cancel)
                        self._vulkan_engine = engines["qwen3"]
                        self._report_status(OwnedText("Qwen3 speech: {0}", self._vulkan_engine.gpu), generation, voice)
                        if cancel.is_set():
                            continue
                        if text:
                            engines["qwen3"].play(text, cancel, selected)
                        else:
                            engines["qwen3"].wav("Ready.", selected)
                    elif backend == "supertonic":
                        previous = engines.pop("qwen3", None)
                        if previous is not None:
                            previous.close()
                            self._vulkan_engine = None
                        def fallback_audio(phrase: str) -> bytes:
                            return _cpu_audio(engines, phrase, report_status)
                        def report_status(message: str) -> None:
                            self._report_status(message, generation, voice)
                        from local_mode.tts_models import prepare_engine
                        supertonic_identity, selection_error = prepare_engine(engines, supertonic_identity)
                        if selection_error:
                            report_status(OwnedText("Selected Supertonic weights unavailable: {0}; CPU fallback", selection_error))
                        if engines.get("supertonic") is not None and engines["supertonic"].cancelled:
                            engines.pop("supertonic").close()
                            self._vulkan_engine = None
                        if "supertonic" not in engines:
                            try:
                                report_status(OwnedText("Preparing Supertonic 3: checking files and Vulkan GPU…"))
                                from local_mode.supertonic_voice import RussianVoice
                                engines["supertonic"] = RussianVoice()
                                self._vulkan_engine = engines["supertonic"]
                                report_status(OwnedText("Supertonic 3: {0}", engines["supertonic"].gpu))
                            except Exception as error:
                                from local_mode.gemini import safe_error
                                engines["supertonic"] = None
                                report_status(OwnedText("Supertonic is unavailable: {0} Using Silero CPU. Restart to check the GPU again.", safe_error(error)))
                        if cancel.is_set():
                            continue
                        if not text:
                            # Check synthesis itself, not just driver availability.
                            # Warmup audio is neither played nor added to chat.
                            if engines["supertonic"] is not None:
                                try:
                                    engines["supertonic"].wav("Готово.", selected)
                                except Exception as error:
                                    if cancel.is_set():
                                        continue
                                    engines["supertonic"].close()
                                    engines["supertonic"] = None
                                    self._vulkan_engine = None
                                    from local_mode.gemini import safe_error
                                    report_status(OwnedText("Vulkan check failed: {0} Using Silero CPU.", safe_error(error)))
                            if engines["supertonic"] is None:
                                fallback_audio("Готово.")
                            self.error = ""
                            continue
                        if engines["supertonic"] is not None:
                            engines["supertonic"].play(text, cancel, selected,
                                fallback=fallback_audio, on_fallback=report_status)
                        else:
                            for part in speech_parts(text, 160):
                                if cancel.is_set(): break
                                play_wav(fallback_audio(part.text), cancel, part.pause_ms)
                    elif backend=="piper" and selected.startswith("managed:"):
                        from local_mode.piper_models import engine_for
                        engine=None
                        engine=engine_for(engines,voice)
                        if not cancel.is_set():
                            engine.play(text,cancel)
                    elif backend in {"silero", "piper"}:
                        if backend not in engines:
                            if backend == "silero":
                                from local_mode.silero_voice import RussianVoice
                            else:
                                from local_mode.piper_voice import RussianVoice
                            engines[backend] = RussianVoice()
                        if cancel.is_set():
                            continue
                        if backend == "silero":
                            engines[backend].play(text, cancel, selected)
                        else:
                            engines[backend].play(text, cancel)
                    else:
                        if not com_initialized:
                            import comtypes
                            import comtypes.client
                            comtypes.CoInitialize()
                            com_initialized = True
                        if "sapi" not in engines:
                            import comtypes.client
                            engines["sapi"] = comtypes.client.CreateObject("SAPI.SpVoice", dynamic=True)
                        engine = engines["sapi"]
                        matches = [item for item in engine.GetVoices() if item.Id == selected]
                        if not matches:
                            raise OwnedValueError("The selected Windows voice is no longer installed.")
                        if cancel.is_set():
                            continue
                        engine.Voice = matches[0]
                        spoken = text
                        if any(code.strip().lstrip("0") in {"419", "19"} for code in str(engine.Voice.GetAttribute("Language")).lower().split(";")):
                            from local_mode.speech_pronunciation import russian_pronunciation
                            spoken = russian_pronunciation(text)
                        while self.paused and not cancel.is_set():
                            cancel.wait(0.05)
                        if cancel.is_set():
                            continue
                        self._playback_state("SPEAKING")
                        engine.Speak(sapi_xml(spoken), 1 | 8)
                        sapi_paused = False
                        try:
                            while not engine.WaitUntilDone(100):
                                if cancel.is_set():
                                    engine.Speak("", 3)
                                    break
                                if self.paused and not sapi_paused:
                                    engine.Pause()
                                    sapi_paused = True
                                elif not self.paused and sapi_paused:
                                    engine.Resume()
                                    sapi_paused = False
                        finally:
                            if sapi_paused:
                                engine.Resume()
                    self.error = ""
                except Exception as error:
                    if self._cancel.is_set() or generation != self._generation or self._closing.is_set():
                        continue
                    from local_mode.gemini import safe_error
                    if isinstance(error, OwnedError):
                        notification = OwnedText(error.template, *error.values)
                    else:
                        diagnostic = safe_error(error)
                        notification = (diagnostic if isinstance(diagnostic, OwnedText)
                                        else OwnedText("Speech synthesis failed: {0}", diagnostic))
                    with self._lock:
                        if generation != self._generation or self._closing.is_set():
                            continue
                        self.error = notification
                    self._notify_error(notification)
                    if future is not None and not future.done():
                        if generation != self._generation or self._cancel.is_set() or self._closing.is_set():
                            future.cancel()
                        else:
                            future.set_exception(OwnedRuntimeError(notification.template, *notification.values))
                finally:
                    if (isinstance(item, tuple) and item[1].startswith("qwen3:")
                            and (item[2] != self._generation or self._cancel.is_set() or self._closing.is_set())):
                        retired = engines.pop("qwen3", None)
                        if retired is not None:
                            if self._vulkan_engine is retired:
                                self._vulkan_engine = None
                            retired.close()
                    with self._lock:
                        if self._emitted_audio:
                            self._mic_ready_at = time.monotonic() + 0.4
                        self._phase = "ONLINE"
                        self._audio_level = 0.0
                    self._active.clear()
                    if future is not None and not future.done():
                        if generation != self._generation or self._cancel.is_set() or self._closing.is_set():
                            future.cancel()
                        else:
                            future.set_result(self.backend_status)
                    self._queue.task_done()
        finally:
            for engine in engines.values():
                if callable(getattr(engine, "close", None)):
                    engine.close()
            if com_initialized:
                import comtypes
                comtypes.CoUninitialize()

    def cancel(self) -> None:
        with self._lock:
            self._generation += 1
            self.error = ""
            self._activity_revision += 1
            self._cancel.set()
            self._paused.clear()
            engines = (self._vulkan_engine, self._cloud_engine)
            while True:
                try:
                    item = self._queue.get_nowait()
                    if isinstance(item, tuple) and len(item) == 4 and not item[3].done():
                        item[3].cancel()
                    self._queue.task_done()
                except queue.Empty:
                    break

        # Engine cancellation may involve network/process teardown. The state
        # and microphone heartbeat must remain readable during teardown.
        for engine in engines:
            if engine is not None:
                engine.cancel()

    def wait(self) -> None:
        self._queue.join()

    def close(self) -> None:
        with self._lock:
            self.enabled = False
            self._closing.set()
            self.cancel()
            if self._thread and self._thread.is_alive():
                self._queue.put_nowait(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
