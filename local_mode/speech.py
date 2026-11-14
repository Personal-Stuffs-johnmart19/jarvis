"""Локальный русский STT: Vosk определяет паузу, Whisper расшифровывает PCM."""
from __future__ import annotations
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
import wave
from typing import Callable
from local_mode.messages import OwnedValueError, OwnedRuntimeError


class RussianRecognizer:
    def __init__(self, model_path: Path, microphone: int | None = None) -> None:
        from vosk import Model, SetLogLevel
        if not (model_path / "am" / "final.mdl").is_file():
            raise OwnedValueError("Vosk model not found: {0}. Run setup_local.ps1.", str(model_path))
        SetLogLevel(-1)
        self.model = Model(str(model_path))
        self.microphone = microphone
        self.engine = os.environ.get("JARVIS_ASR", "whisper")
        if self.engine not in {"whisper", "vosk"}:
            raise OwnedValueError("JARVIS_ASR must be whisper or vosk.")
        self.accurate = None

    def _decoder(self, sample_rate: int):
        from vosk import KaldiRecognizer
        return KaldiRecognizer(self.model, sample_rate)

    def _accurate(self, pcm16: bytes, sample_rate: int) -> str:
        if self.accurate is None:
            from local_mode.whisper_voice import AccurateRussianRecognizer
            self.accurate = AccurateRussianRecognizer()
        return self.accurate.transcribe(pcm16, sample_rate)

    def transcribe_wav(self, path: Path) -> str:
        """Тот же распознаватель для повторяемой проверки и пользовательских WAV."""
        with wave.open(str(path), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getcomptype() != "NONE":
                raise OwnedValueError("A mono PCM16 WAV is required.")
            if source.getnframes() > source.getframerate() * 180:
                raise OwnedValueError("Speech recordings are limited to three minutes.")
            if self.engine == "whisper":
                return self._accurate(source.readframes(source.getnframes()), source.getframerate())
            decoder = self._decoder(source.getframerate())
            texts: list[str] = []
            while chunk := source.readframes(4000):
                if decoder.AcceptWaveform(chunk):
                    texts.append(json.loads(decoder.Result()).get("text", ""))
            texts.append(json.loads(decoder.FinalResult()).get("text", ""))
            return " ".join(text for text in texts if text).strip()

    def listen(self, stop: threading.Event | None = None, timeout: float = 12, *,
               interrupted: Callable[[], bool] = lambda: False,
               on_state: Callable[[str], None] = lambda state: None) -> str:
        """Одна фраза до паузы; очередь ограничена, микрофон закрывается до действия."""
        import sounddevice as sd
        stop = stop or threading.Event()
        if stop.is_set() or interrupted():
            return ""
        chunks: queue.Queue[bytes] = queue.Queue(maxsize=100)
        overflow = threading.Event()
        info = sd.query_devices(self.microphone, "input")
        rate = int(info["default_samplerate"])
        decoder = self._decoder(rate)

        def audio_callback(data, frames, timing, status) -> None:
            if status:
                overflow.set()
            try:
                chunks.put_nowait(bytes(data))
            except queue.Full:
                overflow.set()

        captured: list[bytes] = []
        text = ""
        speech_started = False
        started = time.monotonic()
        deadline = started + timeout
        speech_deadline = started + max(timeout, 12)
        with sd.RawInputStream(samplerate=rate, blocksize=max(800, rate // 5), device=self.microphone,
                               dtype="int16", channels=1, callback=audio_callback):
            while not stop.is_set() and not interrupted() and time.monotonic() < deadline:
                try:
                    chunk = chunks.get(timeout=0.2)
                except queue.Empty:
                    continue
                captured.append(chunk)
                if overflow.is_set():
                    raise OwnedRuntimeError("Microphone audio was lost. Repeat the command.")
                if decoder.AcceptWaveform(chunk):
                    text = json.loads(decoder.Result()).get("text", "")
                    if text:
                        if not speech_started:
                            on_state("LISTENING")
                        break
                elif json.loads(decoder.PartialResult()).get("partial"):
                    if not speech_started:
                        speech_started = True
                        on_state("LISTENING")
                    # Короткий цикл автоматического прослушивания не должен обрезать начатую
                    # длинную команду через три секунды посреди фразы.
                    if timeout < 12:
                        deadline = speech_deadline
        if stop.is_set() or interrupted():
            return ""
        text = text or json.loads(decoder.FinalResult()).get("text", "")
        # Пауза определяется лёгким Vosk, а финальная расшифровка свободной речи
        # делается Whisper уже после закрытия микрофона.
        if self.engine == "whisper" and text:
            on_state("RECOGNIZING")
            recognized = self._accurate(b"".join(captured), rate)
            return "" if stop.is_set() or interrupted() else recognized
        return text


def voice_command(heard: str, wake_enabled: bool) -> str:
    """Обычный режим принимает фразу целиком; ключевое слово — только по настройке."""
    heard = heard.strip()
    wake = re.match(r"^(?:джарвис|джарвиз|жарвис|jarvis|помощник)\b[ ,.!?:—-]*(.*)$", heard, re.IGNORECASE)
    if wake:
        return wake.group(1) or "__local_listen__"
    return "" if wake_enabled else heard
