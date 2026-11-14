"""Headless subprocess execution preserves output, failure and console semantics."""
import os
import subprocess
from unittest.mock import patch

import pytest

from local_mode.processes import run


@pytest.mark.skipif(os.name != "nt", reason="Windows console contract")
def test_background_commands_hide_windows_console():
    with patch("local_mode.processes.subprocess.run") as child:
        run(["powershell", "-NoProfile", "-Command", "Write-Output ok"], capture_output=True)
        assert child.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert child.call_args.kwargs["capture_output"]


@pytest.mark.skipif(os.name != "nt", reason="Windows console contract")
def test_explicit_terminal_request_keeps_console():
    with patch("local_mode.processes.subprocess.run") as child:
        run(["cmd"], creationflags=subprocess.CREATE_NEW_CONSOLE)
        assert child.call_args.kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE


@pytest.mark.skipif(os.name != "nt", reason="Windows console contract")
def test_real_readonly_powershell_returns_output_without_console():
    result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Write-Output jarvis-test"],
                 capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    assert result.stdout.strip() == "jarvis-test"


def test_failed_shell_output_is_not_success():
    from actions.cmd_control import _run_silent
    result = subprocess.CompletedProcess(["cmd"], 7, stdout="partial output", stderr="access denied")
    with patch("local_mode.owned_process.run_owned", return_value=result):
        outcome = _run_silent("some-command")
    assert outcome["ok"] is False and outcome["exit_code"] == 7
    assert outcome["stdout"] == "partial output" and outcome["stderr"] == "access denied"




@pytest.mark.skipif(os.name != "nt", reason="Windows console contract")
def test_real_powershell_preserves_russian_output():
    result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "Write-Output 'Русский ответ'"],
                 capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0
    assert result.stdout.strip() == "Русский ответ"
