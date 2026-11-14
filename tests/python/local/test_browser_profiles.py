"""Маршрутизация профилей без запуска браузера и Computer Use."""
import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from local_mode import browser_profiles as bp
from local_mode.messages import OwnedText, OwnedValueError, message


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "PREFERENCES", tmp_path / "preferences.json")
    monkeypatch.setattr(bp, "MANAGED", tmp_path / "managed")
    monkeypatch.setattr(bp, "user_data", lambda channel="chrome": tmp_path)
    (tmp_path / "Local State").write_text(json.dumps({"profile": {
        "last_used": "Profile 2", "info_cache": {
            "Default": {"name": "Личный"}, "Profile 2": {"name": "Рабочий"},
            "../../bad": {"name": "Нельзя"}}}}), encoding="utf-8")
    monkeypatch.setattr(bp, "executable", lambda channel: "chrome.exe")
    return tmp_path


def test_default_uses_last_used_profile_without_separate_data_directory(storage):
    args = bp.launch_arguments("https://example.com/")
    assert "--profile-directory=Profile 2" in args
    assert not any(arg.startswith("--user-data-dir") for arg in args)
    assert not any("debugging" in arg for arg in args)
    assert len(bp.profiles()[0]) == 2


def test_selected_profile_is_saved_and_applied(storage):
    bp.save_preferences("specific", "Default")
    args = bp.launch_arguments("https://example.com/")
    assert "--profile-directory=Default" in args
    assert bp.read_preferences() == {"mode": "specific", "profile": "Default"}
    assert not any("user-data-dir" in arg for arg in args)


def test_new_profile_is_stable_and_does_not_touch_personal_profile(storage):
    bp.save_preferences("new")
    first = bp.launch_arguments("https://example.com/")
    assert first == bp.launch_arguments("https://example.com/")
    assert "--user-data-dir=" + str(bp.MANAGED) in first
    assert not any("profile-directory" in arg for arg in first)
    assert not bp.MANAGED.exists()


def test_deleted_profile_is_reported_instead_of_silent_switch(storage):
    bp.save_preferences("specific", "Default")
    (storage / "Local State").write_text('{"profile": {"info_cache": {}}}')
    with pytest.raises(OwnedValueError, match="The selected profile no longer exists") as caught:
        bp.launch_arguments("https://example.com/")
    payload = caught.value.payload()
    assert "больше не найден" in message(payload["message"], *payload["values"], locale="ru")


@pytest.mark.parametrize("url", ["file:///C:/private", "javascript:alert(1)", "https://x/\n--flag"])
def test_invalid_urls_are_rejected(storage, url):
    with pytest.raises(ValueError): bp.launch_arguments(url)


def test_navigation_uses_native_selected_profile_without_starting_playwright(storage):
    from actions.browser_control import browser_control
    with patch("local_mode.chrome_mcp.installed", return_value=False), patch("local_mode.browser_profiles.subprocess.Popen") as launch, patch("actions.browser_control._ensure_started") as start:
        result = browser_control({"action": "search", "query": "кот & музыка"})
    start.assert_not_called()
    assert "Opened in the selected browser profile" in result["reply"]
    assert isinstance(result["reply"], OwnedText)
    payload = result["reply"].payload()
    assert "выбранном профиле" in message(payload["message"], *payload["values"], locale="ru")
    assert "--profile-directory=Profile 2" in launch.call_args.args[0]
    assert "%26" in launch.call_args.args[0][-1]


def test_personal_profile_never_silently_uses_automated_browser(storage):
    from actions.browser_control import browser_control
    with patch("local_mode.chrome_mcp.installed", return_value=False), patch("actions.browser_control._ensure_started") as start:
        result = browser_control({"action": "click", "text": "Войти"})
    assert result["requires_setup"] and not result["ok"]
    start.assert_not_called()


def test_youtube_personal_profile_does_not_claim_verified_playback(storage):
    from local_mode.youtube import YouTubePlayer
    response = Mock(text='{"videoRenderer":{"videoId":"abcdefghijk"}}')
    player = YouTubePlayer(Path("unused"))
    with patch("requests.get", return_value=response), patch("local_mode.browser_profiles.open_url") as launch, patch.object(player, "_ensure") as ensure:
        result = player.play("джаз")
    ensure.assert_not_called()
    assert not result["verified"]
    assert "не подтверждено" in result["reply"]
    launch.assert_called_once_with("https://www.youtube.com/watch?v=abcdefghijk&autoplay=1", "chrome")
