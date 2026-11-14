"""Переключение сетевого маршрута отдельно от выбора модели и локальных инструментов."""
from __future__ import annotations

import logging
import time
from threading import Event, Lock
from typing import Callable, Iterator

import httpx

from local_mode.network.network_preferences import validate_proxy
from local_mode.network.errors import NetworkRouteError, is_regional_error
from local_mode.network.public_proxies import discover_proxies, probe_proxy, discovery_summary
from local_mode.network.system_proxies import system_proxies
from local_mode.network.proxy_cache import read_cached_proxies, save_cached_proxy, forget_cached_proxy, recheck_cached_proxies

logger = logging.getLogger(__name__)
_AUTO_LOCK = Lock()
_AUTO_ROUTE: str | None = None
_AUTO_GENERATION = 0


def reset_auto_route() -> None:
    """Новый запуск/настройки всегда начинают с обычного соединения."""
    global _AUTO_ROUTE, _AUTO_GENERATION
    with _AUTO_LOCK:
        _AUTO_ROUTE = None
        _AUTO_GENERATION += 1


def current_auto_route() -> str | None:
    with _AUTO_LOCK:
        return _AUTO_ROUTE



class RouteStream(httpx.SyncByteStream):
    """Передаём SSE без буферизации и отмечаем обрыв для следующей попытки."""

    def __init__(self, stream: httpx.SyncByteStream, failed: Callable[[], None],
                 completed: Callable[[], None]) -> None:
        self.stream, self.failed, self.completed = stream, failed, completed

    def __iter__(self) -> Iterator[bytes]:
        try:
            yield from self.stream
        except httpx.TransportError:
            self.failed()
            raise
        else:
            self.completed()

    def close(self) -> None:
        self.stream.close()


