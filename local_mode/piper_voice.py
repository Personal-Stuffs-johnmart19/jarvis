"""Local Piper synthesis with built-in Russian or verified managed voices."""
from __future__ import annotations
import io
import wave
from typing import Any
from local_mode.config import ROOT
from local_mode.messages import OwnedValueError
from local_mode.speech_text import speech_parts
from local_mode.speech_audio import play_wav
from local_mode.speech_pronunciation import russian_pronunciation

REVISION = "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd"
MODEL = ROOT / "models" / "piper" / "ru_RU-denis-medium.onnx"
MODEL_SHA256 = "15fab56e11a097858ee115545d0f697fc2a316c41a291a5362349fb870411b0a"
BASE_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/ru/ru_RU/denis/medium/"


def ensure_model() -> None:
    from local_mode.downloads import download_file
    for name, remote in ((MODEL.name, MODEL.name), (MODEL.name + ".json", MODEL.name + ".json"), (MODEL.stem + ".MODEL_CARD", "MODEL_CARD")):
        companion = name != MODEL.name
        download_file(BASE_URL + remote, MODEL.parent / name,
                      max_bytes=1024 * 1024 if companion else 70 * 1024 * 1024,
                      sha256=MODEL_SHA256 if not companion else None,
                      content_format=("json-object" if name.endswith(".json") else "utf8-text") if companion else None)


class RussianVoice:
    def __init__(self, model_path=None, speaker_id=0, language="ru") -> None:
        from piper import PiperVoice
        model_path = MODEL if model_path is None else model_path
        self.russian = language.startswith("ru")
        if not model_path.is_file():
            raise OwnedValueError("Piper voice model is missing. Install or activate a voice in Speech settings.")
        from piper import SynthesisConfig
        self.config = SynthesisConfig(length_scale=1.1, speaker_id=speaker_id)
        self.voice = PiperVoice.load(str(model_path), use_cuda=False)

    def wav(self, text: str) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            phrase = (russian_pronunciation(text) if self.russian else text).strip()
            if phrase and phrase[-1] not in ".!?…":
                phrase += "."
            self.voice.synthesize_wav(phrase, target, syn_config=self.config)
        return output.getvalue()

    def play(self, text: str, stop: Any) -> None:
        for part in speech_parts(russian_pronunciation(text) if self.russian else text, 240):
            if stop.is_set(): return
            play_wav(self.wav(part.text), stop, part.pause_ms)
