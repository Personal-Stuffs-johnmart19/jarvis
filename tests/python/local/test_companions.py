"""Embedded workspace broker ownership and preserved browser preferences."""

import json
from unittest.mock import Mock, patch
import pytest
from local_mode import companions, browser_profiles


def test_bad_url_never_launches_browser():
    with patch("subprocess.Popen") as launch:
        for url in (
            "file:///C:/private",
            "javascript:alert(1)",
            "https://user:password@example.com",
            "https://example.com/\n",
        ):
            with pytest.raises(ValueError):
                browser_profiles.open_url(url)
        launch.assert_not_called()


def test_removed_browser_preferences_migrate_to_current_chrome(tmp_path, monkeypatch):
    preferences = tmp_path / "browser.json"
    preferences.write_text(
        json.dumps({"mode": "specific", "profile": "old", "engine": "removed-browser"})
    )
    monkeypatch.setattr(browser_profiles, "PREFERENCES", preferences)
    assert browser_profiles.read_preferences() == {"mode": "current", "profile": ""}
    with pytest.raises(ValueError):
        browser_profiles.save_preferences("current", engine="removed-browser")

def test_workbench_bootstrap_reuses_and_closes_only_its_private_broker(monkeypatch):
    broker = Mock()
    broker.environment.return_value = {
        "JARVIS_AI_TOKEN": "ephemeral",
        "JARVIS_AI_BASE": "http://127.0.0.1:1234/v1beta",
        "JARVIS_AI_MODEL": "fixture",
    }
    create = Mock(return_value=broker)
    monkeypatch.setattr(companions, "GeminiBroker", create)
    monkeypatch.setattr(companions, "_BROKER", None)
    assert companions.workbench_environment() == broker.environment.return_value
    assert companions.workbench_environment() == broker.environment.return_value
    create.assert_called_once_with()
    companions.close()
    broker.close.assert_called_once_with()
    assert companions._BROKER is None
    companions.close()
    broker.close.assert_called_once_with()
