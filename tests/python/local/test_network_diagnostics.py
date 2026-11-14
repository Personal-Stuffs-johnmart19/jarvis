"""Регрессии: проверяется выбранный маршрут, сеть отделена от модели и квоты."""
from unittest.mock import Mock, patch
import httpx
import pytest
from local_mode.network import diagnostics, public_proxies
from local_mode.network.network_transport import RoutingTransport, NetworkRouteError


def test_auto_checks_normal_connection_before_searching_public_proxies():
    with patch.object(diagnostics, "probe_result", return_value="успех") as probe, patch.object(diagnostics, "discover_proxies") as search, patch.object(diagnostics, "recheck_cached_proxies") as cache:
        result = diagnostics.check_connection("auto")
    assert result["ok"] and result["routes"] == []
    assert "No proxy is required" in result["message"]
    probe.assert_called_once_with()
    search.assert_not_called()
    cache.assert_not_called()


def test_manual_check_uses_exact_unsaved_address_and_does_not_search():
    address = "http://127.0.0.1:7890"
    with patch.object(diagnostics, "probe_result", return_value="успех") as probe, patch.object(diagnostics, "save_cached_proxy") as save, patch.object(diagnostics, "discover_proxies") as search:
        result = diagnostics.check_connection("proxy", address)
    probe.assert_called_once_with(address)
    search.assert_not_called()
    save.assert_called_once_with(address, source="manual")
    assert result["ok"] and result["routes"] == [address]


def test_failed_manual_check_never_substitutes_another_route():
    with patch.object(diagnostics, "probe_result", return_value="прокси требует авторизацию"), patch.object(diagnostics, "discover_proxies") as search:
        result = diagnostics.check_connection("proxy", "http://127.0.0.1:7890")
    assert not result["ok"] and "authentication" in result["message"]
    search.assert_not_called()


def test_system_check_does_not_search_proxies():
    with patch.object(diagnostics, "probe_result", return_value="таймаут"), patch.object(diagnostics, "discover_proxies") as search:
        result = diagnostics.check_connection("system")
    assert not result["ok"] and "timeout" in result["message"]
    search.assert_not_called()


def test_explicit_check_forces_new_search_after_negative_cache():
    with patch.object(public_proxies, "_LAST_DISCOVERY", None), patch.object(public_proxies, "_discover_proxies", side_effect=[[], ["http://8.8.8.8:80"]]) as search:
        assert public_proxies.discover_proxies() == []
        assert public_proxies.discover_proxies() == []
        assert public_proxies.discover_proxies(force=True) == ["http://8.8.8.8:80"]
    assert search.call_count == 2


def test_auto_diagnostic_includes_failures_and_refreshes_search():
    with patch.object(diagnostics, "probe_result", return_value="таймаут"), patch.object(diagnostics, "recheck_cached_proxies", return_value=[]), patch.object(diagnostics, "discover_proxies", return_value=[]) as search, patch.object(diagnostics, "discovery_report", return_value={"checked": 24, "candidates": 24, "reasons": {"прокси отклонил CONNECT": 24}}):
        result = diagnostics.check_connection("auto")
    search.assert_called_once_with(force=True)
    assert not result["ok"] and "CONNECT rejection 24" in result["message"] and "timeout" in result["message"]


@pytest.mark.parametrize("status,message,expected", [(403,"User location is not supported for the API use.","ограничение региона Google"), (403,"API key is missing", "успех"), (407,"auth", "прокси требует авторизацию")])
def test_probe_distinguishes_network_reachability_from_region_and_proxy_auth(status,message,expected):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status,json={"error":{"message":message}})))
    with patch("local_mode.network.public_proxies.httpx.Client", return_value=client):
        assert public_proxies.probe_result("http://8.8.8.8:80") == expected


@pytest.mark.parametrize("status", [200, 503])
def test_proxy_html_gateway_error_is_network_failure_not_model_fallback(status):
    route = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status,text="<html>proxy gateway failed</html>",headers={"content-type":"text/html"})))
    transport = RoutingTransport("proxy", "http://127.0.0.1:7890")
    with patch.object(transport, "_new_client", return_value=route):
        with httpx.Client(transport=transport) as client:
            with pytest.raises(NetworkRouteError):
                client.get("https://generativelanguage.googleapis.com/v1beta/models")


def test_real_google_quota_stays_an_api_error_without_proxy_rotation():
    route = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429,json={"error":{"code":429,"message":"quota"}})))
    transport = RoutingTransport("proxy", "http://127.0.0.1:7890")
    with patch.object(transport, "_new_client", return_value=route), patch.object(transport, "_advance") as rotate:
        with httpx.Client(transport=transport) as client:
            assert client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code == 429
        rotate.assert_not_called()


def test_model_check_uses_unsaved_options_and_never_saves_them():
    model_response = Mock(text="Готово")
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    with patch("local_mode.gemini.create_client", return_value=client) as create, patch("local_mode.routing.ModelRouter.generate", return_value=model_response), patch("local_mode.network.network_preferences.save_network_preferences") as save:
        result = diagnostics.check_model("proxy", "http://127.0.0.1:7890")
    create.assert_called_once_with(network={"mode":"proxy","proxy":"http://127.0.0.1:7890"})
    save.assert_not_called()
    assert result["ok"] and "Готово" in result["message"]




def test_stale_address_does_not_block_system_connection():
    with patch.object(diagnostics, "probe_result", return_value="успех") as probe:
        result = diagnostics.check_connection("system", "старый неверный адрес")
    assert result["ok"]
    probe.assert_called_once_with()


def test_connect_auth_challenge_is_reported_as_auth_not_an_opaque_gateway_error():
    with patch("local_mode.network.public_proxies.httpx.Client", side_effect=httpx.ProxyError("407 Proxy Authentication Required")):
        assert public_proxies.probe_result("http://8.8.8.8:80") == "прокси требует авторизацию"


def test_network_messages_retain_ownership_across_settings_rpc_and_locales():
    import json
    from local_mode.messages import OwnedText, message
    from local_mode.results import present_result

    with patch.object(diagnostics, "probe_result", return_value="успех"):
        result = diagnostics.check_connection("auto")
    assert isinstance(result["message"], OwnedText)
    payload = json.loads(json.dumps(present_result(result)))
    owned = payload["message"]
    assert owned["source"] == "jarvis"
    assert "No proxy is required" in message(owned["message"], *owned["values"], locale="en")
    assert "Прокси не требуется" in message(owned["message"], *owned["values"], locale="ru")


def test_model_failure_keeps_raw_provider_diagnostic_as_a_value():
    from local_mode.messages import OwnedText, message
    detail = "Microphone selected. {0} C:/provider/raw"
    with patch("local_mode.gemini.create_client", side_effect=RuntimeError(detail)):
        result = diagnostics.check_model("system")
    owned = result["message"]
    assert not result["ok"] and result["error_kind"] == "api"
    assert isinstance(owned, OwnedText)
    assert owned.values == (detail,)
    assert message(owned.template, *owned.values, locale="en") == "Gemini check: " + detail
    assert message(owned.template, *owned.values, locale="ru") == "Проверка Gemini: " + detail


def test_empty_model_response_is_owned_and_failed():
    from local_mode.messages import OwnedText
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    with patch("local_mode.gemini.create_client", return_value=client), patch("local_mode.routing.ModelRouter.generate", return_value=Mock(text="")):
        result = diagnostics.check_model("system")
    assert not result["ok"]
    assert isinstance(result["message"], OwnedText)
    assert "returned no text" in result["message"]


@pytest.mark.parametrize("reason", list(diagnostics._PROBE_MESSAGES))
@pytest.mark.parametrize("manual", [False, True])
def test_known_probe_categories_have_complete_bilingual_owned_templates(reason, manual):
    from local_mode.messages import message
    owned = diagnostics._probe_failure(reason, manual=manual)
    assert not owned.values
    english = message(owned.template, locale="en")
    russian = message(owned.template, locale="ru")
    assert not any("А" <= character <= "я" or character in "Ёё" for character in english)
    assert english != russian


def test_discovery_report_is_detached_and_owned_summary_has_only_primitive_values():
    from local_mode.messages import message
    report = {"sources": {"available": 2, "total": 3}, "checked": 4, "candidates": 5,
              "reasons": {"сертификат TLS": 4, "Microphone selected.": 1}}
    with patch.object(public_proxies, "_LAST_REPORT", report):
        detached = public_proxies.discovery_report()
        detached["sources"]["available"] = 99
        detached["reasons"]["сертификат TLS"] = 99
        owned = diagnostics._discovery_failure(["таймаут"])
    assert report["sources"]["available"] == 2
    assert report["reasons"]["сертификат TLS"] == 4
    assert len(owned.values) <= 16
    assert all(isinstance(value, (str, int)) for value in owned.values)
    english = message(owned.template, *owned.values, locale="en")
    russian = message(owned.template, *owned.values, locale="ru")
    assert "timeout 1" in english and "TLS certificate 4" in english
    assert "таймаут 1" in russian and "сертификат TLS 4" in russian
    assert "Microphone selected.: 1" in english and "Microphone selected.: 1" in russian
    assert not any("А" <= character <= "я" or character in "Ёё" for character in english)


def test_model_owned_connection_failure_retains_its_original_provenance():
    from local_mode.messages import OwnedText, message
    with patch("local_mode.gemini.create_client", side_effect=httpx.ConnectError("raw provider detail")):
        result = diagnostics.check_model("system")
    owned = result["message"]
    assert isinstance(owned, OwnedText)
    assert all(not isinstance(value, OwnedText) for value in owned.values)
    assert owned.template == "Gemini connection failed. Check Network & Proxy settings."
    assert message(owned.template, *owned.values, locale="ru") != str(owned)
