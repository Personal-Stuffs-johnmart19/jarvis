"""App-owned message resources. Never infer a locale from arbitrary response text."""
from __future__ import annotations
from functools import lru_cache
import json
from pathlib import Path
import re

_ROOT = Path(__file__).with_name("locales")

@lru_cache(maxsize=2)
def _catalog(locale: str) -> dict[str, str]:
    # Locale names are allowlisted; they are never filesystem paths from a caller.
    name = locale if locale in {"en", "ru"} else "en"
    return json.loads((_ROOT / (name + ".json")).read_text(encoding="utf-8"))

def message(template: str, *values: object, locale: str = "en") -> str:
    catalog = _catalog(locale if locale in {"en", "ru"} else "en")
    text = catalog.get(template, template)
    return re.sub(r"\{(\d+)\}", lambda match: str(values[int(match[1])])
                  if int(match[1]) < len(values) else match[0], text)

@lru_cache(maxsize=1)
def _labels() -> dict:
    return json.loads((_ROOT / "labels.en.json").read_text(encoding="utf-8"))

def state_label(state: str, *, locale: str = "en") -> str:
    return message(_labels()["states"].get(state, state), locale=locale)

def tool_label(tool: str, *, locale: str = "en") -> str:
    return message(_labels()["tools"].get(tool, tool), locale=locale)

@lru_cache(maxsize=1)
def _speech_samples() -> dict[str, str]:
    return json.loads((_ROOT / "speech-previews.json").read_text(encoding="utf-8"))

def speech_preview(language: str) -> str:
    return _speech_samples().get(language, _speech_samples()["en"])


class OwnedError(Exception):
    def __init__(self, template: str, *values):
        self.template, self.values = template, values
        super().__init__(message(template, *values))

    def payload(self):
        return {"source": "jarvis", "message": self.template, "values": list(self.values)}

class OwnedValueError(OwnedError, ValueError):
    pass

class OwnedRuntimeError(OwnedError, RuntimeError):
    pass


class OwnedText(str):
    """String-compatible status text with explicit translatable provenance."""
    def __new__(cls, template: str, *values):
        item = super().__new__(cls, message(template, *values))
        item.template, item.values = template, values
        return item

    def payload(self):
        return {"source":"jarvis", "message":self.template, "values":list(self.values)}


def text_payload(value: str):
    return value.payload() if isinstance(value, OwnedText) else str(value)
