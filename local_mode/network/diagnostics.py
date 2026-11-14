"""Diagnose the selected route separately from model access and quotas."""
from __future__ import annotations
from typing import Any
from local_mode.messages import OwnedText
from local_mode.network.network_preferences import network_options
from local_mode.network.system_proxies import system_proxies
from local_mode.network.public_proxies import probe_result, discover_proxies, discovery_report
from local_mode.network.proxy_cache import recheck_cached_proxies, save_cached_proxy


_PROBE_MESSAGES = {
    'прокси требует авторизацию': ('Normal connection: proxy authentication required. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: proxy authentication required. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'ограничение региона Google': ('Normal connection: Google region restriction. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: Google region restriction. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'таймаут': ('Normal connection: timeout. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: timeout. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'ответ HTTP или формат': ('Normal connection: unexpected HTTP response or format. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: unexpected HTTP response or format. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'прокси отклонил CONNECT': ('Normal connection: proxy rejected CONNECT. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: proxy rejected CONNECT. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'сертификат TLS': ('Normal connection: TLS certificate failure. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: TLS certificate failure. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'соединение': ('Normal connection: connection failure. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: connection failure. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
    'другая сетевая ошибка': ('Normal connection: other network failure. Check the internet connection and system proxy, or select automatic fallback.', 'Specified proxy: other network failure. Check its HTTP port and whether it is running. Manual mode does not select other addresses.'),
}

def _probe_failure(reason: str, *, manual: bool = False) -> OwnedText:
    templates = _PROBE_MESSAGES.get(reason)
    if templates:
        return OwnedText(templates[int(manual)])
    # Unknown external diagnostics retain their raw text, even on catalog collisions.
    return OwnedText("Specified proxy: {0}. Check its HTTP port and whether it is running. Manual mode does not select other addresses." if manual else "Normal connection: {0}. Check the internet connection and system proxy, or select automatic fallback.", reason)


def _discovery_failure(initial: list[str]) -> OwnedText:
    report = discovery_report()
    sources = report.get("sources", {})
    counts = dict(report.get("reasons", {}))
    for reason in initial:
        counts[reason] = counts.get(reason, 0) + 1
    known = ["успех", *_PROBE_MESSAGES]
    unknown = "; ".join(f"{key}: {value}" for key, value in counts.items() if key not in _PROBE_MESSAGES)
    return OwnedText('No working route was found. Discovery sources available: {0}/{1}; addresses checked: {2}/{3}. Probe outcomes including initial routes: success {4}; proxy authentication {5}; Google region restriction {6}; timeout {7}; HTTP response/format {8}; CONNECT rejection {9}; TLS certificate {10}; connection failure {11}; other network failure {12}. Other diagnostics: {13}. Specify your own HTTP/HTTPS proxy or use a working system connection/VPN.', sources.get("available", 0), sources.get("total", 0), report.get("checked", 0), report.get("candidates", 0), *(counts.get(key, 0) for key in known), unknown)


def check_connection(mode: str, proxy: str = "") -> dict[str, Any]:
    options = network_options(mode, proxy)
    notes = []
    if mode in {"system", "auto"}:
        direct = probe_result()
        if direct == "успех":
            return {"ok": True, "routes": [], "message": OwnedText("The normal/system connection to Google is available. No proxy is required. A keyless check confirms connectivity, but not quota or model access.")}
        notes.append(direct)
        if mode == "system":
            return {"ok": False, "routes": [], "message": _probe_failure(direct)}
    if options["proxy"]:
        result = probe_result(options["proxy"])
        if result == "успех":
            save_cached_proxy(options["proxy"], source="manual")
            return {"ok": True, "routes": [options["proxy"]], "message": OwnedText("The specified proxy establishes a verified TLS connection to Google. To check the key and model, select Check Gemini response.")}
        notes.append(result)
        if mode == "proxy":
            return {"ok": False, "routes": [], "message": _probe_failure(result, manual=True)}
    for route in system_proxies():
        if probe_result(route) == "успех":
            save_cached_proxy(route, source="manual")
            return {"ok": True, "routes": [route], "message": OwnedText("The system proxy connects to Google. Use Check Gemini response to verify model access.")}
    routes = recheck_cached_proxies()
    if not routes:
        # An explicit user check starts a fresh attempt after a negative search.
        routes = discover_proxies(force=True)
        for route in reversed(routes):
            save_cached_proxy(route)
    if routes:
        return {"ok": True, "routes": routes, "message": OwnedText("Verified HTTPS routes: {0}. Automatic mode uses saved routes and the normal connection. A network check does not confirm model access.", len(routes))}
    return {"ok": False, "routes": [], "message": _discovery_failure(notes)}


def check_model(mode: str, proxy: str = "") -> dict[str, Any]:
    """Make one bounded text request without modifying saved settings."""
    from google.genai import types
    from local_mode.config import Settings
    from local_mode.gemini import create_client, safe_error
    from local_mode.routing import ModelRouter
    options = network_options(mode, proxy)
    settings = Settings.from_env()
    router = ModelRouter(settings.model, settings.auto_fallback)
    try:
        with create_client(network=options) as client:
            response = router.generate(client, contents="Ответь одним словом: Готово", config=types.GenerateContentConfig(
                max_output_tokens=1024, http_options=types.HttpOptions(timeout=min(settings.timeout_ms, 15000),
                retry_options=types.HttpRetryOptions(attempts=1))))
            text = (response.text or "").strip()
            return {"ok": bool(text), "routes": [], "message": OwnedText("Model {0} responds: {1}", router.active_model, text[:250])
                    if text else OwnedText("Google accepted the request, but the model returned no text. Check the selected model.")}
    except Exception as error:
        import httpx
        from local_mode.network.errors import NetworkRouteError
        detail = safe_error(error)
        return {"ok": False, "routes": [], "message": detail if isinstance(detail, OwnedText) else OwnedText("Gemini check: {0}", detail),
                "error_kind": "network" if isinstance(error, (NetworkRouteError, httpx.TransportError)) else "api"}
