"""Расширенный резерв: все сетевые сценарии изолированы от ключей и рабочего кэша."""
from pathlib import Path
import tempfile
from threading import Event
from unittest.mock import Mock, patch
import httpx
from local_mode.network import public_proxies as public
from local_mode.network.network_transport import RoutingTransport, reset_auto_route
from local_mode.network.system_proxies import system_proxies
from local_mode.network.startup import start_network_check


def test_negative_discovery_cache_expires_after_fifteen_seconds():
    with patch.object(public, "_LAST_DISCOVERY", None), patch.object(public.time, "monotonic", return_value=1000) as clock, patch.object(public, "_discover_proxies", side_effect=[[], ["http://8.8.8.8:80"]]) as discover:
        assert public.discover_proxies() == []
        assert public.discover_proxies() == []
        assert discover.call_count == 1
        clock.return_value = 1016
        assert public.discover_proxies() == ["http://8.8.8.8:80"]
        assert discover.call_count == 2


def test_regional_failure_of_eight_tls_routes_triggers_a_new_search():
    old = [f"http://8.8.8.{i}:80" for i in range(1, 9)]
    new = "http://9.9.9.9:80"
    visited = []
    def factory(proxy=None):
        visited.append(proxy)
        def answer(request):
            return httpx.Response(200, json={"ok": True}) if proxy == new else httpx.Response(400, json={"error": {"message": "User location is not supported for the API use."}})
        return httpx.Client(transport=httpx.MockTransport(answer))
    with tempfile.TemporaryDirectory() as folder, patch("local_mode.network.proxy_cache.CACHE_PATH", Path(folder)/"cache.json"), patch("local_mode.network.network_transport.system_proxies", return_value=[]), patch("local_mode.network.network_transport.discover_proxies", side_effect=[old, [new]]) as discover:
        reset_auto_route()
        route = RoutingTransport("auto")
        with patch.object(route, "_new_client", side_effect=factory), httpx.Client(transport=route) as client:
            assert client.post("https://generativelanguage.googleapis.com/v1beta/models/test:generateContent", content=b"test").status_code == 200
        assert visited == [None, *old, new]
        assert discover.call_count == 2
        assert discover.call_args.kwargs == {"force": True, "exclude": set(old)}
    reset_auto_route()


def test_already_configured_system_proxy_precedes_public_discovery():
    system = "http://127.0.0.1:7890"
    with patch("local_mode.network.network_transport.system_proxies", return_value=[system]), patch("local_mode.network.network_transport.read_cached_proxies", return_value=[]), patch("local_mode.network.network_transport.save_cached_proxy") as save, patch("local_mode.network.network_transport.discover_proxies") as discover:
        reset_auto_route()
        route = RoutingTransport("auto")
        def factory(proxy=None):
            return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}) if proxy else httpx.Response(451)))
        with patch.object(route, "_new_client", side_effect=factory), httpx.Client(transport=route) as client:
            assert client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code == 200
        discover.assert_not_called()
        save.assert_called_with(system, source="manual")
    reset_auto_route()


def test_system_proxy_does_not_execute_pac_or_accept_embedded_passwords():
    with patch("local_mode.network.system_proxies.getproxies", return_value={"https": "127.0.0.1:7890", "http": "http://user:secret@host:80", "all": "socks5://127.0.0.1:1080", "pac": "https://host/script.js"}):
        assert system_proxies() == ["http://127.0.0.1:7890"]


def test_auto_direct_connection_does_not_inherit_broken_env_proxy():
    route = RoutingTransport("auto")
    with patch("local_mode.network.network_transport.httpx.Client") as client:
        route._new_client()
        assert client.call_args.kwargs["trust_env"] is False
        assert client.call_args.kwargs["verify"] is True


def test_sources_outside_github_remain_available_when_github_is_blocked():
    def answer(request):
        if request.url.host in {"api.proxyscrape.com", "api.proxyscraper.com"}:
            assert "x-goog-api-key" not in request.headers
            return httpx.Response(200, text="8.8.8.8:80")
        return httpx.Response(503)
    client = httpx.Client(transport=httpx.MockTransport(answer))
    with patch.object(public.httpx, "Client", return_value=client):
        assert public.download_candidates() == ["http://8.8.8.8:80"]
    assert public._LAST_SOURCES["available"] == 2


def test_startup_does_not_schedule_model_recovery_or_periodic_retries():
    stop = Mock(spec=Event)
    stop.is_set.return_value = False
    connected = Mock()
    with patch("local_mode.network.diagnostics.check_model",side_effect=RuntimeError("network failure")) as check:
        assert start_network_check(stop,Mock(),connected) is None
        assert start_network_check(stop,Mock(),connected,initialize=False) is None
    check.assert_not_called()
    stop.wait.assert_not_called()
    connected.assert_not_called()



def test_startup_does_not_probe_model_quota_or_keys():
    stop = Mock(spec=Event)
    stop.is_set.return_value = False
    with patch("local_mode.network.diagnostics.check_model",return_value={"ok":False,"message":"quota","error_kind":"api"}) as check:
        assert start_network_check(stop,Mock(),Mock()) is None
    check.assert_not_called()
    stop.wait.assert_not_called()



def test_recovery_does_not_override_manual_mode_or_reset_session_generation():
    with patch("local_mode.network.network_preferences.read_network_preferences",return_value={"mode":"proxy","proxy":"http://127.0.0.1:7890"}) as read, patch("local_mode.network.network_transport.reset_auto_route") as reset, patch("local_mode.network.diagnostics.check_model") as check:
        assert start_network_check(Event(),Mock(),Mock(),initialize=False) is None
    read.assert_not_called()
    reset.assert_not_called()
    check.assert_not_called()
