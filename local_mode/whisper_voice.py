"""Локальный Whisper-small INT8: свободная русская речь без облачного STT."""
from __future__ import annotations
from local_mode.config import ROOT
from local_mode.downloads import download_file
from local_mode.messages import OwnedValueError

MODEL = ROOT / "models" / "whisper-small"
REVISION = "536b0662742c02347bc0e980a01041f333bce120"
MODEL_SHA256 = "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"
COMMAND_CONTEXT = ("Джарвис. Jarvis. Включи музыку на YouTube. Разверни окно. "
                   "Открой Google Chrome. Останови воспроизведение.")
BASE_URL = f"https://huggingface.co/Systran/faster-whisper-small/resolve/{REVISION}/"


def ensure_model() -> None:
    for filename in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        companion = filename != "model.bin"
        download_file(BASE_URL + filename, MODEL / filename,
                      max_bytes=16 * 1024 * 1024 if companion else 500 * 1024 * 1024,
                      sha256=MODEL_SHA256 if not companion else None,
                      content_format=("json-object" if filename.endswith(".json") else "utf8-text") if companion else None)


class AccurateRussianRecognizer:
    def __init__(self) -> None:
        # Такой порядок native DLL также нужен для последующего MediaPipe.
        import torch
        from faster_whisper import WhisperModel
        if not (MODEL / "model.bin").is_file():
            raise OwnedValueError("Local Whisper model not found. Run INSTALL.cmd.")
        self.model = WhisperModel(str(MODEL), device="cpu", compute_type="int8", cpu_threads=2, num_workers=1,
                                  local_files_only=True)

    def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        import numpy as np
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        if not len(audio):
            return ""
        if len(audio) > sample_rate * 180:
            raise OwnedValueError("Speech requests are limited to three minutes.")
        if sample_rate != 16000:
            positions = np.arange(round(len(audio) * 16000 / sample_rate)) * sample_rate / 16000
            audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
        # Короткие команды у границы записи распознаются стабильнее с тишиной
        # по краям. Примеры полных фраз задают лексику, но не переписывают результат.
        audio = np.pad(audio, (6400, 6400))
        segments, info = self.model.transcribe(audio, language="ru", beam_size=5, vad_filter=True,
            condition_on_previous_text=False, initial_prompt=COMMAND_CONTEXT)
        return " ".join(segment.text.strip() for segment in segments).strip()
