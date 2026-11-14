"""Общие категории сетевых отказов Gemini без ключей и содержимого запросов."""
from __future__ import annotations

class NetworkRouteError(RuntimeError):
    """Понятная ошибка маршрута без URL, заголовков и секретов запроса."""


def is_regional_error(status: int, message: str) -> bool:
    """Определяем регион по ответу API, не отправляя IP сторонним геосервисам."""
    if status not in {400, 403, 451}:
        return False
    text = message.lower()
    return status == 451 or any(marker in text for marker in (
        "location is not supported", "unsupported location", "unsupported region",
        "not available in your country", "not available in your region", "country is not supported",
        "region is not supported", "geographic restriction",
    ))
