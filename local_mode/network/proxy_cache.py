"""Общее локальное хранилище маршрутов из меню, подбора и успешных запросов."""
from __future__ import annotations

import json
import logging
from local_mode.storage import write_json, synchronized
from concurrent.futures import ThreadPoolExecutor
from local_mode.config import ROOT
PROXY_DIR = ROOT / "data" / "network"

from local_mode.network.network_preferences import validate_proxy
from local_mode.network.public_proxies import parse_candidates, probe_proxy, discard_discovered

CACHE_PATH = PROXY_DIR / "proxy_cache.json"
logger = logging.getLogger(__name__)


@synchronized
def read_cached_proxies() -> list[dict]:
    """Поддерживаем прежний кэш; введённые вручную локальные прокси допустимы."""
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        entries = data.get("proxies", [{"proxy": data.get("proxy"), "source": "public"}])
        result = []
        for entry in entries[:32]:
            if not isinstance(entry, dict):
                continue
            value, source = entry.get("proxy"), entry.get("source", "public")
            if source not in {"manual", "public"}:
                continue
            try:
                value = validate_proxy(value)
            except ValueError:
                continue
            if source == "public" and parse_candidates(value) != [value]:
                continue
            if not any(item["proxy"] == value for item in result):
                result.append({"proxy": value, "source": source})
        return result
    except (OSError, ValueError, UnicodeError, AttributeError, TypeError):
        return []


def _write(entries: list[dict]) -> None:
    try:
        write_json(CACHE_PATH, {"proxies": entries[:32]})
    except OSError:
        logger.warning("Не удалось сохранить хранилище прокси на диск.")


def read_cached_proxy() -> str:
    entries = read_cached_proxies()
    return entries[0]["proxy"] if entries else ""


@synchronized
def save_cached_proxy(proxy: str, source: str = "public") -> None:
    """Добавляем без сетевых проверок, успешный маршрут ставим первым."""
    proxy = validate_proxy(proxy)
    entries = read_cached_proxies()
    existing = next((item for item in entries if item["proxy"] == proxy), None)
    source = existing["source"] if existing else source
    if source not in {"public", "manual"} or (source == "public" and parse_candidates(proxy) != [proxy]):
        raise ValueError("Недопустимый публичный адрес прокси.")
    _write([{"proxy": proxy, "source": source}] + [item for item in entries if item["proxy"] != proxy])


@synchronized
def forget_cached_proxy(proxy: str) -> None:
    entries = read_cached_proxies()
    if any(item["proxy"] == proxy for item in entries):
        _write([item for item in entries if item["proxy"] != proxy])
        discard_discovered({proxy})


def available_proxies() -> list[str]:
    """Сначала общие сохранённые адреса; поиск нужен только при пустом хранилище."""
    entries = read_cached_proxies()
    if entries:
        return [item["proxy"] for item in entries]
    from local_mode.network.public_proxies import discover_proxies
    routes = discover_proxies()
    for route in reversed(routes):
        save_cached_proxy(route)
    return routes


def recheck_cached_proxies(known: dict[str, bool] | None = None) -> list[str]:
    """После обрыва проверяем весь резерв без ключа; удаляем недоступные узлы."""
    entries = read_cached_proxies()
    known = known or {}
    pending = [item["proxy"] for item in entries if item["proxy"] not in known]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(pending, pool.map(probe_proxy, pending)))
    results.update(known)
    working = [item["proxy"] for item in entries if results[item["proxy"]]]
    dead = {item["proxy"] for item in entries if not results[item["proxy"]]}
    if dead:
        _forget_many(dead)
    logger.warning("Повторная проверка сохранённых прокси: рабочих %s из %s; недоступные удалены.",
                   len(working), len(entries))
    return working


@synchronized
def _forget_many(dead: set[str]) -> None:
    """Одна запись на проверку пула; новые маршруты других клиентов сохраняются."""
    _write([item for item in read_cached_proxies() if item["proxy"] not in dead])
    discard_discovered(dead)
