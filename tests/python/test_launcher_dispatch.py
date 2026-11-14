"""Canonical batch dispatch with owned runtime/setup fixtures, never installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launchers")


@pytest.fixture(scope="module")
def runtime_template(tmp_path_factory):
    target = tmp_path_factory.mktemp("launcher runtime") / ".venv"
    result = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(target)],
                            capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    return target


@pytest.fixture
def launcher_root(tmp_path, runtime_template):
    root = tmp_path / "Jarvis path & spaces!"
    root.mkdir()
    for name in ("START.cmd", "INSTALL.cmd"):
        shutil.copyfile(ROOT / name, root / name)
    (root / "launch_local.py").write_text('''import ctypes,json,os,sys
from pathlib import Path
u=ctypes.windll.user32;k=ctypes.windll.kernel32
k.GetConsoleWindow.restype=ctypes.c_void_p
handle=k.GetConsoleWindow()
data={"argv":sys.argv[1:],"cwd":str(Path.cwd()),"exe":sys.executable,"visible_console":bool(handle and u.IsWindowVisible(ctypes.c_void_p(handle)))}
Path(__file__).with_name("dispatch.json").write_text(json.dumps(data),encoding="utf-8")
''', encoding="utf-8")
    (root / "setup_local.ps1").write_text((ROOT / "setup_local.ps1").read_text().split("$OutputEncoding", 1)[0] + '''@{Python=$Python;NonInteractive=[bool]$NonInteractive;Launch=[bool]$Launch;cwd=(Get-Location).Path}|ConvertTo-Json|Set-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot 'setup.json')
if ($env:JARVIS_FIXTURE_SETUP_FAIL -eq '1') {Write-Output 'Owned setup failure';exit 7}
Copy-Item -LiteralPath $env:JARVIS_FIXTURE_RUNTIME -Destination (Join-Path $PSScriptRoot '.venv') -Recurse
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'data')|Out-Null
Set-Content -Encoding ASCII -LiteralPath (Join-Path $PSScriptRoot 'data/setup-complete.txt') -Value 'unified-v4'
''', encoding="ascii")
    return root, runtime_template


def run_batch(root, name, args=(), *, environment=None):
    from local_mode.setup_state import _system_directory
    cmd = _system_directory() / "cmd.exe"
    command = subprocess.list2cmdline([str(root / name), *args])
    return subprocess.run(f'"{cmd}" /d /v:on /s /c "{command}"', cwd=ROOT,
                          env={**os.environ, **(environment or {})}, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                          timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)


def read_event(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            time.sleep(.02)
    pytest.fail(f"Owned launcher did not report dispatch: {path}")


@pytest.mark.parametrize("ready", [False, True])
def test_start_preserves_remote_and_quoted_arguments_after_cold_setup(launcher_root, ready):
    root, template = launcher_root
    if ready:
        shutil.copytree(template, root / ".venv")
        (root / "data").mkdir()
        (root / "data/setup-complete.txt").write_text("unified-v4\n", encoding="ascii")
    args = ["--remote", "--model", "gemini-3.5-flash-lite", "--window-title", "Owned window & value!"]
    result = run_batch(root, "START.cmd", args, environment={"JARVIS_FIXTURE_RUNTIME": str(template)})
    assert result.returncode == 0, result.stdout + result.stderr
    event = read_event(root / "dispatch.json")
    assert event["argv"] == args
    assert Path(event["cwd"]) == root
    assert Path(event["exe"]) == root / ".venv/Scripts/pythonw.exe"
    assert event["visible_console"] is False
    if ready:
        assert not (root / "setup.json").exists()
    else:
        setup = read_event(root / "setup.json")
        assert setup["Launch"] is False
        assert Path(setup["cwd"]) == root


def test_install_forwards_setup_options_with_spaces(launcher_root):
    root, template = launcher_root
    python = "C:/Owned Runtime/python.exe"
    result = run_batch(root, "INSTALL.cmd", ["-Python", python, "-NonInteractive"],
                       environment={"JARVIS_FIXTURE_RUNTIME": str(template)})
    assert result.returncode == 0, result.stdout + result.stderr
    setup = read_event(root / "setup.json")
    assert setup["Python"] == python
    assert setup["Launch"] is True and setup["NonInteractive"] is True
    assert Path(setup["cwd"]) == root


@pytest.mark.parametrize("name,feedback", [("START.cmd", "Could not prepare Jarvis"), ("INSTALL.cmd", "Installation did not complete")])
def test_setup_failure_is_reported_without_launching(launcher_root, name, feedback):
    root, template = launcher_root
    result = run_batch(root, name, environment={"JARVIS_FIXTURE_RUNTIME": str(template), "JARVIS_FIXTURE_SETUP_FAIL": "1"})
    assert result.returncode == 1
    assert "Owned setup failure" in result.stdout and feedback in result.stdout
    assert not (root / "dispatch.json").exists()


def test_install_rejects_runtime_arguments_before_setup(launcher_root):
    root, template = launcher_root
    result = run_batch(root, "INSTALL.cmd", ["--remote"], environment={"JARVIS_FIXTURE_RUNTIME": str(template)})
    assert result.returncode == 1
    assert "Use START.cmd for runtime options" in result.stdout + result.stderr
    assert not (root / "setup.json").exists()
    assert not (root / "dispatch.json").exists()


def test_repaired_start_keeps_only_owned_hidden_application_helper(launcher_root):
    root, template = launcher_root
    shutil.copytree(template, root / ".venv")
    (root / "data").mkdir()
    (root / "data/setup-complete.txt").write_text("unified-v4\n", encoding="ascii")
    shutil.copyfile(ROOT / "launch_local.py", root / "launch_local.py")
    package = root / "local_mode"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "instance.py").write_text("def is_running(root): return False\n")
    (package / "setup_state.py").write_text('''import os

def check_environment(root):
    marker=root/"repair-helper.pid"
    if marker.exists(): return False
    marker.write_text(str(os.getpid()))
    return True
''')
    (package / "cli.py").write_text('''import ctypes,json,os,sys,time
from pathlib import Path

def main(args):
    root=Path(__file__).resolve().parents[1]
    k=ctypes.windll.kernel32;u=ctypes.windll.user32
    k.GetConsoleWindow.restype=ctypes.c_void_p
    handle=k.GetConsoleWindow()
    data={"pid":os.getpid(),"args":args,"cwd":str(Path.cwd()),"exe":sys.executable,"telegram_inherited":os.environ.get("JARVIS_TELEGRAM_TOKEN")=="owned-fixture-token","visible_console":bool(handle and u.IsWindowVisible(ctypes.c_void_p(handle)))}
    (root/"helper.json").write_text(json.dumps(data),encoding="utf-8")
    deadline=time.monotonic()+20
    while not (root/"release").exists() and time.monotonic()<deadline: time.sleep(.02)
    return 0
''')
    args = ["--remote", "--model", "gemini-3.5-flash-lite", "--window-title", "Owned title & !"]
    child = None
    try:
        result = run_batch(root, "START.cmd", args, environment={"JARVIS_TELEGRAM_TOKEN": "owned-fixture-token"})
        assert result.returncode == 0, result.stdout + result.stderr
        event = read_event(root / "helper.json")
        import psutil
        child = psutil.Process(event["pid"])
        assert str(root / "launch_local.py").casefold() in [x.casefold() for x in child.cmdline()]
        assert event["args"] == args and event["telegram_inherited"] is True
        assert Path(event["cwd"]) == root
        assert event["visible_console"] is False
        repair_pid = int((root / "repair-helper.pid").read_text())
        assert repair_pid != event["pid"]
        try:
            psutil.Process(repair_pid).wait(timeout=5)
        except psutil.NoSuchProcess:
            pass
        (root / "release").write_text("release")
        child.wait(timeout=5)
        print(json.dumps({"owned_helper_visibility": "hidden", "repair_helper_exited": True,
                          "application_helper_exited_after_release": True, "arguments_preserved": True,
                          "telegram_environment_preserved": True, "root_with_spaces_bang_ampersand": True}))
    finally:
        (root / "release").write_text("release")
        if child is not None:
            try:
                child.wait(timeout=3)
            except psutil.TimeoutExpired:
                assert str(root / "launch_local.py").casefold() in [x.casefold() for x in child.cmdline()]
                child.terminate()
                child.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass
