"""Живая Gemini → потоковая Supertonic Vulkan, выход звука заглушён."""

import json, threading, time
from pathlib import Path
from unittest.mock import patch
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.voice import LocalSpeaker
from local_mode.streaming_speech import SpeechStream

started = time.monotonic()
timings = {}
errors = []
speaker = LocalSpeaker()
speaker.on_error = errors.append


class SilentOutput:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass

    def write(self, data):
        timings.setdefault("first_pcm_seconds", time.monotonic() - started)
        time.sleep(0.002)


stop = threading.Event()
bot = LocalAgent(Settings.from_env(), stop)
warmup_started=time.monotonic()
speaker.prepare()
speaker.wait()
warmup_seconds=time.monotonic()-warmup_started
started=time.monotonic()
stream = SpeechStream(speaker, lambda: True, stop)


def text(value):
    if value:
        timings.setdefault("first_text_seconds", time.monotonic() - started)
    stream.feed(value)


bot.on_text = text
try:
    with patch("sounddevice.OutputStream", side_effect=SilentOutput):
        # Модель прогрета так же, как при обычном запуске приложения.
        result = bot.run(
            "Напиши ровно 12 отдельных коротких предложений о космосе для ребёнка. Каждое предложение минимум шесть слов. Без инструментов, списков и кода."
        )
        timings["model_complete_seconds"] = time.monotonic() - started
        stream.finish(result)
        speaker.wait()
        assert result.get("ok") and timings.get("first_pcm_seconds") is not None, errors
        timings["speech_started_before_model_complete"] = (
            timings["first_pcm_seconds"] < timings["model_complete_seconds"]
        )
        report = {
            "timings": timings,
            "warmup_seconds": warmup_seconds,
            "backend": speaker.backend_status,
            "errors": errors,
            "audio_written_to_device": False,
        }
        Path(".cache/streaming-tts-result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
finally:
    stop.set()
    stream.abort()
    speaker.close()
    bot.close()
