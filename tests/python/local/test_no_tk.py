"""Снятый Tk не должен быть нужен инструментам и сервису."""

import subprocess
import sys
from pathlib import Path


def test_all_tools_import_without_tkinter():
    code = "import sys;sys.modules['tkinter']=None;from local_mode.capabilities import report;assert report()['ok']"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
