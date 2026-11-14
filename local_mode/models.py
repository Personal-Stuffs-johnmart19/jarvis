"""Разрешённые модели с Free Tier; тариф проекта задаётся в Google AI Studio."""
from __future__ import annotations
from typing import Literal

ModelName = Literal["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
MODEL_ORDER = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite")
DEFAULT_MODEL = "gemini-3.5-flash-lite"


def model_chain(model: str, enabled: bool = True) -> tuple[str, ...]:
    """Не повышаем модель автоматически и никогда не подключаем платные-only модели."""
    if model not in MODEL_ORDER:
        raise ValueError("Модель не входит в список Free Tier Jarvis.")
    return MODEL_ORDER[MODEL_ORDER.index(model):] if enabled else (model,)