class RoutingTransport(httpx.BaseTransport):
    """Обычное соединение, настроенные прокси и ограниченный расширенный подбор."""

    def __init__(self, mode: str, proxy: str = "", *, stop: Event | None = None) -> None:
        if mode not in {"system", "auto", "proxy"}:
            raise ValueError("Неизвестный режим сети.")
        if mode == "proxy" and not proxy:
            raise ValueError("Укажите адрес прокси.")
        with _AUTO_LOCK:
            self.auto_generation = _AUTO_GENERATION
        self.mode = mode
        self.stop = stop
        self._check_cancelled()
        self.proxy = validate_proxy(proxy) if proxy else ""
        self.active: httpx.Client | None = None
        self.routes: list[str] = []
        self.discovered = False
        self.discovery_passes = 0
        self.system_routes = system_proxies() if mode == "auto" else []
        self._check_cancelled()
        self.failed = False
        self.last_discovery = 0.0
        self.last_failure = 0.0
        self.active_proxy = ""
        self.saved_proxy = ""
        self.rejected: set[str] = set()
        self.direct_attempted = False
        self.stream_failures = 0

    def _check_cancelled(self) -> None:
        if self.stop is not None and self.stop.is_set():
            raise RuntimeError("Model request cancelled")

    def _stream_failed(self) -> None:
        global _AUTO_ROUTE
        self.stream_failures += 1
        if self.stream_failures >= 2 and self.mode == "auto":
            with _AUTO_LOCK:
                if self.auto_generation == _AUTO_GENERATION and _AUTO_ROUTE == self.active_proxy:
                    _AUTO_ROUTE = None

    def _stream_completed(self) -> None:
        self.stream_failures = 0

    def _remember_route(self, response: httpx.Response) -> None:
        global _AUTO_ROUTE
        # Подтверждённый ответ API разделяется клиентами только внутри запуска.
        # Квота/перегрузка API не требуют смены сетевого маршрута.
        if self.mode == "auto" and (response.status_code < 300 or response.status_code in {429, 500, 502, 503, 504}):
            with _AUTO_LOCK:
                if self.auto_generation == _AUTO_GENERATION:
                    _AUTO_ROUTE = self.active_proxy
        # Сохраняем после настоящего ответа API, а не предварительного TLS-теста.
        if (self.mode in {"auto", "proxy"} and self.active_proxy and self.active_proxy != self.saved_proxy
                and response.status_code < 300):
            save_cached_proxy(self.active_proxy, source="manual" if self.mode == "proxy" or self.active_proxy == self.proxy or self.active_proxy in self.system_routes else "public")
            self.saved_proxy = self.active_proxy

    def _new_client(self, proxy: str | None = None) -> httpx.Client:
        self._check_cancelled()
        client = httpx.Client(proxy=proxy, verify=True, trust_env=self.mode == "system" and proxy is None, follow_redirects=False)
        return client

    def _advance(self) -> bool:
        self._check_cancelled()
        refresh = self.mode == "auto" and self.discovery_passes < 2 and time.monotonic() - self.last_discovery >= 15
        if self.mode == "auto" and not self.direct_attempted:
            if self.active:
                self.active.close()
            self.active_proxy = ""
            self.active = self._new_client()
            self.direct_attempted = True
            logger.warning("Проверяем обычное соединение перед резервом.")
            return True
        if self.mode == "auto" and not self.routes:
            candidates = ([self.proxy] if self.proxy else []) + self.system_routes + [item["proxy"] for item in read_cached_proxies()]
            self._check_cancelled()
            self.routes = list(dict.fromkeys(route for route in candidates if route not in self.rejected))
        retry_pool = bool(self.rejected) and self.discovery_passes < 2
        if not self.routes and (not self.discovered or refresh or retry_pool):
            self.discovered = True
            self.last_discovery = time.monotonic()
            if self.mode == "auto":
                logger.warning("Маршрут недоступен. Ищем публичные прокси; проверка выполняется без API-ключа.")
                self._check_cancelled()
                found = (discover_proxies(force=True, exclude=self.rejected)
                         if self.rejected else discover_proxies())
                self._check_cancelled()
                self.discovery_passes += 1
                for route in reversed(found):
                    if route not in self.rejected:
                        save_cached_proxy(route)
                self.routes.extend(route for route in found if route not in self.rejected)
        if not self.routes:
            return False
        self._check_cancelled()
        if self.active:
            self.active.close()
        self.active_proxy = self.routes.pop(0)
        self.active = self._new_client(self.active_proxy)
        logger.warning("Используется резервный HTTPS-маршрут к Google с проверкой сертификата.")
        return True

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        global _AUTO_ROUTE
        self._check_cancelled()
        if (request.url.scheme != "https" or request.url.host != "generativelanguage.googleapis.com"
                or request.url.port not in {None, 443}):
            raise NetworkRouteError("Сетевой резерв разрешён только для официального HTTPS API Gemini.")
        if self.stream_failures >= 2:
            # Один обрыв повторяем на том же маршруте; повторный — через резерв.
            self.stream_failures = 0
            if self.active_proxy:
                self.rejected.add(self.active_proxy)
            if not self._advance():
                raise NetworkRouteError("Поток Gemini прервался. Рабочий резервный маршрут не найден.")
        if self.failed:
            if time.monotonic() - self.last_failure < 15:
                raise NetworkRouteError("Рабочий маршрут к Gemini пока не найден. Автоматический повтор доступен через 15 секунд. " + discovery_summary())
            self.failed, self.discovered = False, False
            self.discovery_passes = 0
            self.system_routes = system_proxies() if self.mode == "auto" else []
            self._check_cancelled()
            if self.active:
                self.active.close()
            self.active = None
            self.routes.clear()
            self.rejected.clear()
            self.direct_attempted = False
        if self.active is None:
            # Сохранённый на диске прокси — только резерв. В новом сеансе
            # сначала выполняется настоящий запрос по обычному соединению.
            with _AUTO_LOCK:
                preferred = _AUTO_ROUTE if self.auto_generation == _AUTO_GENERATION else None
            self.active_proxy = (preferred or "") if self.mode == "auto" else self.proxy if self.mode == "proxy" else ""
            self.saved_proxy = self.active_proxy
            self.active = self._new_client(self.active_proxy or None)
            self.direct_attempted = not bool(self.active_proxy)
            if self.mode == "auto" and self.active_proxy:
                logger.info("Используется прокси, уже подтверждённый ответом API в текущем запуске.")
            if self.mode == "proxy":
                self.discovered = True
        # Тело читается один раз: при смене маршрута повторяется только API-запрос.
        request.read()
        self._check_cancelled()
        regional = False
        attempts = 0
        deadline = time.monotonic() + 180
        network_failures = 0
        checked: dict[str, bool] = {}
        while True:
            self._check_cancelled()
            attempts += 1
            response = None
            try:
                # Плохой прокси не должен занимать полный таймаут модели.
                original_timeout = request.extensions.get("timeout")
                if self.active_proxy:
                    limits = dict(original_timeout or {})
                    for kind, maximum in (("connect", 5), ("read", 15), ("write", 15), ("pool", 5)):
                        value = limits.get(kind)
                        limits[kind] = min(value, maximum) if value is not None else maximum
                    request.extensions["timeout"] = limits
                try:
                    # close() may run concurrently when the broker disconnects.
                    # Keep this physical client stable; never dereference None.
                    active = self.active
                    self._check_cancelled()
                    if active is None:
                        raise RuntimeError("Model transport was closed")
                    response = active.send(request, stream=True)
                finally:
                    if original_timeout is None:
                        request.extensions.pop("timeout", None)
                    else:
                        request.extensions["timeout"] = original_timeout
                if self.stop is not None and self.stop.is_set():
                    response.close()
                    self._check_cancelled()
                media_type = response.headers.get("content-type", "").lower()
                if response.status_code == 200 and "text/event-stream" in media_type:
                    # SSE читается SDK по частям. После начала потока повторяет
                    # только планировщик, до исполнения каких-либо инструментов.
                    self._remember_route(response)
                    response.stream = RouteStream(response.stream, self._stream_failed, self._stream_completed)
                    return response
                # Ошибки читаем целиком для диагностики и выбора маршрута.
                response.read()
                if self.stop is not None and self.stop.is_set():
                    response.close()
                    self._check_cancelled()
                html_response = "text/html" in media_type
                proxy_failure = response.status_code == 407 or (response.status_code in {502, 503, 504} and "application/json" not in media_type)
                if response.status_code not in {400, 403, 451} and not html_response and not proxy_failure:
                    self._remember_route(response)
                    return response
                region_block = is_regional_error(response.status_code, response.text[:65536])
                html_block = html_response or proxy_failure
                if not region_block and not html_block:
                    return response
                regional = regional or region_block
                response.close()
                logger.warning("Google отклонил региональный или сетевой доступ. Проверяем другой маршрут.")
            except httpx.TransportError:
                if response is not None:
                    response.close()
                self._check_cancelled()
                network_failures += 1
                if self.active_proxy and network_failures == 1:
                    logger.warning("Обрыв соединения. Проверяем текущий HTTPS-прокси без API-ключа.")
                    self._check_cancelled()
                    checked[self.active_proxy] = probe_proxy(self.active_proxy)
                    self._check_cancelled()
                    if not checked[self.active_proxy]:
                        network_failures = 2
                if network_failures < 2:
                    attempts -= 1
                    logger.warning("Кратковременный сбой сети. Повторяем запрос через тот же маршрут и модель.")
                    if self.stop is not None:
                        self.stop.wait(1)
                        self._check_cancelled()
                    else:
                        time.sleep(1)
                    continue
                if self.mode == "auto":
                    self._check_cancelled()
                    self.routes = [route for route in recheck_cached_proxies(checked)
                                   if route != self.active_proxy and route not in self.rejected]
                    self._check_cancelled()
                elif self.mode == "proxy" and checked.get(self.active_proxy) is False:
                    forget_cached_proxy(self.active_proxy)
            network_failures = 0
            self._check_cancelled()
            if self.mode == "auto" and self.active_proxy:
                with _AUTO_LOCK:
                    if self.auto_generation == _AUTO_GENERATION and _AUTO_ROUTE == self.active_proxy:
                        _AUTO_ROUTE = None
                self.rejected.add(self.active_proxy)
                # Живой TLS-маршрут сохраняем: сбой самого API не доказывает отказ прокси.
                if self.active_proxy not in checked or not checked[self.active_proxy]:
                    forget_cached_proxy(self.active_proxy)
                self.saved_proxy = ""
            checked = {}
            if attempts >= 18 or time.monotonic() >= deadline or not self._advance():
                self.failed = True
                self.last_failure = time.monotonic()
                reason = "Google ограничил доступ по региону." if regional else "Не удалось подключиться к Gemini."
                advice = (" Указанный прокси не работает: проверьте адрес, порт и запуск локального прокси."
                          if self.mode == "proxy" else " Ни обычное соединение, ни резервные маршруты не ответили. " + discovery_summary() +
                          " Автоматический повтор доступен через 15 секунд. Если сеть блокирует публичные прокси, нужен рабочий системный VPN или собственный прокси.")
                raise NetworkRouteError(reason + advice) from None

    def close(self) -> None:
        if self.active is not None:
            self.active.close()
            self.active = None
