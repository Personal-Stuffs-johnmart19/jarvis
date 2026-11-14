"""Настройки локального режима; ключи хранятся отдельно от предпочтений."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Literal
import json
from pydantic import BaseModel, ConfigDict, Field
from local_mode.models import ModelName, MODEL_ORDER, DEFAULT_MODEL

ROOT = Path(__file__).resolve().parent.parent
FREE_MODELS = set(MODEL_ORDER)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: ModelName = DEFAULT_MODEL
    auto_fallback: bool = Field(default=True, strict=True)
    vosk_model: Path = ROOT / "models" / "vosk-model-small-ru-0.22"
    browser_profile: Path = ROOT / "data" / "local-browser"
    browser_channel: Literal["chrome", "msedge", "chromium"] = "chrome"
    window_title: str = Field(default="", max_length=200)
    music_query: str = Field(default="спокойная музыка без слов", min_length=1, max_length=200)
    max_rounds: int = Field(default=12, ge=1, le=32)
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    microphone: int | None = None

    @classmethod
    def from_env(cls, **overrides: object) -> Settings:
        preferences = ROOT / "data" / "preferences.json"
        saved = json.loads(preferences.read_text(encoding="utf-8-sig")) if preferences.is_file() else {}
        data = {"model": os.environ.get("JARVIS_MODEL", saved.get("model", DEFAULT_MODEL)),
                "browser_channel": os.environ.get("JARVIS_BROWSER", saved.get("browser_channel", "chrome"))}
        data["auto_fallback"] = saved.get("auto_fallback", True)
        data["microphone"] = saved.get("microphone")
        if "JARVIS_AUTO_FALLBACK" in os.environ:
            value = os.environ["JARVIS_AUTO_FALLBACK"].casefold()
            if value not in {"true", "false", "1", "0"}:
                raise ValueError("JARVIS_AUTO_FALLBACK: используйте true/false или 1/0.")
            data["auto_fallback"] = value in {"true", "1"}
        if os.environ.get("VOSK_MODEL_PATH"):
            data["vosk_model"] = os.environ["VOSK_MODEL_PATH"]
        data.update({key: value for key, value in overrides.items() if value is not None})
        return cls.model_validate(data)


def save_model_preferences(settings: Settings) -> None:
    """Сохраняем только явный выбор, оставляя настройки браузера на месте."""
    from local_mode.storage import write_json
    path = ROOT / "data" / "preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
    values.update(model=settings.model, auto_fallback=settings.auto_fallback)
    write_json(path, values)
