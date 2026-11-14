"""Synthetic owned workers only; no providers, microphone, media or browser."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from local_mode.owned_process import run_owned, ProcessCancelled, ProcessTimedOut
from agent import executor
from agent.task_approval import QueueApprovalRequired, ToolCancelled, ToolTimedOut

FIXTURE = Path(__file__).with_name("fixtures") / "owned_process_tree.py"
# The tree fixture imports only the standard library. Avoid three nested Windows
# venv redirectors spending the short execution deadline on interpreter startup.
TREE_PYTHON = getattr(sys, "_base_executable", None) or sys.executable


def assert_tree_dead(folder):
    pids = [int((folder / (name + ".pid")).read_text()) for name in ("root", "child", "grandchild")]
    deadline = time.monotonic() + 3
    def alive(pid):
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
    while any(alive(pid) for pid in pids):
        if time.monotonic() >= deadline:
            pytest.fail("Owned process or descendant survived cleanup")
        time.sleep(0.03)


def test_cleanup_observer_accepts_disappearance_during_status_query(tmp_path, monkeypatch):
    for name in ("root", "child", "grandchild"):
        (tmp_path / (name + ".pid")).write_text("12345")
    def disappeared(pid):
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, "Process", disappeared)
    assert_tree_dead(tmp_path)


def test_cleanup_observer_still_rejects_a_surviving_process(tmp_path, monkeypatch):
    for name in ("root", "child", "grandchild"):
        (tmp_path / (name + ".pid")).write_text("12345")
    monkeypatch.setattr(psutil, "Process", lambda _pid: SimpleNamespace(status=lambda: psutil.STATUS_RUNNING))
    clock = iter((0, 4))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    with pytest.raises(pytest.fail.Exception, match="survived cleanup"):
        assert_tree_dead(tmp_path)


@pytest.mark.parametrize("mode", ["cancel", "timeout", "success", "relay"])
def test_real_owned_process_tree_is_cleaned_on_all_exit_paths(tmp_path, mode):
    cancel = threading.Event()
    seen = []
    def event(line):
        value = json.loads(line)
        seen.append(value)
        if mode == "cancel" and value["event"] == "ready":
            cancel.set()
        if value["event"] == "text_request":
            return json.dumps({"event": "text_response", "request_id": "fixture", "response": {"text": "local fixture response"}}).encode()
    started = time.monotonic()
    kwargs = dict(payload=json.dumps({"folder": str(tmp_path), "mode": mode}).encode(),
                  timeout=1.5 if mode == "timeout" else 8, cancel=cancel, on_line=event)
    if mode in {"cancel", "timeout"}:
        with pytest.raises(ProcessCancelled if mode == "cancel" else ProcessTimedOut):
            run_owned([TREE_PYTHON, str(FIXTURE)], **kwargs)
    else:
        result = run_owned([TREE_PYTHON, str(FIXTURE)], **kwargs)
        assert result.returncode == 0
        if mode == "relay":
            assert seen[-1]["text"] == "local fixture response"
    assert time.monotonic() - started < 7
    assert_tree_dead(tmp_path)


def test_already_cancelled_owned_worker_never_launches(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    spawn = Mock()
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(ProcessCancelled):
        run_owned(["never"], payload=b"{}", timeout=1, cancel=cancel)
    spawn.assert_not_called()


def registry(risky=False, result=None):
    return SimpleNamespace(validate=Mock(), risky=Mock(return_value=risky),
                           execute=Mock(return_value=result or {"ok": True}))


@pytest.mark.parametrize("kind", ["schema", "policy", "broken_validator"])
def test_pre_dispatch_validation_never_reaches_policy_worker_or_handler(monkeypatch, kind):
    from jsonschema import ValidationError
    from agent.task_approval import ToolNotDispatched
    from local_mode.messages import OwnedText, OwnedValueError

    failure = (OwnedValueError("Expired or changed queued task approval") if kind == "policy"
               else ValidationError("fixture-secret") if kind == "schema"
               else RuntimeError("fixture-secret"))
    core = registry()
    core.validate.side_effect = failure
    worker = Mock(side_effect=AssertionError("Validation dispatched a worker"))
    monkeypatch.setattr("local_mode.owned_process.run_owned", worker)
    with pytest.raises(ToolNotDispatched) as raised:
        executor._call_tool_with_timeout("system_status", {}, None, registry=core)
    assert isinstance(raised.value.message, OwnedText)
    assert "fixture-secret" not in str(raised.value.message)
    if kind == "policy":
        assert raised.value.message.template == failure.template
    else:
        assert "handler was not run" in str(raised.value.message)
    core.risky.assert_not_called()
    core.execute.assert_not_called()
    worker.assert_not_called()


def test_reported_handler_failure_retains_output_instead_of_false_success():
    from agent.task_approval import ToolReportedFailure

    output = {"ok": False, "exit_code": 1, "stdout": "effect written", "stderr": "owned traceback"}
    core = registry(result=output)
    with pytest.raises(ToolReportedFailure) as raised:
        executor._call_tool_with_timeout("task_manager", {}, None, registry=core)
    assert raised.value.result == output and raised.value.result is not output
    core.execute.assert_called_once_with("task_manager", {}, approved=False)


def test_queued_risk_suspends_before_worker_or_host_side_effect(monkeypatch):
    parent = registry(risky=True)
    spawn = Mock()
    monkeypatch.setattr("local_mode.owned_process.run_owned", spawn)
    args = {"action": "write", "path": "fixture.txt", "content": "fixture"}
    with pytest.raises(QueueApprovalRequired) as pending:
        executor._call_tool_with_timeout("file_controller", args, None, registry=parent)
    assert pending.value.name == "file_controller"
    assert pending.value.args_snapshot == args
    parent.execute.assert_not_called()
    spawn.assert_not_called()


def test_explicit_host_dispatch_preserves_live_registry_state():
    parent = registry(result={"ok": True, "tasks": ["existing-task"]})
    result = json.loads(executor._call_tool_with_timeout("task_manager", {"action": "list"}, None, registry=parent))
    assert result["tasks"] == ["existing-task"]
    parent.execute.assert_called_once_with("task_manager", {"action": "list"}, approved=False)


@pytest.mark.parametrize("failure,expected", [(ProcessCancelled("cancelled"), ToolCancelled), (ProcessTimedOut("timeout"), ToolTimedOut)])
def test_worker_terminal_signals_are_not_success_results(monkeypatch, failure, expected):
    monkeypatch.setattr("local_mode.owned_process.run_owned", Mock(side_effect=failure))
    with pytest.raises(expected):
        executor._call_tool_with_timeout("system_status", {}, None, registry=registry())


def test_approved_worker_preserves_feedback_result_and_strips_keys(monkeypatch):
    from local_mode import model
    monkeypatch.setattr(model, "current_text_provider", lambda: lambda *args: None, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-never-sent")
    speak = Mock()
    def run(command, **kwargs):
        payload = json.loads(kwargs["payload"])
        assert payload["approved"] is True
        assert payload["relay_text"] is True
        assert kwargs["env"]["JARVIS_QUEUED_TOOL_WORKER"] == "1"
        assert "GEMINI_API_KEY" not in kwargs["env"]
        kwargs["on_line"](b'{"event":"feedback","text":"fixture feedback"}')
        kwargs["on_line"](b'{"event":"result","result":{"ok":true,"value":"fixture result"}}')
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr("local_mode.owned_process.run_owned", run)
    result = json.loads(executor._call_tool_with_timeout("file_controller", {"action": "write", "path": "fixture"}, speak,
                                                       registry=registry(risky=True), approved=True))
    assert result["value"] == "fixture result"
    speak.assert_called_once_with("fixture feedback")


def test_real_worker_reads_only_owned_file(tmp_path):
    path = tmp_path / "fixture.txt"
    path.write_text("owned-worker-fixture", encoding="utf-8")
    result = json.loads(executor._call_tool_with_timeout("file_controller", {"action": "read", "path": str(path)}, None))
    assert result["ok"] is True
    assert "owned-worker-fixture" in result["result"]

@pytest.mark.parametrize("name,args,host", [
    ("cmd_control", {"command": "echo fixture"}, True),
    ("cmd_control", {"command": "echo fixture", "visible": False}, False),
    ("cmd_control", {"command": "notepad fixture.txt", "visible": False}, True),
    ("file_controller", {"action": "undo_organize"}, True),
    ("file_controller", {"action": "read"}, False),
])
def test_persistent_launch_and_stateful_tool_isolation_boundaries(name, args, host):
    assert executor._requires_host(name, args) is host


def test_worker_output_limit_stops_owned_descendants(tmp_path):
    with pytest.raises(RuntimeError, match="output exceeds"):
        run_owned([sys.executable, str(FIXTURE)],
                  payload=json.dumps({"folder": str(tmp_path), "mode": "overflow"}).encode(),
                  timeout=8, max_output=1024)
    assert_tree_dead(tmp_path)


def test_parent_relay_failure_stops_owned_descendants(tmp_path):
    def reject(line):
        if json.loads(line)["event"] == "text_request":
            raise RuntimeError("synthetic provider relay rejected")
    with pytest.raises(RuntimeError, match="synthetic provider relay"):
        run_owned([sys.executable, str(FIXTURE)],
                  payload=json.dumps({"folder": str(tmp_path), "mode": "relay"}).encode(),
                  timeout=8, on_line=reject)
    assert_tree_dead(tmp_path)


def test_cli_worker_retains_provider_keys_and_never_uses_parent_relay(monkeypatch):
    from local_mode import model
    monkeypatch.setattr(model, "current_text_provider", lambda: None, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-cli-key")
    relay = Mock(side_effect=AssertionError("CLI must keep SDK inside owned worker"))
    monkeypatch.setattr(model, "relay_text_request", relay, raising=False)
    def run(command, **kwargs):
        assert json.loads(kwargs["payload"])["relay_text"] is False
        assert kwargs["env"]["GEMINI_API_KEY"] == "synthetic-cli-key"
        kwargs["on_line"](b'{"event":"result","result":{"ok":true}}')
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr("local_mode.owned_process.run_owned", run)
    executor._call_tool_with_timeout("system_status", {}, None, registry=registry())
    relay.assert_not_called()


def test_gui_relay_failure_propagates_without_sdk_fallback(monkeypatch):
    from local_mode import model
    monkeypatch.setattr(model, "current_text_provider", lambda: lambda *args: None, raising=False)
    relay = Mock(side_effect=RuntimeError("selected route unavailable"))
    cloud = Mock(side_effect=AssertionError("must not fallback to Google"))
    monkeypatch.setattr(model, "relay_text_request", relay, raising=False)
    monkeypatch.setattr(model, "create_client", cloud)
    def run(command, **kwargs):
        kwargs["on_line"](b'{"event":"text_request","request_id":"fixture","request":{"prompt":"synthetic"}}')
    monkeypatch.setattr("local_mode.owned_process.run_owned", run)
    with pytest.raises(RuntimeError, match="selected route unavailable"):
        executor._call_tool_with_timeout("code_helper", {"action": "explain", "code": "fixture"}, None, registry=registry())
    relay.assert_called_once()
    cloud.assert_not_called()


@pytest.mark.parametrize("relay_text,fail", [(False, False), (True, False), (True, True)])
def test_worker_binds_selected_provider_only_when_parent_requests_it(monkeypatch, relay_text, fail):
    import io
    from agent import tool_worker
    from local_mode import model, tools
    from local_mode.text_helpers import current_text_provider, using_text_provider
    monkeypatch.setattr(model, "using_text_provider", using_text_provider, raising=False)
    events = []
    monkeypatch.setattr(tool_worker, "emit", events.append)
    request = {"tool": "system_status", "args": {}, "approved": False, "relay_text": relay_text}
    monkeypatch.setattr(tool_worker.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode() + b"\n")))
    relay = Mock(side_effect=RuntimeError("selected route unavailable")) if fail else Mock(return_value={"text": "selected fixture"})
    monkeypatch.setattr(tool_worker, "request_text", relay)
    def execute(*args, **kwargs):
        provider = current_text_provider()
        if relay_text:
            assert provider is not None
            return {"ok": True, **provider({"prompt": "synthetic"})}
        assert provider is None
        return {"ok": True, "cli_binding": False}
    monkeypatch.setattr(tools, "ToolRegistry", lambda **kwargs: SimpleNamespace(execute=execute))
    tool_worker.main()
    assert current_text_provider() is None
    assert events[-1]["event"] == ("failure" if fail else "result")
    assert relay.call_count == int(relay_text)
    if relay_text and not fail:
        assert events[-1]["result"]["text"] == "selected fixture"


def test_cli_worker_cannot_force_parent_provider_execution(monkeypatch):
    from local_mode import model
    monkeypatch.setattr(model, "current_text_provider", lambda: None, raising=False)
    relay = Mock(side_effect=AssertionError("CLI must not execute parent relay"))
    monkeypatch.setattr(model, "relay_text_request", relay, raising=False)
    def run(command, **kwargs):
        kwargs["on_line"](b'{"event":"text_request","request_id":"fixture","request":{"prompt":"synthetic"}}')
    monkeypatch.setattr("local_mode.owned_process.run_owned", run)
    with pytest.raises(RuntimeError, match="CLI worker cannot request"):
        executor._call_tool_with_timeout("system_status", {}, None, registry=registry())
    relay.assert_not_called()


@pytest.mark.parametrize("cause", ["cancel", "deadline"])
def test_final_result_drain_cannot_hide_stop_after_root_exit(tmp_path, monkeypatch, cause):
    from local_mode import owned_process as owned
    real_queue = owned.queue.Queue
    real_popen = owned.subprocess.Popen
    real_clock = owned.time.monotonic
    root_exited = threading.Event()
    cancel = threading.Event()
    clock_offset = [0]

    class AfterExitQueue:
        def __init__(self, maxsize=0):
            self.inner = real_queue(maxsize=maxsize)
        def put(self, *args, **kwargs):
            return self.inner.put(*args, **kwargs)
        def get_nowait(self):
            # Force records into the final drain, after poll observed root exit.
            if not root_exited.is_set():
                raise owned.queue.Empty
            return self.inner.get_nowait()

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        original_poll = process.poll
        def poll():
            code = original_poll()
            if code is not None:
                root_exited.set()
            return code
        process.poll = poll
        return process

    def on_line(line):
        if json.loads(line)["event"] == "result":
            assert root_exited.is_set()
            if cause == "cancel":
                cancel.set()
            else:
                clock_offset[0] = 20

    monkeypatch.setattr(owned.queue, "Queue", AfterExitQueue)
    monkeypatch.setattr(owned.subprocess, "Popen", spawn)
    monkeypatch.setattr(owned.time, "monotonic", lambda: real_clock() + clock_offset[0])
    try:
        with pytest.raises(ProcessCancelled if cause == "cancel" else ProcessTimedOut):
            run_owned([sys.executable, str(FIXTURE)],
                      payload=json.dumps({"folder": str(tmp_path), "mode": "success"}).encode(),
                      timeout=8, cancel=cancel, on_line=on_line)
    finally:
        clock_offset[0] = 0
    assert_tree_dead(tmp_path)


@pytest.mark.parametrize("cause,expected", [("cancel", ToolCancelled), ("deadline", ToolTimedOut)])
def test_executor_worker_return_is_rechecked_before_success(monkeypatch, cause, expected):
    from local_mode import model
    monkeypatch.setattr(model, "current_text_provider", lambda: None, raising=False)
    real_clock = time.monotonic
    clock_offset = [0]
    cancel = threading.Event()
    def run(command, **kwargs):
        kwargs["on_line"](b'{"event":"result","result":{"ok":true}}')
        if cause == "cancel":
            cancel.set()
        else:
            clock_offset[0] = 1000
        return subprocess.CompletedProcess(command, 0, b"", b"")
    monkeypatch.setattr("local_mode.owned_process.run_owned", run)
    monkeypatch.setattr(time, "monotonic", lambda: real_clock() + clock_offset[0])
    try:
        with pytest.raises(expected):
            executor._call_tool_with_timeout("system_status", {}, None, registry=registry(), cancel_flag=cancel)
    finally:
        clock_offset[0] = 0


@pytest.mark.parametrize("cause,expected", [("cancel", ToolCancelled), ("deadline", ToolTimedOut)])
def test_provider_relay_exception_keeps_terminal_stop_type(monkeypatch, cause, expected):
    from local_mode import model
    monkeypatch.setattr(model, "current_text_provider", lambda: lambda *args: None, raising=False)
    cancel = threading.Event()
    real_clock = time.monotonic
    offset = [0]
    def relay(*args, **kwargs):
        if cause == "cancel":
            cancel.set()
        else:
            offset[0] = 1000
        raise RuntimeError("provider observed terminal stop")
    def run(command, **kwargs):
        kwargs["on_line"](b'{"event":"text_request","request_id":"fixture","request":{"prompt":"synthetic"}}')
    monkeypatch.setattr(model, "relay_text_request", relay, raising=False)
    monkeypatch.setattr("local_mode.owned_process.run_owned", run)
    monkeypatch.setattr(time, "monotonic", lambda: real_clock() + offset[0])
    try:
        with pytest.raises(expected):
            executor._call_tool_with_timeout("code_helper", {"action": "explain", "code": "fixture"}, None,
                                             registry=registry(), cancel_flag=cancel)
    finally:
        offset[0] = 0
