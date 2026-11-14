"""Автовыбор при запуске: сеть моделируется без управления ПК и отправки ключей."""
from threading import Event
from unittest.mock import Mock,patch
import httpx
from local_mode.network import network_transport as transport_module
from local_mode.network.network_transport import RoutingTransport, reset_auto_route, current_auto_route
from local_mode.network.startup import start_network_check
from local_mode.network.network_preferences import prepare_startup_network


def test_startup_enables_auto_and_preserves_manual_address():
    with patch("local_mode.network.network_preferences.read_network_preferences",return_value={"mode":"proxy","proxy":"http://127.0.0.1:7890"}), patch("local_mode.network.network_preferences.save_network_preferences") as save:
        assert prepare_startup_network()=={"mode":"auto","proxy":"http://127.0.0.1:7890"}
    save.assert_called_once_with("auto","http://127.0.0.1:7890")


def test_startup_does_not_rewrite_existing_auto_preferences():
    with patch("local_mode.network.network_preferences.read_network_preferences",return_value={"mode":"auto","proxy":""}), patch("local_mode.network.network_preferences.save_network_preferences") as save:
        assert prepare_startup_network()["mode"]=="auto"
    save.assert_not_called()


def test_startup_notice_is_passive_and_does_not_confirm_connection():
    report,connected=Mock(),Mock()
    with patch("local_mode.network.network_preferences.prepare_startup_network") as prepare, patch("local_mode.network.diagnostics.check_model") as check:
        assert start_network_check(Event(),report,connected) is None
    prepare.assert_not_called()
    check.assert_not_called()
    connected.assert_not_called()
    assert "Network checks are manual" in report.call_args.args[0]



def test_cancelled_startup_does_not_change_settings_or_start_requests():
    stop=Event();stop.set()
    with patch("local_mode.network.network_preferences.prepare_startup_network") as prepare, patch("local_mode.network.diagnostics.check_model") as check:
        assert start_network_check(stop,Mock(),Mock()) is None
    prepare.assert_not_called();check.assert_not_called()



def test_startup_does_not_read_or_override_manual_setting():
    connected=Mock()
    with patch("local_mode.network.network_preferences.read_network_preferences",return_value={"mode":"system","proxy":""}) as read, patch("local_mode.network.diagnostics.check_model") as check:
        assert start_network_check(Event(),Mock(),connected) is None
    connected.assert_not_called()
    read.assert_not_called()
    check.assert_not_called()



def test_direct_region_block_switches_to_saved_proxy_and_shares_confirmed_route():
    reset_auto_route()
    route="http://8.8.8.8:80"
    blocked=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(400,json={"error":{"message":"User location is not supported for the API use."}})))
    healthy=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"ok":True})))
    transport=RoutingTransport("auto")
    try:
        with patch("local_mode.network.network_transport.read_cached_proxies",return_value=[{"proxy":route,"source":"public"}]), patch.object(transport,"_new_client",side_effect=[blocked,healthy]) as create, patch("local_mode.network.network_transport.save_cached_proxy"), patch("local_mode.network.network_transport.discover_proxies") as discover:
            with httpx.Client(transport=transport) as client:
                assert client.post("https://generativelanguage.googleapis.com/v1beta/models/test:generateContent",json={"contents":[]}).status_code==200
            assert [call.args[0] for call in create.call_args_list]==[None,route]
            assert current_auto_route()==route
            discover.assert_not_called()
        next_transport=RoutingTransport("auto")
        next_route=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})))
        with patch.object(next_transport,"_new_client",return_value=next_route) as create:
            with httpx.Client(transport=next_transport) as client:
                client.get("https://generativelanguage.googleapis.com/v1beta/models")
            create.assert_called_once_with(route)
    finally:
        reset_auto_route()
        blocked.close();healthy.close()


def test_new_startup_ignores_previous_confirmed_proxy_and_stale_clients():
    reset_auto_route()
    stale=RoutingTransport("auto")
    stale.active_proxy="http://8.8.8.8:80"
    with patch("local_mode.network.network_transport.save_cached_proxy"):
        stale._remember_route(httpx.Response(200))
        assert current_auto_route()==stale.active_proxy
        reset_auto_route()
        stale._remember_route(httpx.Response(200))
        assert current_auto_route() is None
    transport=RoutingTransport("auto")
    healthy=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={})))
    with patch.object(transport,"_new_client",return_value=healthy) as create:
        with httpx.Client(transport=transport) as client:
            client.get("https://generativelanguage.googleapis.com/v1beta/models")
        create.assert_called_once_with(None)
    reset_auto_route()


def test_quota_does_not_trigger_proxy_discovery():
    reset_auto_route()
    transport=RoutingTransport("auto")
    direct=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(429,json={"error":{"code":429,"message":"quota"}})))
    with patch.object(transport,"_new_client",return_value=direct), patch("local_mode.network.network_transport.discover_proxies") as discover:
        with httpx.Client(transport=transport) as client:
            assert client.get("https://generativelanguage.googleapis.com/v1beta/models").status_code==429
        discover.assert_not_called()
    assert current_auto_route()==""
    reset_auto_route()


def test_local_manual_proxy_keeps_manual_source_in_auto_mode():
    reset_auto_route()
    transport=RoutingTransport("auto","http://127.0.0.1:7890")
    transport.active_proxy="http://127.0.0.1:7890"
    with patch("local_mode.network.network_transport.save_cached_proxy") as save:
        transport._remember_route(httpx.Response(200))
    save.assert_called_once_with("http://127.0.0.1:7890",source="manual")
    reset_auto_route()
