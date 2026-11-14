"""Cancel routing at owned boundaries; all sends/probes/discovery are mocked."""
import threading
from unittest.mock import Mock

import httpx
import pytest

from local_mode import gemini
from local_mode.network import network_transport as transport


def fixture_transport(monkeypatch, mode="auto", proxy=""):
    monkeypatch.setattr(transport, "system_proxies", Mock(return_value=[]))
    monkeypatch.setattr(transport, "probe_proxy", Mock(return_value=False))
    monkeypatch.setattr(transport, "discover_proxies", Mock(return_value=[]))
    monkeypatch.setattr(transport, "recheck_cached_proxies", Mock(return_value=[]))
    monkeypatch.setattr(transport, "read_cached_proxies", Mock(return_value=[]))
    stop = threading.Event()
    route = transport.RoutingTransport(mode, proxy, stop=stop)
    route.active = Mock()
    route.active_proxy = proxy
    route.direct_attempted = True
    return route, stop


@pytest.mark.parametrize("proxy", ["", "http://127.0.0.1:9999"])
def test_cancel_after_transport_error_never_retries_probes_or_discovers(monkeypatch, proxy):
    route, stop = fixture_transport(monkeypatch, proxy=proxy)
    physical = route.active
    def send(*args, **kwargs):
        stop.set()
        route.close()  # reproduces the handler close()/active=None race
        raise httpx.ReadError("fixture closed by cancellation")
    physical.send.side_effect = send
    with pytest.raises(RuntimeError, match="cancelled"):
        route.handle_request(httpx.Request("POST", "https://generativelanguage.googleapis.com/v1/fixture"))
    assert physical.send.call_count == 1
    assert route.active is None
    transport.probe_proxy.assert_not_called()
    transport.discover_proxies.assert_not_called()
    transport.recheck_cached_proxies.assert_not_called()


def test_cancel_during_discovery_never_opens_or_sends_next_route(monkeypatch):
    route, stop = fixture_transport(monkeypatch)
    new_client = Mock()
    monkeypatch.setattr(route, "_new_client", new_client)
    def discover():
        stop.set()
        return ["http://127.0.0.1:9999"]
    monkeypatch.setattr(transport, "discover_proxies", Mock(side_effect=discover))
    with pytest.raises(RuntimeError, match="cancelled"):
        route._advance()
    new_client.assert_not_called()
    route.active.send.assert_not_called()


def test_retry_pause_is_interruptible_and_never_resends(monkeypatch):
    route, stop = fixture_transport(monkeypatch)
    physical = route.active
    entered = threading.Event()
    physical.send.side_effect = httpx.ReadError("fixture transient error")
    original_wait = stop.wait
    def wait(timeout):
        entered.set()
        stop.set()
        return original_wait(timeout)
    monkeypatch.setattr(stop, "wait", wait)
    with pytest.raises(RuntimeError, match="cancelled"):
        route.handle_request(httpx.Request("POST", "https://generativelanguage.googleapis.com/v1/fixture"))
    assert entered.is_set()
    assert physical.send.call_count == 1
    transport.discover_proxies.assert_not_called()


def test_create_client_passes_owned_stop_to_routing_transport(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-unused-key")
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    monkeypatch.delenv("GOOGLE_GENAI_USE_ENTERPRISE", raising=False)
    monkeypatch.setattr(gemini, "has_key", Mock(return_value=True))
    factory = Mock()
    monkeypatch.setattr(gemini, "RoutingTransport", factory)
    monkeypatch.setattr(gemini.httpx, "Client", Mock())
    monkeypatch.setattr(gemini.genai, "Client", Mock())
    # This fixture tests wiring through mocked clients, not QA admission.
    # Install every network/client mock before removing the inherited guard.
    monkeypatch.delenv("JARVIS_DESKTOP_TEST", raising=False)
    stop = threading.Event()
    gemini.create_client(network={"mode": "auto", "proxy": ""}, retry_attempts=0, stop=stop)
    assert factory.call_args.kwargs["stop"] is stop
