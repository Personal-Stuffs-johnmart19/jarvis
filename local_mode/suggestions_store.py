"""Постоянные пользовательские подсказки и проверка их формата."""
from __future__ import annotations
import json
from pathlib import Path
from local_mode.storage import write_json

MAX_ITEMS = 100


def validate(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if len(items) > MAX_ITEMS:
        raise ValueError('Можно сохранить не больше 100 подсказок.')
    result = []
    for label, prompt in items:
        if not isinstance(label, str) or not isinstance(prompt, str):
            raise ValueError('Название и команда должны быть текстом.')
        label, prompt = label.strip(), prompt.strip()
        if not label or not prompt:
            raise ValueError('Заполните название и текст команды.')
        if len(label) > 60 or '\n' in label or '\r' in label:
            raise ValueError('Название: одна строка, не больше 60 символов.')
        if len(prompt) > 8000:
            raise ValueError('Команда должна содержать не больше 8000 символов.')
        result.append((label, prompt))
    return result


def load(path: Path, defaults: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    if not path.exists():
        return list(defaults)
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('items'), list):
        raise ValueError('Неизвестный формат файла подсказок.')
    items = value['items']
    if any(not isinstance(item, dict) or set(item) != {'label', 'prompt'} for item in items):
        raise ValueError('Повреждён список подсказок.')
    return validate([(item['label'], item['prompt']) for item in items])


def save(path: Path, items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    checked = validate(items)
    write_json(path, {'version': 1, 'items': [{'label': label, 'prompt': prompt} for label, prompt in checked]})
    return checked
