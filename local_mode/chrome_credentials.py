"""Токены Playwright: Windows DPAPI, строго отдельно по каналу и профилю."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from local_mode.config import ROOT
from local_mode.credentials import save_key, load_key

DIRECTORY = ROOT / "data" / "credentials" / "chrome"


def token_path(profile: str, channel: str = "chrome") -> Path:
    if not re.fullmatch(r"Default|Profile [0-9]+", profile) or channel not in {"chrome","msedge","chromium"}:
        raise ValueError("Выберите существующий профиль браузера.")
    identifier = hashlib.sha256((channel + ":" + profile).encode()).hexdigest()
    return DIRECTORY / (identifier + ".dpapi")


def normalize_token(value: str) -> str:
    value = value.strip()
    prefix = "PLAYWRIGHT_MCP_EXTENSION_TOKEN="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,512}={0,2}",value):
        raise ValueError("Вставьте токен Playwright или строку PLAYWRIGHT_MCP_EXTENSION_TOKEN=… без других команд.")
    return value


def save_token(profile: str, value: str, channel: str = "chrome") -> None:
    save_key(normalize_token(value),token_path(profile,channel))


def load_token(profile: str, channel: str = "chrome") -> str | None:
    value = load_key(token_path(profile,channel))
    return normalize_token(value) if value else None


def has_token(profile: str, channel: str = "chrome") -> bool:
    return token_path(profile,channel).is_file()


def delete_token(profile: str, channel: str = "chrome") -> None:
    token_path(profile,channel).unlink(missing_ok=True)
