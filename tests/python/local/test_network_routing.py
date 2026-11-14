"""Без сетевых запросов проверяем региональный резерв и границы доверия прокси."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from local_mode import gemini as gemini_client
from google.genai.errors import APIError
from local_mode.network.network_preferences import read_network_preferences, save_network_preferences, validate_proxy
from local_mode.network.network_transport import NetworkRouteError, RoutingTransport, is_regional_error
from local_mode.network.public_proxies import SOURCES, discover_proxies, download_candidates, parse_candidates, probe_proxy, probe_result
from local_mode.network.proxy_cache import read_cached_proxy, save_cached_proxy

ROOT = Path(__file__).resolve().parent


class NetworkRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        route_hint = patch("local_mode.network.network_transport._AUTO_ROUTE", None)
        route_hint.start()
        self.addCleanup(route_hint.stop)
        discovery = patch("local_mode.network.public_proxies._LAST_DISCOVERY", None)
        discovery.start()
        self.addCleanup(discovery.stop)
        directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(directory.cleanup)
        cache = patch("local_mode.network.proxy_cache.CACHE_PATH", Path(directory.name) / "proxy_cache.json")
        cache.start()
        self.addCleanup(cache.stop)
        pause = patch("local_mode.network.network_transport.time.sleep")
        pause.start()
        self.addCleanup(pause.stop)
        for target, result in (("local_mode.network.network_transport.probe_proxy", False), ("local_mode.network.proxy_cache.probe_proxy", True)):
            probe = patch(target, return_value=result)
            probe.start()
            self.addCleanup(probe.stop)

    def test_manual_and_menu_discovery_share_storage_without_retesting(self) -> None:
        from local_mode.network.proxy_cache import available_proxies
        with patch("local_mode.network.public_proxies.discover_proxies", return_value=["http://8.8.8.8:80"]) as discover:
            self.assertEqual(available_proxies(), ["http://8.8.8.8:80"])
            self.assertEqual(available_proxies(), ["http://8.8.8.8:80"])
            discover.assert_called_once()
        from local_mode.network import proxy_cache
        save_network_preferences("proxy", "http://127.0.0.1:7890", proxy_cache.CACHE_PATH.parent / "network.json")
        self.assertEqual(read_cached_proxy(), "http://127.0.0.1:7890")
        with patch("local_mode.network.public_proxies.discover_proxies") as discover:
            self.assertEqual(len(available_proxies()), 2)
            discover.assert_not_called()

    def test_brief_disconnect_keeps_route_without_search(self) -> None:
        save_cached_proxy("http://8.8.8.8:80")
        calls = 0
        def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadError("disconnected", request=request)
            return httpx.Response(200, json={})
        route = httpx.Client(transport=httpx.MockTransport(handler))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", return_value=route), patch("local_mode.network.network_transport.discover_proxies") as discover, \
                patch("local_mode.network.network_transport.probe_proxy", return_value=True):
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
            discover.assert_not_called()
        route.close()
        self.assertEqual(calls, 2)
        self.assertEqual(read_cached_proxy(), "http://8.8.8.8:80")

    def test_recheck_prunes_dead_reserves_and_preserves_multiple_working_proxies(self) -> None:
        from local_mode.network.proxy_cache import read_cached_proxies, recheck_cached_proxies
        for route in ("http://8.8.8.8:80", "http://9.9.9.9:80", "http://1.1.1.1:80"):
            save_cached_proxy(route)
        with patch("local_mode.network.proxy_cache.probe_proxy", side_effect=lambda route: "9.9.9.9" not in route) as probe:
            working = recheck_cached_proxies({"http://1.1.1.1:80": True})
            self.assertEqual(probe.call_count, 2)
        self.assertEqual(working, ["http://1.1.1.1:80", "http://8.8.8.8:80"])
        self.assertEqual([item["proxy"] for item in read_cached_proxies()], working)

    def test_discovery_saves_several_verified_routes_for_future_connections(self) -> None:
        from local_mode.network.proxy_cache import available_proxies, read_cached_proxies
        routes = ["http://8.8.8.8:80", "http://9.9.9.9:80", "http://1.1.1.1:80"]
        with patch("local_mode.network.public_proxies.discover_proxies", return_value=routes) as discover:
            self.assertEqual(available_proxies(), routes)
            self.assertEqual(available_proxies(), routes)
            discover.assert_called_once()
        self.assertEqual([item["proxy"] for item in read_cached_proxies()], routes)

    def test_google_reconnection_tries_multiple_verified_reserves_without_new_search(self) -> None:
        from local_mode.network.proxy_cache import read_cached_proxies
        routes = ["http://8.8.8.8:80", "http://9.9.9.9:80", "http://1.1.1.1:80"]
        for route in reversed(routes):
            save_cached_proxy(route)
        def fail(request):
            raise httpx.ConnectError("offline", request=request)
        clients = [httpx.Client(transport=httpx.MockTransport(fail)) for _ in range(2)]
        clients.append(httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", side_effect=clients) as factory, \
                patch("local_mode.network.network_transport.probe_proxy", return_value=True), \
                patch("local_mode.network.network_transport.discover_proxies") as discover:
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
            discover.assert_not_called()
        self.assertEqual([call.args[0] for call in factory.call_args_list], [None, *routes[:2]])
        # HTTPS-туннели живы: отказ запроса Google не удаляет их из хранилища.
        self.assertEqual(len(read_cached_proxies()), 3)
        self.assertEqual(read_cached_proxy(), routes[1])
        for client in clients:
            client.close()

    def test_working_proxy_is_reused_by_new_transport_without_discovery(self) -> None:
        save_cached_proxy("http://8.8.8.8:80")
        for index in range(2):
            transport = RoutingTransport("auto")
            healthy = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
            blocked = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400,
                json={"error": {"message": "User location is not supported for the API use."}})))
            with patch.object(transport, "_new_client", side_effect=[blocked, healthy] if index == 0 else [healthy]) as factory, \
                    patch("local_mode.network.network_transport.discover_proxies") as discover:
                with httpx.Client(transport=transport) as client:
                    self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
                self.assertEqual([call.args[0] for call in factory.call_args_list],
                                 [None, "http://8.8.8.8:80"] if index == 0 else ["http://8.8.8.8:80"])
                discover.assert_not_called()
            blocked.close()
            healthy.close()

    def test_stored_reserve_is_used_before_search_when_response_body_breaks(self) -> None:
        save_cached_proxy("http://9.9.9.9:80")
        save_cached_proxy("http://8.8.8.8:80")
        class BrokenBody(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"partial":'
                raise httpx.ReadError("response interrupted")
        failed = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=BrokenBody())))
        healthy = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", side_effect=[failed, healthy]), \
                patch("local_mode.network.network_transport.discover_proxies") as discover:
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").json(), {})
            discover.assert_not_called()
        failed.close()
        healthy.close()
        self.assertEqual(read_cached_proxy(), "http://8.8.8.8:80")

    def test_failed_cached_proxy_is_replaced_after_successful_api_response(self) -> None:
        save_cached_proxy("http://8.8.8.8:80")
        def fail(request):
            raise httpx.ConnectError("offline", request=request)
        failed = httpx.Client(transport=httpx.MockTransport(fail))
        direct = httpx.Client(transport=httpx.MockTransport(fail))
        healthy = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", side_effect=[failed, direct, healthy]), \
                patch("local_mode.network.network_transport.discover_proxies", return_value=["http://9.9.9.9:80"]) as discover:
            with httpx.Client(transport=transport) as client:
                client.get("https://generativelanguage.googleapis.com/v1beta/models")
            discover.assert_called_once()
        self.assertEqual(read_cached_proxy(), "http://9.9.9.9:80")
        failed.close()
        direct.close()
        healthy.close()

    def test_quota_or_server_error_does_not_discard_saved_proxy(self) -> None:
        save_cached_proxy("http://8.8.8.8:80")
        for status in (403, 429, 503):
            transport = RoutingTransport("auto")
            healthy = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={})))
            with patch.object(transport, "_new_client", return_value=healthy), \
                    patch("local_mode.network.network_transport.discover_proxies") as discover:
                with httpx.Client(transport=transport) as client:
                    self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, status)
                discover.assert_not_called()
            healthy.close()
            self.assertEqual(read_cached_proxy(), "http://8.8.8.8:80")

    def test_corrupt_or_private_cached_proxy_is_ignored(self) -> None:
        from local_mode.network import proxy_cache
        for value in ('broken json', '{"proxy":"http://127.0.0.1:80"}'):
            proxy_cache.CACHE_PATH.write_text(value, encoding="utf-8")
            self.assertEqual(read_cached_proxy(), "")

    @patch("local_mode.network.public_proxies.download_candidates", return_value=[f"http://8.8.8.{i}:80" for i in range(1, 65)])
    @patch("local_mode.network.public_proxies.probe_result")
    def test_discovery_continues_after_first_twelve_failures(self, probe, candidates) -> None:
        probe.side_effect = lambda url: "успех" if int(url.split(".")[-1].split(":")[0]) > 16 else "таймаут"
        self.assertEqual(len(discover_proxies()), 8)
        self.assertEqual(probe.call_count, 24)

    def test_small_source_is_not_drowned_out_by_large_source(self) -> None:
        def source(request):
            if str(request.url) == SOURCES[0]:
                return httpx.Response(200, text="8.8.8.8:80")
            if str(request.url) == SOURCES[1]:
                return httpx.Response(200, text="\n".join(f"9.9.9.{i}:80" for i in range(1, 100)))
            return httpx.Response(503)
        client = httpx.Client(transport=httpx.MockTransport(source))
        with patch("local_mode.network.public_proxies.httpx.Client", return_value=client), patch("local_mode.network.public_proxies.random.shuffle"):
            candidates = download_candidates()
        self.assertEqual(candidates[0], "http://8.8.8.8:80")
        self.assertEqual(len(candidates), 100)
        self.assertEqual(len(set(candidates)), 100)

    @patch("local_mode.network.public_proxies.httpx.Client", side_effect=httpx.ConnectTimeout("private message"))
    def test_probe_failure_category_does_not_leak_exception(self, client) -> None:
        self.assertEqual(probe_result("http://8.8.8.8:80"), "таймаут")

    def test_proxy_input_and_public_list_reject_unsafe_addresses(self) -> None:
        for value in ("http://user:password@host:80", "http://host:80/path", "http://host:80?key=secret",
                      "ftp://host:80", "http://host:0", "http://host:99999", "http://host:80\n"):
            with self.assertRaises(ValueError):
                validate_proxy(value)
        self.assertEqual(validate_proxy("http://127.0.0.1:7890"), "http://127.0.0.1:7890")
        candidates = parse_candidates("8.8.8.8:80\n127.0.0.1:80\n169.254.169.254:80\n10.0.0.1:80\n"
                                      "localhost:80\n224.0.0.1:80\nhttp://8.8.8.8:80\n100.64.0.1:80")
        self.assertEqual(candidates, ["http://8.8.8.8:80"])

    def test_network_settings_are_persistent_and_separate_from_api_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "network_preferences.json"
            self.assertEqual(read_network_preferences(path)["mode"], "auto")
            save_network_preferences("auto", path=path)
            self.assertEqual(read_network_preferences(path), {"mode": "auto", "proxy": ""})
            save_network_preferences("proxy", "http://127.0.0.1:7890", path)
            self.assertEqual(read_network_preferences(path)["proxy"], "http://127.0.0.1:7890")
            with self.assertRaises(ValueError):
                save_network_preferences("proxy", path=path)

    def test_regional_detection_does_not_confuse_wrong_key_or_quota(self) -> None:
        self.assertTrue(is_regional_error(400, "User location is not supported for the API use."))
        self.assertFalse(is_regional_error(403, "API key not valid"))
        self.assertFalse(is_regional_error(429, "region quota exhausted"))
        self.assertIn("current region", gemini_client.safe_error(
            APIError(400, {"error": {"message": "User location is not supported for the API use."}})))

    def test_regional_error_switches_route_and_preserves_request_body(self) -> None:
        sent = []
        direct = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            400, json={"error": {"message": "User location is not supported for the API use."}})))

        def succeed(request):
            sent.append((request.content, request.headers.get("x-goog-api-key")))
            return httpx.Response(200, json={"ok": True})

        proxy = httpx.Client(transport=httpx.MockTransport(succeed))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", side_effect=[direct, proxy]) as factory, \
                patch("local_mode.network.network_transport.discover_proxies", return_value=["http://8.8.8.8:80"]) as discover:
            with httpx.Client(transport=transport) as client:
                for _ in range(2):
                    self.assertEqual(client.post("https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",
                                                 content=b'{"contents":"test"}', headers={"x-goog-api-key": "test-key"}).status_code, 200)
            self.assertEqual(factory.call_count, 2)
            discover.assert_called_once_with()
        direct.close()
        proxy.close()
        self.assertEqual(sent, [(b'{"contents":"test"}', "test-key")] * 2)

    def test_bad_key_does_not_trigger_proxy_search(self) -> None:
        direct = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            403, json={"error": {"message": "API key not valid"}})))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", return_value=direct), \
                patch("local_mode.network.network_transport.discover_proxies") as discover:
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 403)
            discover.assert_not_called()
        direct.close()

    def test_html_access_block_is_distinct_from_json_authentication_error(self) -> None:
        direct = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            403, text="Access denied", headers={"content-type": "text/html"})))
        proxy = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", side_effect=[direct, proxy]), \
                patch("local_mode.network.network_transport.discover_proxies", return_value=["http://8.8.8.8:80"]):
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
        direct.close()
        proxy.close()

    def test_exhausted_routes_fail_without_searching_again_or_replaying_tools(self) -> None:
        def fail(request):
            raise httpx.ConnectError("private diagnostic data", request=request)
        direct = httpx.Client(transport=httpx.MockTransport(fail))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", return_value=direct), \
                patch("local_mode.network.network_transport.discover_proxies", return_value=[]) as discover:
            with httpx.Client(transport=transport) as client:
                for _ in range(2):
                    with self.assertRaises(NetworkRouteError) as error:
                        client.get("https://generativelanguage.googleapis.com/v1beta/models")
                    self.assertNotIn("private diagnostic data", str(error.exception))
            discover.assert_called_once()
        direct.close()

    def test_non_google_endpoint_is_rejected_before_network(self) -> None:
        with httpx.Client(transport=RoutingTransport("auto")) as client:
            with self.assertRaises(NetworkRouteError):
                client.get("https://example.com/")

    def test_failed_route_can_recover_after_cooldown(self) -> None:
        def fail(request):
            raise httpx.ConnectError("offline", request=request)
        failed = httpx.Client(transport=httpx.MockTransport(fail))
        healthy = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        transport = RoutingTransport("auto")
        with patch.object(transport, "_new_client", side_effect=[failed, healthy]), \
                patch("local_mode.network.network_transport.discover_proxies", return_value=[]), \
                patch("local_mode.network.network_transport.time.monotonic", return_value=1000) as clock:
            with httpx.Client(transport=transport) as client:
                with self.assertRaises(NetworkRouteError):
                    client.get("https://generativelanguage.googleapis.com/v1beta/models")
                clock.return_value = 1301
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
        failed.close()
        healthy.close()

    @patch("local_mode.network.public_proxies.httpx.Client")
    def test_public_probe_has_tls_and_never_uses_api_key(self, client) -> None:
        response = client.return_value.__enter__.return_value.stream.return_value.__enter__.return_value
        response.status_code = 403
        response.headers = {"content-type": "application/json"}
        response.iter_bytes.return_value = [b'{"error":{"code":403}}']
        self.assertTrue(probe_proxy("http://8.8.8.8:80"))
        self.assertTrue(client.call_args.kwargs["verify"])
        self.assertFalse(client.call_args.kwargs["trust_env"])
        request = client.return_value.__enter__.return_value.stream.call_args
        self.assertNotIn("headers", request.kwargs)
        self.assertNotIn("key=", request.args[1])

    def test_remote_http_disconnect_is_retried_on_current_proxy(self) -> None:
        save_cached_proxy("http://8.8.8.8:80")
        calls = 0
        def respond(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.RemoteProtocolError("peer closed response")
            return httpx.Response(200, json={"ok": True})
        route = httpx.Client(transport=httpx.MockTransport(respond))
        transport = RoutingTransport("auto")
        with patch("local_mode.network.network_transport._AUTO_ROUTE", "http://8.8.8.8:80"), patch.object(transport, "_new_client", return_value=route), \
                patch("local_mode.network.network_transport.probe_proxy", return_value=True) as probe, \
                patch("local_mode.network.network_transport.discover_proxies") as discover:
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
            probe.assert_called_once()
            discover.assert_not_called()
        route.close()
        self.assertEqual(calls, 2)


    def test_dead_cached_proxy_falls_back_to_direct_before_new_search(self) -> None:
        save_cached_proxy("http://8.8.8.8:80")
        def fail(request):
            raise httpx.ConnectError("proxy offline", request=request)
        dead = httpx.Client(transport=httpx.MockTransport(fail))
        direct = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
        transport = RoutingTransport("auto")
        with patch("local_mode.network.network_transport._AUTO_ROUTE", "http://8.8.8.8:80"), patch.object(transport, "_new_client", side_effect=[dead, direct]) as factory, \
                patch("local_mode.network.network_transport.discover_proxies") as discover:
            with httpx.Client(transport=transport) as client:
                self.assertEqual(client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code, 200)
            discover.assert_not_called()
            factory.assert_called_with()
        self.assertEqual(read_cached_proxy(), "")
        dead.close()
        direct.close()


    def test_probe_rejects_truncated_or_invalid_json_response(self) -> None:
        class Broken(httpx.SyncByteStream):
            def __iter__(self):
                yield b'{"error":'
                raise httpx.RemoteProtocolError('body disconnected')
        for stream in (Broken(), httpx.ByteStream(b'not json'), httpx.ByteStream(b'{}')):
            client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
                403, headers={'content-type': 'application/json'}, stream=stream)))
            with patch('local_mode.network.public_proxies.httpx.Client', return_value=client):
                self.assertNotEqual(probe_result('http://8.8.8.8:80'), 'успех')



if __name__ == "__main__":
    unittest.main()
