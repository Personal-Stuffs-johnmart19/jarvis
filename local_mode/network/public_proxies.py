"""Ограниченный поиск публичных прокси с проверкой TLS Google без API-ключа."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from itertools import zip_longest
from ipaddress import ip_address
import logging
import json
import random
import time
from threading import Lock
from urllib.parse import urlsplit

import httpx

from local_mode.network.network_preferences import validate_proxy
from local_mode.network.errors import is_regional_error

SOURCES = (
    "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/https.txt",
    "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
    "https://vakhov.github.io/fresh-proxy-list/http.txt",
    "https://raw.githubusercontent.com/relayglass/free-proxy-list/main/protocol/http/http.txt",
    "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
    "https://api.proxyscraper.com/v1/http.txt",
)
PROBE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_BYTES = 131072
MAX_CANDIDATES = 256
BATCH_SIZE = 24
MAX_ROUTES = 8
NEGATIVE_CACHE_SECONDS = 15
POSITIVE_CACHE_SECONDS = 120
logger = logging.getLogger(__name__)
_DISCOVERY_LOCK = Lock()
_LAST_DISCOVERY: tuple[float, list[str]] | None = None
_LAST_REPORT: dict = {}
_LAST_SOURCES: dict = {}


def parse_candidates(text: str) -> list[str]:
    """Данные списка не могут направить проверку на localhost или внутреннюю сеть."""
    candidates = []
    seen = set()
    for line in text.splitlines()[:4096]:
        try:
            value = validate_proxy(line if "://" in line else "http://" + line)
            address = ip_address(urlsplit(value).hostname)
        except ValueError:
            continue
        if address.is_global and not address.is_multicast and not address.is_reserved and value not in seen:
            candidates.append(value)
            seen.add(value)
    return candidates


def download_candidates() -> list[str]:
    """Загружаем ограниченный объём только с заранее заданных HTTPS-источников."""
    global _LAST_SOURCES
    def download(source: str) -> list[str]:
        deadline = time.monotonic() + 12
        try:
            with client.stream("GET", source) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    content.extend(chunk[:MAX_BYTES - len(content)])
                    if len(content) >= MAX_BYTES or time.monotonic() >= deadline:
                        break
            candidates = parse_candidates(content.decode("utf-8", errors="ignore"))
            random.shuffle(candidates)
            return candidates
        except httpx.HTTPError:
            return []
    # Недоступный GitHub не задерживает загрузку Pages/CDN и других списков.
    with httpx.Client(timeout=httpx.Timeout(5, connect=3), follow_redirects=False) as client:
        with ThreadPoolExecutor(max_workers=10) as pool:
            groups = list(pool.map(download, SOURCES))
    _LAST_SOURCES = {"total": len(SOURCES), "available": sum(bool(group) for group in groups)}
    for number, group in enumerate(groups, 1):
        logger.warning("Список прокси %s: допустимых адресов %s.", number, len(group))
    # Большой непроверенный список не должен вытеснять остальные источники.
    candidates = []
    seen = set()
    for row in zip_longest(*groups):
        for proxy in row:
            if proxy and proxy not in seen:
                candidates.append(proxy)
                seen.add(proxy)
                if len(candidates) == MAX_CANDIDATES:
                    return candidates
    return candidates


def probe_result(proxy: str = "") -> str:
    """Возвращаем безопасную категорию сбоя вместо исключения с адресом запроса."""
    try:
        with httpx.Client(proxy=validate_proxy(proxy) if proxy else None, timeout=httpx.Timeout(8, connect=5), verify=True,
                          trust_env=not bool(proxy), follow_redirects=False) as client:
            with client.stream("GET", PROBE_URL) as response:
                if response.status_code == 407:
                    return "прокси требует авторизацию"
                if response.status_code == 451:
                    return "ограничение региона Google"
                if (response.status_code in {200, 400, 401, 403, 404}
                        and "application/json" in response.headers.get("content-type", "")):
                    deadline = time.monotonic() + 10
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        if time.monotonic() > deadline:
                            return "таймаут"
                        if len(body) + len(chunk) > 65536:
                            return "ответ HTTP или формат"
                        body.extend(chunk)
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError):
                        return "ответ HTTP или формат"
                    if isinstance(payload, dict) and (isinstance(payload.get("error"), dict) or isinstance(payload.get("models"), list)):
                        error = payload.get("error")
                        message = str(error.get("message", "")) if isinstance(error, dict) else ""
                        if is_regional_error(response.status_code, message):
                            return "ограничение региона Google"
                        return "успех"
                return "ответ HTTP или формат"
    except httpx.TimeoutException:
        return "таймаут"
    except httpx.ProxyError as error:
        return "прокси требует авторизацию" if "407" in str(error) else "прокси отклонил CONNECT"
    except httpx.ConnectError as error:
        return "сертификат TLS" if "certificate" in str(error).lower() else "соединение"
    except (httpx.HTTPError, ValueError):
        return "другая сетевая ошибка"


def probe_proxy(proxy: str) -> bool:
    """Проверяем HTTPS CONNECT и сертификат Google без ключа и личных данных."""
    return probe_result(proxy) == "успех"


def _discover_proxies(*, exclude: set[str] | None = None) -> list[str]:
    """Непрерывная очередь проверок: медленный адрес не задерживает весь пакет."""
    global _LAST_REPORT
    deadline = time.monotonic() + 75
    candidates = [route for route in download_candidates() if route not in (exclude or set())]
    routes: list[str] = []
    counts = Counter()
    checked = 0
    remaining = iter(candidates)
    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
        pending = {pool.submit(probe_result, route): route for route in
                   [next(remaining, None) for _ in range(BATCH_SIZE)] if route}
        while pending and time.monotonic() < deadline:
            done, _ = wait(pending, timeout=max(0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                route = pending.pop(future)
                result = future.result()
                counts[result] += 1
                checked += 1
                if result == "успех":
                    routes.append(route)
            if checked % 16 == 0 or routes and len(routes) >= MAX_ROUTES:
                logger.warning("Проверено прокси: %s/%s; HTTPS-маршрутов к Google: %s.",
                               checked, len(candidates), len(routes))
            if len(routes) >= MAX_ROUTES:
                break
            for _ in done:
                route = next(remaining, None)
                if route and time.monotonic() < deadline:
                    pending[pool.submit(probe_result, route)] = route
        for future in pending:
            future.cancel()
    logger.warning("Результаты проверки: %s.", ", ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "нет кандидатов")
    _LAST_REPORT = {"candidates": len(candidates), "checked": checked, "reasons": dict(counts), "sources": dict(_LAST_SOURCES)}
    return routes[:MAX_ROUTES]


def discover_proxies(*, force: bool = False, exclude: set[str] | None = None) -> list[str]:
    """Пустой результат устаревает через 15 секунд; рабочий резерв — через две минуты."""
    global _LAST_DISCOVERY
    with _DISCOVERY_LOCK:
        if not force and _LAST_DISCOVERY is not None:
            age = time.monotonic() - _LAST_DISCOVERY[0]
            cached = [route for route in _LAST_DISCOVERY[1] if route not in (exclude or set())]
            ttl = POSITIVE_CACHE_SECONDS if _LAST_DISCOVERY[1] else NEGATIVE_CACHE_SECONDS
            if age < ttl and (cached or not _LAST_DISCOVERY[1]):
                return cached
        routes = _discover_proxies(exclude=exclude) if exclude else _discover_proxies()
        _LAST_DISCOVERY = (time.monotonic(), list(routes))
        return routes


def discard_discovered(routes: set[str]) -> None:
    """Провалившиеся маршруты не возвращаются из недавнего результата подбора."""
    global _LAST_DISCOVERY
    with _DISCOVERY_LOCK:
        if _LAST_DISCOVERY is not None:
            _LAST_DISCOVERY = (_LAST_DISCOVERY[0], [route for route in _LAST_DISCOVERY[1] if route not in routes])


def discovery_summary() -> str:
    report = dict(_LAST_REPORT)
    sources = report.get("sources", {})
    source_note = f"Источников доступно: {sources['available']}/{sources['total']}. " if sources else ""
    if not report.get("candidates"):
        return source_note + "Источники списков не вернули допустимых адресов."
    reasons = "; ".join(f"{key}: {count}" for key, count in sorted(report.get("reasons", {}).items()))
    return source_note + f"Проверено {report.get('checked', 0)} из {report['candidates']} адресов. {reasons}"


def discovery_report() -> dict:
    """Return detached diagnostic counters without changing discovery behavior."""
    report = dict(_LAST_REPORT)
    return {**report, "sources": dict(report.get("sources", {})), "reasons": dict(report.get("reasons", {}))}
