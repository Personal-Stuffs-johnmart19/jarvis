"""PC/browser adapter regressions. No real OS input, Chrome or media actions."""
from unittest.mock import Mock, patch

import pytest

from local_mode.tools import ToolRegistry
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent
from safety import classify, Risk
from jsonschema import ValidationError


def test_shared_bridge_standard_controls_need_no_approval():
    core = ToolRegistry()
    services = ServiceRegistry(Mock(), lambda: core)
    bridge = SharedAgent(Mock())
    for name, args in [
        ("desktop_control", {"action": "list"}),
        ("desktop_control", {"action": "current_wallpaper"}),
        ("game_updater", {"action": "list"}),
        ("game_updater", {"action": "download_status"}),
        ("computer_settings", {"action": "volume_set", "value": 0}),
        ("browser_control", {"action": "snapshot"}),
        ("media_control", {"action": "pause"}),
    ]:
        with patch.object(core, "_invoke", return_value={"ok": True}) as execute:
            receipt = bridge.prepare(services, name, args)
            assert not receipt["requires_approval"], (name, args)
            assert bridge.execute(services, receipt["token"])["ok"]
            execute.assert_called_once_with(name, args)
            with pytest.raises(ValueError, match="consumed"):
                bridge.execute(services, receipt["token"])


@pytest.mark.parametrize("action", ["restart", "restart-computer", "reboot", "reboot_computer", "shutdown", "shut down", "power_off", "turn_off_computer"])
def test_power_aliases_keep_confirmation(action):
    assert classify("computer_settings", {"action": action}) == Risk.HIGH


def test_browser_legacy_entry_uses_same_current_backend():
    from actions import browser_control as module
    with patch.object(module, "_api", Mock(return_value={"ok": True})) as api:
        args = {"action": "snapshot", "profile_id": "Profile 2"}
        assert module.browser_control(args)["ok"]
        api.assert_called_once_with(args)
    assert callable(ToolRegistry().special["browser_control"])


def test_media_never_emits_global_keys_and_preserves_target():
    from actions.media_control import media_control_action
    with patch("actions.browser_control.browser_control", return_value={"ok": True}) as browser:
        assert media_control_action({"action": "pause", "tab_id": "2", "profile_id": "Default"})["ok"]
        browser.assert_called_once_with({"action": "media_pause", "tab_id": "2", "profile_id": "Default"})
    with patch("actions.browser_control.browser_control", side_effect=[{"ok": True, "media": {"video": True, "paused": True}}, {"ok": True}]) as browser:
        media_control_action({"action": "toggle"})
        assert browser.call_args.args[0]["action"] == "media_resume"


def test_settings_explicit_values_no_hidden_model_or_pyautogui():
    from actions import computer_settings as module
    with patch.object(module, "_pg", side_effect=AssertionError("OS input not allowed")), patch.object(module, "volume_set") as volume, patch.object(module, "brightness_set") as brightness:
        module.computer_settings({"action": "volume_set", "value": 0})
        module.computer_settings({"action": "brightness_set", "value": "0"})
        volume.assert_called_once_with(0)
        brightness.assert_called_once_with(0)
        assert "explicit action" in module.computer_settings({"description": "anything"})


def test_unicode_typing_uses_clipboard_for_short_text():
    from actions import computer_control as module
    with patch.object(module, "_pg", return_value=Mock()) as pg, patch.object(module, "_PYPERCLIP", True), patch.object(module.pyperclip, "copy") as copy, patch.object(module.time, "sleep"):
        module._type_text("Привет 🌍")
        copy.assert_called_once_with("Привет 🌍")
        pg.return_value.typewrite.assert_not_called()
        pg.return_value.hotkey.assert_called_once_with("ctrl", "v")


def test_all_declared_handlers_import_without_executing_actions():
    from local_mode.capabilities import report
    result = report()
    assert result["execution_tested"] is False
    assert all(item["handler_available"] for item in result["tools"]), result["tools"]


def test_schema_reaches_drag_and_bounds_wait():
    registry = ToolRegistry()
    registry.validate("computer_control", {"action": "drag", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration": 0.2})
    with pytest.raises(ValidationError):
        registry.validate("computer_control", {"action": "wait", "seconds": 36000})


def test_power_confirmation_description_stays_text():
    from safety import describe
    assert isinstance(describe("computer_settings", {"action": "reboot"}), str)


def test_volume_backend_failure_is_not_success():
    from actions import computer_settings as module
    with patch("pycaw.pycaw.AudioUtilities.GetSpeakers", side_effect=RuntimeError("no endpoint")):
        result = module.computer_settings({"action": "volume_set", "value": 40})
    assert "Could not set volume" in result


def test_clipboard_fallback_decodes_unicode_without_pointer_truncation():
    from actions import clipboard as module
    with patch.object(module, "run_hidden", return_value=Mock(stdout="Привет 🌍\r\n")) as run:
        assert module._win_clip_get() == "Привет 🌍"
        assert run.call_args.kwargs["encoding"] == "utf-8"
