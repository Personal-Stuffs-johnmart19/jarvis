"""Windows entrypoint parsing and synthetic setup dispatch, never installation."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name", ["setup_local.ps1", "start_local.ps1", "start_jarvis.ps1", "START.cmd", "INSTALL.cmd", "Jarvis.cmd", "start_remote.cmd"])
def test_windows_entrypoint_source_is_ascii_for_legacy_hosts(name):
    (ROOT / name).read_text(encoding="utf-8-sig").encode("ascii")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 parser")
@pytest.mark.parametrize("name", ["setup_local.ps1", "start_local.ps1", "start_jarvis.ps1"])
def test_windows_powershell_parses_supported_entrypoints_without_executing(name):
    from local_mode.setup_state import _system_directory
    host = _system_directory() / "WindowsPowerShell/v1.0/powershell.exe"
    script = "$e=$null;$t=$null;[System.Management.Automation.Language.Parser]::ParseFile($env:JARVIS_PARSE_SCRIPT,[ref]$t,[ref]$e)|Out-Null;[pscustomobject]@{version=$PSVersionTable.PSVersion.Major;errors=@($e|ForEach-Object{$_.ErrorId})}|ConvertTo-Json -Compress"
    result = subprocess.run([str(host), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                            env={**os.environ, "JARVIS_PARSE_SCRIPT": str(ROOT / name)}, capture_output=True,
                            encoding="utf-8", errors="strict", timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["version"] == 5
    assert parsed["errors"] == []


def test_python_setup_entrypoint_passes_absolute_root_and_arguments_without_installing(monkeypatch):
    call = Mock(return_value=7)
    monkeypatch.setattr(subprocess, "call", call)
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "-Python", "C:/Fixture Runtime/python.exe", "-NonInteractive"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(ROOT / "tools/setup/bootstrap.py"), run_name="__main__")
    assert exited.value.code == 7
    call.assert_called_once_with(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  str(ROOT / "setup_local.ps1"), "-Python", "C:/Fixture Runtime/python.exe", "-NonInteractive"])
