"""Локальные настройки маршрута к Gemini без изменения настроек Windows."""
from __future__ import annotations

import json
from pathlib import Path
from local_mode.config import ROOT
PROXY_DIR = ROOT / "data" / "network"
from urllib.parse import urlsplit
from local_mode.storage import write_json

NETWORK_PATH = PROXY_DIR / "network_preferences.json"


def validate_proxy(value: str) -> str:
    """Разрешаем HTTP/HTTPS-прокси без паролей, путей и управляющих символов."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(c.isspace() for c in value):
        raise ValueError("Укажите адрес прокси вида http://host:port без пробелов.")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port
                 and parsed.username is None and parsed.password is None
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Нужен HTTP/HTTPS-прокси с портом, без логина, пароля, пути и параметров.")
    return value.rstrip("/")


def read_network_preferences(path: Path | None = None) -> dict:
    """По умолчанию выбираем маршрут автоматически, начиная с обычного соединения."""
    path = NETWORK_PATH if path is None else path
    if not path.exists():
        return {"mode": "auto", "proxy": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError("Исправьте или удалите network_preferences.json: файл повреждён.") from error
    if (not isinstance(data, dict) or set(data) != {"mode", "proxy"} or not isinstance(data["mode"], str)
            or data["mode"] not in {"system", "auto", "proxy"} or not isinstance(data["proxy"], str)):
        raise ValueError("Некорректные сетевые настройки в network_preferences.json.")
    if data["proxy"]:
        validate_proxy(data["proxy"])
    if data["mode"] == "proxy" and not data["proxy"]:
        raise ValueError("Для режима прокси нужен его адрес.")
    return data


def save_network_preferences(mode: str, proxy: str = "", path: Path | None = None) -> None:
    """Сохраняем режим; введённый адрес добавляем в общее хранилище маршрутов."""
    path = NETWORK_PATH if path is None else path
    options = network_options(mode, proxy)
    proxy = options["proxy"]
    write_json(path, options)
    if proxy:
        from local_mode.network.proxy_cache import save_cached_proxy
        save_cached_proxy(proxy, source="manual")


def network_options(mode: str, proxy: str = "") -> dict[str, str]:
    """Единая проверка для сохранения, диагностики и временного клиента SDK."""
    if mode not in {"system", "auto", "proxy"}:
        raise ValueError("Неизвестный режим сети.")
    if mode == "system":
        return {"mode": mode, "proxy": ""}
    proxy = validate_proxy(proxy) if proxy else ""
    if mode == "proxy" and not proxy:
        raise ValueError("Укажите адрес прокси.")
    return {"mode": mode, "proxy": proxy}


def prepare_startup_network() -> dict[str, str]:
    """Каждый запуск — автоматический выбор; ручной адрес сохраняется как резерв."""
    try:
        saved = read_network_preferences()
    except (OSError, ValueError):
        saved = {"mode": "auto", "proxy": ""}
        save_network_preferences("auto")
    if saved["mode"] != "auto":
        save_network_preferences("auto", saved["proxy"])
    return {"mode": "auto", "proxy": saved["proxy"]}
