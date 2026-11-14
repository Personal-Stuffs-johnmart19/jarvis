"""Validated controls supported by the Supertonic audio.cpp adapter only."""
from __future__ import annotations
import json
import math
import os
from local_mode.config import ROOT
from local_mode.storage import write_json

LANGUAGES = ("en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id", "it", "lt", "lv", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi", "na")
DEFAULTS = {"language": "ru", "automatic_speed": True, "speed": 1.0, "inference_steps": 8}
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "language": {"enum": list(LANGUAGES)}, "automatic_speed": {"type": "boolean"},
    "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0},
    "inference_steps": {"type": "integer", "minimum": 1, "maximum": 32}},
    "required": list(DEFAULTS)}


def settings_path():
    return ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data") / "tts-controls.json"


def validate(values: dict) -> dict:
    from jsonschema import Draft202012Validator
    Draft202012Validator(SCHEMA).validate(values)
    if not math.isfinite(values["speed"]):
        raise ValueError("Speech speed must be finite")
    return dict(values)


def read() -> dict:
    path = settings_path()
    if not path.exists():
        return dict(DEFAULTS)
    if path.stat().st_size > 4096:
        raise ValueError("Invalid TTS controls")
    return validate(json.loads(path.read_text(encoding="utf8")))


def save(values: dict) -> dict:
    validated = validate(values)
    write_json(settings_path(), validated)
    return validated


def request_options(text: str) -> tuple[str, dict]:
    from local_mode.speech_text import speech_speed
    controls = read()
    return controls["language"], {"num_inference_steps": controls["inference_steps"],
        "text_chunk_size": 160, "speed": speech_speed(text) if controls["automatic_speed"] else controls["speed"]}


def describe() -> dict:
    from jsonschema import ValidationError
    try:
        return {"values": read(), "languages": list(LANGUAGES)}
    except (OSError, ValueError, ValidationError):
        return {"values": dict(DEFAULTS), "languages": list(LANGUAGES), "warning": "invalid_saved_controls"}
