from unittest.mock import patch, Mock
from pathlib import Path
from actions.game_updater import (
    _steam_state,
    _update_steam_games,
    steam_ready_result,
    _is_steam_running,
)


def test_combined_installed_flags_are_not_updates():
    assert _steam_state(4) == "ready"
    assert _steam_state(4 | 8192) == "ready"
    assert _steam_state(4 | 2) == "pending"
    assert _steam_state(2 | 1024) == "pending"
    assert _steam_state(2 | 256 | 1048576) == "active"
    assert _steam_state(0) == "unknown"


def test_ready_game_does_not_launch_steam_or_start_update():
    game = {"id": "570", "name": "Dota 2", "state": 4 | 8192}
    with patch("actions.game_updater._get_steam_games", return_value=[game]), patch(
        "actions.game_updater._ensure_steam_running"
    ) as launch, patch("actions.game_updater.subprocess.Popen") as spawn:
        assert "already up to date" in _update_steam_games(Path("fixture"), "Dota 2")
        launch.assert_not_called()
        spawn.assert_not_called()
    with patch(
        "actions.game_updater._find_steam_path", return_value=Path("fixture")
    ), patch("actions.game_updater._get_steam_games", return_value=[game]):
        assert (
            steam_ready_result(
                {"platform": "steam", "action": "install", "game_name": "Dota 2"}
            )["verification"]
            == "local_manifest"
        )


def test_steam_process_detection_does_not_depend_on_tasklist_encoding():
    with patch("psutil.process_iter", return_value=[Mock(info={"name": "steam.exe"})]):
        assert _is_steam_running()
