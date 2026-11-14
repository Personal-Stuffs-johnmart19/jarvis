"""Точная маршрутизация основного Chrome через официальный MCP."""
import threading
from unittest.mock import Mock, patch
from local_mode import chrome_mcp
from local_mode.browser_api import BrowserAPI


def test_main_chrome_uses_explicit_profile_without_separate_browser():
    with patch("local_mode.browser_profiles.read_preferences",return_value={"mode":"specific","profile":"Profile 2"}), patch("local_mode.browser_profiles.profiles",return_value=([{"id":"Default"},{"id":"Profile 2"}],"Default")), patch.object(chrome_mcp,"execute",return_value={"ok":True}) as execute:
        assert BrowserAPI()({"action":"snapshot"})["ok"]
        assert execute.call_args.args[0] == "Profile 2"


def test_explicit_profile_per_command_overrides_ui_selection():
    with patch("local_mode.browser_profiles.read_preferences",return_value={"mode":"specific","profile":"Default"}), patch("local_mode.browser_profiles.profiles",return_value=([{"id":"Default"},{"id":"Profile 2"}],"Default")), patch.object(chrome_mcp,"execute",return_value={"ok":True}) as execute:
        BrowserAPI()({"action":"list_tabs","profile_id":"Profile 2"})
        assert execute.call_args.args[0] == "Profile 2"


def test_refs_are_converted_to_current_sdk_targets():
    session=chrome_mcp.ChromeSession.__new__(chrome_mcp.ChromeSession)
    session.profile="Profile 2"
    session.tools={"browser_click":{"inputSchema":{"properties":{"target":{}}}}}
    session.request=Mock(return_value={"content":[{"type":"text","text":"snapshot"}]})
    result=session.tool("browser_click",{"ref":"e14"})
    assert result["ok"] and result["untrusted_content"]
    assert session.request.call_args.args[1]["arguments"] == {"target":"e14"}


def test_no_first_element_click_without_observed_ref():
    import pytest
    session=chrome_mcp.ChromeSession.__new__(chrome_mcp.ChromeSession)
    session.lock=threading.RLock()
    session.tool=Mock()
    with pytest.raises(ValueError,match="ref"):
        session.execute({"action":"click","text":"Войти"})
    session.tool.assert_not_called()


def test_missing_official_extension_returns_setup_without_launch():
    with patch.object(chrome_mcp,"installed",return_value=False),patch.object(chrome_mcp,"ChromeSession") as launch:
        result=chrome_mcp.execute("Profile 2",{"action":"snapshot"})
        assert result["requires_setup"]
        launch.assert_not_called()
