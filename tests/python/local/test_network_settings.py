"""Интеграция маршрута с SDK, общим кэшем и GUI; без внешней сети."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode import gemini
from local_mode.network import proxy_cache, public_proxies
from local_mode.network.network_preferences import save_network_preferences, read_network_preferences
from local_mode.network.network_transport import NetworkRouteError, RoutingTransport
from local_mode.routing import ModelRouter
import httpx


class NetworkSettingsTests(unittest.TestCase):
    def setUp(self):
        route_hint = patch("local_mode.network.network_transport._AUTO_ROUTE", None)
        route_hint.start()
        self.addCleanup(route_hint.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, value in [("local_mode.network.network_preferences.NETWORK_PATH", self.root / "new" / "network.json"),
                              ("local_mode.network.proxy_cache.CACHE_PATH", self.root / "new" / "cache.json"),
                              ("local_mode.network.public_proxies._LAST_DISCOVERY", None)]:
            context = patch(target, value)
            context.start()
            self.addCleanup(context.stop)

    def test_concurrent_cache_updates_preserve_all_routes(self):
        routes = [f"http://8.8.8.{i}:80" for i in range(1, 21)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(proxy_cache.save_cached_proxy, routes))
        self.assertEqual({entry["proxy"] for entry in proxy_cache.read_cached_proxies()}, set(routes))

    def test_discovery_caches_empty_results_across_clients(self):
        with patch.object(public_proxies, "_discover_proxies", return_value=[]) as discover:
            self.assertEqual(public_proxies.discover_proxies(), [])
            self.assertEqual(public_proxies.discover_proxies(), [])
            discover.assert_called_once()

    def test_pruned_route_is_not_resurrected_from_discovery_cache(self):
        proxy = "http://8.8.8.8:80"
        with patch.object(public_proxies, "_discover_proxies", return_value=[proxy]) as discover:
            self.assertEqual(proxy_cache.available_proxies(), [proxy])
            proxy_cache.forget_cached_proxy(proxy)
            self.assertEqual(proxy_cache.available_proxies(), [])
            discover.assert_called_once()

    def test_apply_network_closes_old_client_without_resetting_dialogue(self):
        bot = LocalAgent.__new__(LocalAgent)
        bot.client = Mock()
        old_client = bot.client
        bot.history = ["existing"]
        result = bot.configure_network("proxy", "http://127.0.0.1:7890")
        self.assertEqual(result["mode"], "proxy")
        self.assertEqual(read_network_preferences(), result)
        old_client.close.assert_called_once()
        self.assertIsNone(bot.client)
        self.assertEqual(bot.history, ["existing"])

    def test_model_cannot_edit_network_route_files(self):
        from local_mode.tools import ToolRegistry
        from local_mode.config import ROOT
        for name in ["network_preferences.json", "proxy_cache.json"]:
            with self.assertRaises(ValueError):
                ToolRegistry().execute("file_controller", {"action": "write", "path": str(ROOT / "data/network" / name), "content": "{}"})

    def test_invalid_network_never_closes_existing_client(self):
        bot = LocalAgent.__new__(LocalAgent)
        bot.client = Mock()
        with self.assertRaises(ValueError):
            bot.configure_network("proxy", "http://user:password@host:80")
        bot.client.close.assert_not_called()

    @patch("local_mode.gemini.genai.Client")
    @patch("local_mode.gemini.has_key", return_value=True)
    def test_sdk_uses_custom_transport_only_when_configured(self, key, create):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic"}, clear=True):
            save_network_preferences("system")
            gemini.create_client()
            create.assert_called_once_with()
            create.reset_mock()
            save_network_preferences("proxy", "http://127.0.0.1:7890")
            gemini.create_client()
            http_client = create.call_args.kwargs["http_options"].httpx_client
            self.assertIsInstance(http_client._transport, RoutingTransport)
            http_client.close()

    def test_failed_route_never_downgrades_model(self):
        generator = Mock(side_effect=NetworkRouteError("safe"))
        router = ModelRouter("gemini-3.6-flash")
        with self.assertRaises(NetworkRouteError):
            router.generate(Mock(), contents="test", generator=generator)
        self.assertEqual(router.active_model, "gemini-3.6-flash")
        generator.assert_called_once()
        self.assertFalse(gemini.is_transient(NetworkRouteError("safe")))

    def test_protocol_disconnect_retries_same_route(self):
        calls = 0
        def handle(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.RemoteProtocolError("disconnected")
            return httpx.Response(200, json={})
        route = httpx.Client(transport=httpx.MockTransport(handle))
        with patch.object(RoutingTransport, "_new_client", return_value=route), patch("local_mode.network.network_transport.time.sleep"):
            with httpx.Client(transport=RoutingTransport("auto")) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
        self.assertEqual(calls, 2)
