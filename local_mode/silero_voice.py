"""Silero v5.5: локальный синтез русского текста на CPU, без облачного API."""
from __future__ import annotations
import io
import wave
from threading import Event
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import numpy as np
from local_mode.config import ROOT
from local_mode.voice_settings import SILERO_SPEAKERS
from local_mode.speech_text import speech_parts, silero_ssml
from local_mode.speech_audio import play_wav
from local_mode.speech_pronunciation import russian_pronunciation

MODEL = ROOT / "models" / "silero" / "v5_5_ru.pt"
URL = "https://models.silero.ai/models/tts/ru/v5_5_ru.pt"
SHA256 = "50081637b602126ee06cb3bc8a744d25651d2da149ee8864b9a379bfdd934437"
MIRROR = "https://huggingface.co/JackGauter/tatarby-models/resolve/4fd0dd970517bfabb77ab0e1461aeb59e9b46319/tts/silero-v5-5-ru.pt"
SAMPLE_RATE = 48000


def ensure_model() -> None:
    from local_mode.downloads import download_file
    import requests
    # Зеркало закреплено по revision и SHA256; официальный хост остаётся резервом.
    for url in (MIRROR, URL):
        try:
            download_file(url, MODEL, max_bytes=200 * 1024 * 1024, sha256=SHA256)
            return
        except (requests.RequestException, ValueError):
            if url == URL:
                raise


def phrases(text: str) -> list[str]:
    """Ограничиваем длину фрагмента: отмена не ждёт синтеза целого длинного ответа."""
    return [part.text for part in speech_parts(text, 240)]



class RussianVoice:
    def __init__(self) -> None:
        import hashlib
        import torch
        if not MODEL.is_file():
            raise ValueError("Модель Silero не установлена. Перезапустите Jarvis для автоматической установки.")
        with MODEL.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise ValueError("Контрольная сумма Silero не совпала. Перезапустите Jarvis для восстановления.")
        # Загружаем только закреплённый официальный пакет после проверки SHA256.
        self.model = torch.package.PackageImporter(str(MODEL)).load_pickle("tts_models", "model")
        self.model.to(torch.device("cpu"))
        self._ssml_enabled = True

    def audio(self, text: str, speaker: str = "xenia") -> np.ndarray:
        import torch
        if speaker not in SILERO_SPEAKERS:
            raise ValueError("Неизвестный голос Silero.")
        text = russian_pronunciation(text)
        with torch.inference_mode():
            options = dict(speaker=speaker, sample_rate=SAMPLE_RATE, put_accent=True, put_yo=True)
            if self._ssml_enabled:
                try:
                    audio = self.model.apply_tts(ssml_text=silero_ssml(text), **options)
                except (ValueError, RuntimeError) as error:
                    # Некоторые сборки Silero не разбирают SSML из-за внутренней
                    # ошибки. Повторяем только такие отказы обычным текстом;
                    # ошибки модели и памяти передаются вызывающему коду.
                    message = str(error).lower()
                    if not any(marker in message for marker in
                               ("failed to parse ssml", "failed to process ssml", "invalid xml format")):
                        raise
                    self._ssml_enabled = False
                    audio = self.model.apply_tts(text=text, **options)
            else:
                audio = self.model.apply_tts(text=text, **options)
            return audio.detach().cpu().numpy()

    def wav(self, text: str, speaker: str = "xenia") -> bytes:
        import numpy as np
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(SAMPLE_RATE)
            for part in speech_parts(russian_pronunciation(text), 240):
                target.writeframes((np.clip(self.audio(part.text, speaker), -1, 1) * 32767).astype("<i2").tobytes())
                target.writeframes(bytes(SAMPLE_RATE * part.pause_ms // 1000 * 2))
        return output.getvalue()

    def play(self, text: str, stop: Event, speaker: str = "xenia") -> None:
        for part in speech_parts(russian_pronunciation(text), 240):
            if stop.is_set(): return
            play_wav(self.wav(part.text, speaker), stop, part.pause_ms)
