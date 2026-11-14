"""Автоматическое понижение модели и перенос истории без чужих подписей."""
from __future__ import annotations

import json
import logging
from typing import Callable

from google import genai
from google.genai import types

from local_mode.gemini import can_fallback, safe_error as describe_error, generate as generate_content
from local_mode.models import model_chain

logger = logging.getLogger(__name__)


def portable_history(contents: list[types.Content]) -> list[types.Content]:
    """Передаём другой модели факты и результаты, а не внутренние подписи прежней."""
    history = []
    for content in contents:
        parts = []
        for part in content.parts or []:
            if part.thought:
                continue
            if part.text:
                parts.append(types.Part.from_text(text=part.text))
            if part.function_call:
                parts.append(types.Part.from_text(text="Ранее запрошен инструмент: " + json.dumps(
                    {"name": part.function_call.name, "args": part.function_call.args}, ensure_ascii=False)))
            if part.function_response:
                parts.append(types.Part.from_text(text="Результат уже выполненного вызова; повторять действие не нужно: "
                             + json.dumps({"name": part.function_response.name,
                                           "response": part.function_response.response}, ensure_ascii=False)))
            if part.inline_data:
                parts.append(types.Part(inline_data=part.inline_data.model_copy(deep=True)))
        if parts:
            history.append(types.Content(role=content.role, parts=parts))
    return history


class ModelRouter:
    """Выбранная резервная модель остаётся активной до конца текущего сеанса."""

    def __init__(self, model: str, enabled: bool = True) -> None:
        self.models = model_chain(model, enabled)
        self.index = 0

    @property
    def active_model(self) -> str:
        return self.models[self.index]

    def generate(self, client: genai.Client, *, contents: list[types.Content] | str,
                 config: types.GenerateContentConfig | None = None,
                 generator: Callable = generate_content) -> types.GenerateContentResponse:
        """Переходим ниже только после исчерпания повторов текущей модели."""
        if self.index and isinstance(contents, list) and all(isinstance(c, types.Content) for c in contents):
            contents = portable_history(contents)
        while True:
            has_next = self.index + 1 < len(self.models)
            try:
                return generator(client, self.active_model, contents, config, fallback_available=has_next)
            except Exception as error:
                if not has_next or not can_fallback(error):
                    raise
                previous = self.active_model
                self.index += 1
                logger.warning("Модель %s недоступна: %s Переходим на %s.",
                               previous, describe_error(error), self.active_model)
                if isinstance(contents, list) and all(isinstance(c, types.Content) for c in contents):
                    contents = portable_history(contents)
