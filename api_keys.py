"""Совместимость старых модулей с единым клиентом Gemini и Windows DPAPI."""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "api_keys.json"


def get_all_keys() -> list[str]:
    keys = [os.environ.get(n, "") for n in ("GOOGLE_API_KEY", "GEMINI_API_KEY", "JARVIS_GEMINI_API_KEY")]
    keys.extend(os.environ.get("JARVIS_GEMINI_API_KEYS", "").split(","))
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        keys.extend([cfg.get("gemini_api_key", ""), *(cfg.get("gemini_api_keys") or [])])
    return list(dict.fromkeys(k.strip() for k in keys if isinstance(k, str) and k.strip()))


def next_key() -> str:
    from local_mode.credentials import load_key
    keys = get_all_keys()
    key = keys[0] if keys else load_key()
    if not key:
        raise KeyError("Настройте ключ через start_local.py --set-key.")
    return key


def add_key(key: str) -> int:
    from local_mode.credentials import save_key
    save_key(key)
    os.environ["GEMINI_API_KEY"] = key.strip()
    return 1


def mark_exhausted(key: str) -> None:
    """Совместимость: квота не обходится переключением ключей, retry ограничен."""


def generate_with_retry(model: str, contents: Any, config: Any = None, api_version: str | None = None) -> Any:
    from local_mode.model import generate_content
    return generate_content(contents=contents, config=config)


async def aio_generate(model: str, contents: Any, config: Any = None, api_version: str | None = None) -> Any:
    import asyncio
    return await asyncio.to_thread(generate_with_retry, model, contents, config, api_version)
