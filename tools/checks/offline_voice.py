"""Повторяемая проверка русского TTS→ASR→маршрут, без звука и действий на ПК."""
import json
from local_mode.piper_voice import RussianVoice
from local_mode.speech import RussianRecognizer
from local_mode.assistant import local_request
from local_mode.config import ROOT, Settings


def check() -> dict:
    settings = Settings.from_env()
    recognizer = RussianRecognizer(settings.vosk_model)
    voice = RussianVoice()
    results = []
    # Эталонные синтезированные SAPI Irina WAV проверяют ASR, независимо от
    # произношения случайно выбранного выходного голоса пользователя.
    for name, phrase, expected in (("maximize", "разверни окно", "maximize_window"),
                                   ("youtube", "включи музыку на YouTube", "youtube_play")):
        path = ROOT / "tests/python/local" / "fixtures" / ("ru_" + name + ".wav")
        text = recognizer.transcribe_wav(path)
        request = local_request(text, settings)
        results.append({"phrase": phrase, "recognized": text, "expected": expected,
                        "route": request.action if request else None,
                        "ok": request is not None and request.action == expected})
    # Отдельно проверяем, что выходной Piper строит настоящий WAV без звука.
    import io, wave
    with wave.open(io.BytesIO(voice.wav("Проверка русской озвучки.")), "rb") as generated:
        tts_valid = generated.getnframes() > generated.getframerate() // 2
    return {"ok": tts_valid and all(result["ok"] for result in results), "tts_wav_valid": tts_valid, "results": results}


if __name__ == "__main__":
    result = check()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
