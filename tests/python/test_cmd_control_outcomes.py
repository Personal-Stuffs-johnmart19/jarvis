"""Owned command fixtures only; visible-terminal launch is always mocked."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from actions import cmd_control as commands
from local_mode.owned_process import owned_action_cancel
from local_mode import owned_process
from local_mode.tools import ToolRegistry


def script_command(tmp_path, source):
    script = tmp_path / "fixture.py"
    script.write_text(source, encoding="utf-8")
    executable = getattr(sys, "_base_executable", None) or sys.executable
    return f'"{executable}" "{script}"'


@pytest.mark.parametrize("exit_code", [0, 7])
def test_real_registry_command_reports_exact_exit_and_streams(tmp_path, exit_code):
    command = script_command(tmp_path, f"import sys\nprint('OUT',flush=True)\nprint('ERR',file=sys.stderr,flush=True)\nsys.exit({exit_code})\n")
    registry = ToolRegistry()
    args = {"task": "owned fixture", "command": command, "visible": False}
    assert registry.risky("cmd_control", args) is True
    pending = registry.execute("cmd_control", args)
    assert pending["requires_confirmation"] is True
    result = registry.execute("cmd_control", args, approved=True)
    assert result["ok"] is (exit_code == 0)
    assert result["exit_code"] == exit_code
    assert result["stdout"].strip() == "OUT" and result["stderr"].strip() == "ERR"
    assert result["output_complete"] is True
    assert result["status"] == ("succeeded" if exit_code == 0 else "failed")


def test_timeout_stops_tree_and_labels_missing_partial_output(tmp_path):
    result = commands._run_silent(script_command(tmp_path, "import time\ntime.sleep(20)\n"), timeout=1)
    assert result["ok"] is False and result["timed_out"] is True
    assert result["output_complete"] is False and result["exit_code"] is None


def test_output_limit_is_terminal_incomplete_failure(tmp_path):
    result = commands._run_silent(script_command(tmp_path, "import sys\nsys.stdout.write('x'*100000)\nsys.stdout.flush()\n"))
    assert result["ok"] is False and result["status"] == "output_limit"
    assert result["truncated"] is True and result["output_complete"] is False


def _open_windows_fixture_processes(pids):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetExitCodeProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handles = []
    try:
        for pid in pids:
            handle = api.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append((pid, handle))
    except BaseException:
        for _, handle in handles:
            api.CloseHandle(handle)
        raise
    return api, handles


def _assert_windows_fixture_stopped(api, pid, handle, *, wait_ms=0):
    # A retained kernel handle identifies this exact process even if a PID is
    # reused. psutil's Windows status() reports suspension, not an exit signal.
    wait_result = api.WaitForSingleObject(handle, wait_ms)
    exit_code = wintypes.DWORD()
    assert api.GetExitCodeProcess(handle, ctypes.byref(exit_code)), "Cannot query owned fixture exit code"
    assert wait_result == 0, f"Owned fixture {pid} is not stopped: kernel_wait={wait_result}, exit_code={exit_code.value}"


def test_shared_cancel_scope_reaps_owned_command_and_descendant(tmp_path):
    marker = tmp_path / "pids.txt"
    source = (
        "import os,subprocess,sys,time\n"
        "from pathlib import Path\n"
        "flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],creationflags=flags)\n"
        f"ready=Path({str(marker)!r})\n"
        "pending=ready.with_suffix('.tmp')\n"
        "pending.write_text(str(os.getpid())+','+str(child.pid))\n"
        "pending.replace(ready)\n"
        "time.sleep(30)\n"
    )
    cancel = threading.Event()
    observed = {}
    observation_errors = []
    def stop_when_started():
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        try:
            if marker.exists():
                pids = [int(value) for value in marker.read_text().split(',')]
                if os.name == "nt":
                    observed["windows"] = _open_windows_fixture_processes(pids)
                else:
                    processes = [psutil.Process(pid) for pid in pids]
                    for process in processes:
                        process.create_time()  # Cache identity before cancellation/PID reuse.
                    observed["unix"] = processes
        except Exception as error:
            observation_errors.append(str(error))
        finally:
            # Job accounting/root completion may precede descendant object
            # signaling. Both retained handles share the existing 3s cleanup
            # budget, beginning at cancellation rather than at this assertion.
            observed["cleanup_deadline"] = time.monotonic() + 3
            cancel.set()
    watcher = threading.Thread(target=stop_when_started, daemon=True)
    watcher.start()
    try:
        with owned_action_cancel(cancel):
            result = commands._run_silent(script_command(tmp_path, source))
        watcher.join(timeout=1)
        assert not watcher.is_alive()
        assert marker.exists(), "Owned fixture must have started before cancellation"
        assert not observation_errors, observation_errors
        assert result["ok"] is False and result["cancelled"] is True
        assert result["output_complete"] is False
        if os.name == "nt":
            api, handles = observed["windows"]
            assert len(handles) == 2
            for pid, handle in handles:
                remaining_ms = max(0, int((observed["cleanup_deadline"] - time.monotonic()) * 1000))
                _assert_windows_fixture_stopped(api, pid, handle, wait_ms=remaining_ms)
        else:
            assert len(observed["unix"]) == 2
            for process in observed["unix"]:
                try:
                    assert not process.is_running() or process.status() == psutil.STATUS_ZOMBIE
                except psutil.NoSuchProcess:
                    pass
    finally:
        if "windows" in observed:
            api, handles = observed["windows"]
            for _, handle in handles:
                api.CloseHandle(handle)


@pytest.mark.parametrize("wait_result,exit_code", [(0, 1), (258, 259), (258, 1), (0xFFFFFFFF, 259)])
def test_fixture_exit_proof_uses_kernel_identity_and_rejects_live_or_failed_wait(monkeypatch, wait_result, exit_code):
    def query(_handle, output):
        output._obj.value = exit_code
        return True
    api = SimpleNamespace(WaitForSingleObject=Mock(return_value=wait_result), GetExitCodeProcess=Mock(side_effect=query))
    unstable_pid_lookup = Mock(side_effect=AssertionError("Must use the pre-cancel process handle"))
    monkeypatch.setattr(psutil, "Process", unstable_pid_lookup)
    if wait_result == 0:
        _assert_windows_fixture_stopped(api, 123, 456)
    else:
        with pytest.raises(AssertionError, match="kernel_wait="):
            _assert_windows_fixture_stopped(api, 123, 456)
    api.WaitForSingleObject.assert_called_once_with(456, 0)
    unstable_pid_lookup.assert_not_called()


def test_windows_job_cleanup_waits_for_descendant_ack_before_closing(monkeypatch):
    events = []
    active_counts = iter((2, 1, 0))
    def query(_handle, _kind, info, _size, _length):
        info._obj.active_processes = next(active_counts)
        events.append(f"active:{info._obj.active_processes}")
        return True
    job = owned_process._WindowsJob.__new__(owned_process._WindowsJob)
    job.handle = 123
    job.api = SimpleNamespace(
        TerminateJobObject=Mock(side_effect=lambda *_: events.append("terminate") or True),
        QueryInformationJobObject=Mock(side_effect=query),
        CloseHandle=Mock(side_effect=lambda *_: events.append("close")),
    )
    monkeypatch.setattr(owned_process.time, "monotonic", lambda: 0)
    monkeypatch.setattr(owned_process.time, "sleep", lambda _: None)
    job.close()
    assert events == ["terminate", "active:2", "active:1", "active:0", "close"]
    assert job.handle is None


@pytest.mark.parametrize("failure", ["terminate", "query", "deadline"])
def test_windows_job_cleanup_failure_never_claims_tree_stopped(monkeypatch, failure):
    def query(_handle, _kind, info, _size, _length):
        info._obj.active_processes = 1
        return failure != "query"
    job = owned_process._WindowsJob.__new__(owned_process._WindowsJob)
    job.handle = 123
    job.api = SimpleNamespace(
        TerminateJobObject=Mock(return_value=failure != "terminate"),
        QueryInformationJobObject=Mock(side_effect=query),
        CloseHandle=Mock(),
    )
    clock = iter((0, 4))
    monkeypatch.setattr(owned_process.time, "monotonic", lambda: next(clock))
    with pytest.raises(owned_process.ProcessCleanupError):
        job.close()
    job.api.CloseHandle.assert_called_once_with(123)
    assert job.handle is None


def test_command_cleanup_failure_reports_uncertain_outcome(monkeypatch):
    monkeypatch.setattr(owned_process, "run_owned", Mock(side_effect=owned_process.ProcessCleanupError("fixture")))
    result = commands._run_silent("fixture.exe")
    assert result["ok"] is False and result["status"] == "cleanup_error"
    assert result["cancelled"] is False and result["timed_out"] is False
    assert result["output_complete"] is False and result["exit_code"] is None
    assert "may still be running" in str(result["error"])
    assert "was stopped" not in str(result["error"])


@pytest.mark.parametrize("failure", ["kill", "wait", "handle", "stdin", "reader"])
def test_cleanup_attempts_all_resources_and_preserves_original_uncertainty(failure):
    events = []
    original = owned_process.ProcessCleanupError("original unconfirmed job cleanup")
    def operation(name):
        events.append(name)
        if name == failure:
            raise OSError("fixture cleanup failure")
    process = SimpleNamespace(
        poll=lambda: None,
        kill=lambda: operation("kill"),
        wait=lambda **_: operation("wait"),
        _handle=SimpleNamespace(Close=lambda: operation("handle")),
        stdin=SimpleNamespace(closed=False, close=lambda: operation("stdin")),
    )
    reader = SimpleNamespace(join=lambda **_: operation("reader"))
    with pytest.raises(owned_process.ProcessCleanupError) as caught:
        owned_process._finish_cleanup(process, [reader], lambda: operation("tree"), lambda: 0, original, windows=True)
    assert caught.value is original
    assert events == ["tree", "kill", "wait", "handle", "stdin", "reader"]


def test_cleanup_root_wait_and_readers_share_remaining_deadline():
    clock = [0]
    timeouts = []
    deadline = 3
    def tree():
        clock[0] = 2  # Job acknowledgement already used two seconds.
    def wait(*, timeout):
        timeouts.append(("wait", timeout))
        clock[0] = 3
    process = SimpleNamespace(poll=lambda: 0, wait=wait, stdin=None)
    reader = SimpleNamespace(join=lambda **kwargs: timeouts.append(("reader", kwargs["timeout"])))
    owned_process._finish_cleanup(process, [reader], tree, lambda: max(0, deadline-clock[0]), None, windows=False)
    assert timeouts == [("wait", 1), ("reader", 0)]


def test_already_cancelled_scope_never_spawns(monkeypatch):
    spawn = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    cancel = threading.Event()
    cancel.set()
    with owned_action_cancel(cancel):
        result = commands._run_silent("owned fixture")
    assert result["cancelled"] is True and result["ok"] is False
    spawn.assert_not_called()


def test_visible_pre_cancelled_scope_never_spawns(monkeypatch):
    spawn = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    cancel = threading.Event()
    cancel.set()
    with owned_action_cancel(cancel):
        result = commands.cmd_control({"task": "owned fixture", "command": "fixture.exe", "visible": True})
    assert result["cancelled"] is True and result["ok"] is False
    spawn.assert_not_called()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-32"])
def test_unicode_command_bytes_preserve_exact_text_and_encoding(encoding):
    text = "Русский ответ 😀\n"
    result = commands._command_result("succeeded", stdout=text.encode(encoding), exit_code=0)
    assert result["stdout"] == text and result["stdout_encoding"] == encoding
    assert result["output_complete"] is True
    assert result["decoding_loss"] is False and result["encoding_uncertain"] is False


def test_native_oem_fallback_reports_assumption_without_claiming_complete_text(monkeypatch):
    monkeypatch.setattr(commands, "_native_output_encoding", lambda: "cp866")
    result = commands._command_result("succeeded", stdout="Русский ответ".encode("cp866"), exit_code=0)
    assert result["stdout"] == "Русский ответ" and result["stdout_encoding"] == "cp866"
    assert result["decoding_loss"] is False and result["encoding_uncertain"] is True
    assert result["output_complete"] is False


def test_invalid_utf8_bytes_report_decoding_loss_when_no_native_encoding_is_known(monkeypatch):
    monkeypatch.setattr(commands, "_native_output_encoding", lambda: None)
    result = commands._command_result("succeeded", stdout=b"prefix\xffsuffix", exit_code=0)
    assert result["stdout"] == "prefix\ufffdsuffix"
    assert result["decoding_loss"] is True and result["output_complete"] is False


def test_malformed_bom_output_is_not_reinterpreted_as_oem(monkeypatch):
    monkeypatch.setattr(commands, "_native_output_encoding", Mock(side_effect=AssertionError("BOM wins")))
    result = commands._command_result("succeeded", stdout=b"\xef\xbb\xbf\xff", exit_code=0)
    assert result["stdout_encoding"] == "utf-8-sig" and result["decoding_loss"] is True
    assert result["output_complete"] is False


def test_visible_launch_is_truthfully_spawned_not_completed(monkeypatch):
    monkeypatch.setattr(commands, "_get_platform", lambda: "windows")
    spawn = Mock(return_value=SimpleNamespace(pid=4242))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    result = commands.cmd_control({"task": "owned fixture", "command": "fixture.exe", "visible": True})
    assert result["status"] == "spawned" and result["pid"] == 4242
    assert result["completed"] is False and result["output_complete"] is False
    assert result["exit_code"] is None
    assert spawn.call_args.args[0] == 'cmd.exe /d /s /k "fixture.exe"'


def test_visible_spawn_failure_cannot_report_success(monkeypatch):
    monkeypatch.setattr(commands, "_get_platform", lambda: "windows")
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=OSError("owned spawn failure")))
    result = ToolRegistry().execute("cmd_control", {"task": "owned fixture", "command": "fixture.exe", "visible": True}, approved=True)
    assert result["ok"] is False and result["status"] == "execution_error"


def test_missing_visible_flag_uses_owned_silent_path(monkeypatch):
    silent = Mock(return_value={"ok": True, "status": "succeeded"})
    monkeypatch.setattr(commands, "_run_silent", silent)
    monkeypatch.setattr(commands, "_run_visible", Mock(side_effect=AssertionError("visible not requested")))
    commands.cmd_control({"task": "owned fixture", "command": "fixture.exe"})
    silent.assert_called_once_with("fixture.exe")


@pytest.mark.parametrize("args", [
    {}, {"task": "unsupported fixture"},
    {"task": "owned", "command": "echo safe & fixture"},
    {"task": "owned", "command": "notepad", "visible": False},
    {"task": "open note&fixture.txt", "visible": True},
])
def test_invalid_or_implicit_visible_requests_never_spawn(monkeypatch, args):
    spawn = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(subprocess, "Popen", spawn)
    result = commands.cmd_control(args)
    assert result["ok"] is False
    spawn.assert_not_called()
