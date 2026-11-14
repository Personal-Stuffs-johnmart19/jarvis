"""Windows aliases must not bypass canonical credential/path validation."""
import os
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock

import pytest

from local_mode.tools import ToolRegistry, _windows_file_path, validate_paths


@pytest.mark.parametrize("path", [
    r"C:\work\api_keys.json::$DATA", r"C:\work\file.txt:stream",
    r"C:\work\.codex.\notes.txt", r"C:\work\trailing \notes.txt",
    r"C:\work\NUL.txt", r"C:\work\COM¹", r"C:\work\LPT3.log",
    r"C:\work\NUL .txt", r"C:\work\CON .log", r"C:\work\COM1 .txt",
    r"\\.\PhysicalDrive0", r"\\?\GLOBALROOT\Device\HarddiskVolume1\secret",
    r"C:relative.txt", "C:\\work\\line\nname",
])
def test_windows_device_stream_and_alias_paths_are_rejected(path):
    with pytest.raises(ValueError, match="Windows device paths"):
        _windows_file_path(path)


@pytest.mark.parametrize("path", [r"C:\work\file.txt", r"C:\work\..\file.txt",
    r"\\wsl.localhost\Ubuntu\home\user\notes.txt", r"C:\работа\заметки.txt"])
def test_ordinary_unicode_parent_and_wsl_paths_remain_valid(path):
    assert PureWindowsPath(_windows_file_path(path)) == PureWindowsPath(path)


def test_long_path_prefixes_use_the_same_credential_comparison():
    assert _windows_file_path(r"\\?\C:\work\file.txt") == r"C:\work\file.txt"
    assert _windows_file_path(r"\\?\UNC\server\share\file.txt") == r"\\server\share\file.txt"


@pytest.mark.parametrize("target", [".ssh/key", ".env", "api_keys.json"])
def test_resolved_credential_targets_cannot_hide_behind_ordinary_names(tmp_path, monkeypatch, target):
    alias = tmp_path / "ordinary.txt"
    resolved = tmp_path / target
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        return resolved if path == alias else original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="keys or tokens"):
        validate_paths({"path": str(alias)})


@pytest.mark.skipif(os.name != "nt", reason="Canonical Windows filesystem integration")
def test_derived_stream_name_is_rejected_before_reading(tmp_path, monkeypatch):
    from actions import file_controller
    reader = Mock(side_effect=AssertionError("Unexpected file read"))
    monkeypatch.setattr(file_controller, "read_file", reader)
    result = ToolRegistry().execute("file_controller", {
        "action": "read", "path": str(tmp_path), "name": "api_keys.json::$DATA"})
    assert result["ok"] is False
    reader.assert_not_called()
    with pytest.raises(ValueError, match="keys"):
        validate_paths({"path": r"\\?\C:\work\api_keys.json"})
