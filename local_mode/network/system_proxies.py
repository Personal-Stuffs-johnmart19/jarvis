"""Используем только уже настроенные прокси ОС/окружения, без сканирования портов."""
from __future__ import annotations
from urllib.request import getproxies
from local_mode.network.network_preferences import validate_proxy


def system_proxies() -> list[str]:
    """В Windows urllib читает также ProxyEnable/ProxyServer пользователя.

    PAC не исполняется. Адреса с паролями и неподдерживаемые протоколы пропускаются.
    """
    try:
        configured = getproxies()
    except (OSError, ValueError):
        return []
    result = []
    for scheme in ("https", "http", "all"):
        value = configured.get(scheme, "")
        if not value:
            continue
        try:
            value = validate_proxy(value if "://" in value else "http://" + value)
        except ValueError:
            continue
        if value not in result:
            result.append(value)
    return result
