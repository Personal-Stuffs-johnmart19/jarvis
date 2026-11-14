"""Shared runtime correlation and approval receipts, without audio or a browser."""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import pytest
from local_mode.shared_agent import SharedAgent


def test_shared_run_correlates_response_and_rejects_stale_completion():
    event = threading.Event()
    requests = []

    def emit(name, data):
        if name == "agent.request":
            requests.append(data)
            event.set()

    bridge = SharedAgent(emit)
    bridge.attach()
    with ThreadPoolExecutor() as pool:
        result = pool.submit(bridge.run, "hello", threading.Event())
        assert event.wait(2)
        with pytest.raises(ValueError, match="Expired"):
            bridge.complete("wrong", "bad")
        bridge.complete(requests[0]["id"], "answer")
        assert result.result(timeout=2) == {"ok": True, "reply": "answer"}
    with pytest.raises(ValueError, match="Expired"):
        bridge.complete(requests[0]["id"], "duplicate")


def test_receipt_binds_validated_arguments_and_is_single_use():
    bridge = SharedAgent(lambda *_: None)
    registry = Mock()
    registry.risky.return_value = True
    args = {"path": "fixture.txt"}
    receipt = bridge.prepare(registry, "read_file", args)
    args["path"] = "changed"
    assert receipt["requires_approval"] is True
    registry.validate.assert_called_once()
    bridge.execute(registry, receipt["token"])
    registry.execute.assert_called_once_with(
        "read_file", {"path": "fixture.txt"}, approved=True
    )
    with pytest.raises(ValueError, match="consumed"):
        bridge.execute(registry, receipt["token"])


def test_detach_revokes_receipts_and_pending_request():
    event = threading.Event()
    bridge = SharedAgent(
        lambda name, _: event.set() if name == "agent.request" else None
    )
    registry = Mock()
    receipt = bridge.prepare(registry, "test", {})
    with ThreadPoolExecutor() as pool:
        result = pool.submit(bridge.run, "hello", threading.Event())
        assert event.wait(2)
        bridge.detach()
        with pytest.raises(ValueError, match="disconnected"):
            result.result(timeout=2)
    with pytest.raises(ValueError):
        bridge.execute(registry, receipt["token"])
    registry.execute.assert_not_called()


def test_changed_risk_requires_new_approval():
    bridge = SharedAgent(lambda *_: None)
    registry = Mock()
    registry.risky.side_effect = [False, True]
    receipt = bridge.prepare(registry, "game_updater", {})
    with pytest.raises(ValueError, match="risk changed"):
        bridge.execute(registry, receipt["token"])
    registry.execute.assert_not_called()


def test_empty_agent_response_does_not_claim_success():
    event = threading.Event()
    requests = []
    def emit(name, data):
        if name == "agent.request":
            requests.append(data)
            event.set()
    bridge = SharedAgent(emit)
    with ThreadPoolExecutor() as pool:
        result = pool.submit(bridge.run, "hello", threading.Event())
        assert event.wait(2)
        bridge.complete(requests[0]["id"], "")
        with pytest.raises(ValueError, match="without a text response"):
            result.result(timeout=2)


def test_owned_action_cancel_context_defaults_explicit_precedence_and_reset(monkeypatch):
    from local_mode import owned_process

    cancelled = threading.Event()
    cancelled.set()
    spawn = Mock(side_effect=RuntimeError("owned fixture would launch"))
    monkeypatch.setattr(owned_process.subprocess, "Popen", spawn)
    with owned_process.owned_action_cancel(cancelled):
        with pytest.raises(owned_process.ProcessCancelled):
            owned_process.run_owned(["owned"], payload=b"{}", timeout=1)
        with pytest.raises(RuntimeError, match="would launch"):
            owned_process.run_owned(["owned"], payload=b"{}", timeout=1, cancel=threading.Event())
        with owned_process.owned_action_cancel(threading.Event()):
            with pytest.raises(RuntimeError, match="would launch"):
                owned_process.run_owned(["owned"], payload=b"{}", timeout=1)
        with pytest.raises(owned_process.ProcessCancelled):
            owned_process.run_owned(["owned"], payload=b"{}", timeout=1)
    with pytest.raises(RuntimeError, match="would launch"):
        owned_process.run_owned(["owned"], payload=b"{}", timeout=1)


@pytest.mark.parametrize("change", ["stop", "detach", "owner", "session", "foreign_detach"])
def test_shared_owned_call_stop_or_identity_change_retains_racing_outcome(change):
    entered, release = threading.Event(), threading.Event()
    bridge = SharedAgent(lambda *_: None)
    bridge.attach("model", "session", "owner")
    actual = {"ok": True, "result": "owned effect completed"}
    def execute(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return actual
    registry = Mock()
    registry.risky.return_value = True
    registry.execute.side_effect = execute
    receipt = bridge.prepare(registry, "owned", {})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.execute, registry, receipt["token"])
        try:
            assert entered.wait(2)
            if change == "stop": bridge.cancel_current()
            elif change == "detach": bridge.detach("owner")
            elif change == "owner": bridge.attach("model", "session", "other")
            elif change == "session": bridge.attach("model", "other", "owner")
            else: assert bridge.detach("unrelated") is False
        finally:
            release.set()
        result = future.result(timeout=2)
    if change == "foreign_detach":
        assert result is actual
    else:
        assert result["ok"] is False and result["cancelled"] is True
        assert result["result"] is actual
    with pytest.raises(ValueError, match="consumed"):
        bridge.execute(registry, receipt["token"])
    bridge.attach("model", "session", "owner")
    fresh = bridge.prepare(registry, "owned", {})
    assert bridge.execute(registry, fresh["token"]) is actual


def test_shared_owned_call_keeps_handler_cancelled_result():
    bridge = SharedAgent(lambda *_: None)
    result = {"ok": False, "cancelled": True, "status": "cancelled", "output_complete": False}
    registry = Mock()
    registry.risky.return_value = True
    def execute(*args, **kwargs):
        bridge.cancel_current()
        return result
    registry.execute.side_effect = execute
    receipt = bridge.prepare(registry, "owned", {})
    assert bridge.execute(registry, receipt["token"]) is result


def test_direct_shared_code_helper_stop_reaps_owned_root_child_and_grandchild(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    import time
    import psutil
    from actions import code_helper
    from local_mode import owned_process
    from local_mode.tools import ToolRegistry

    model = Mock(side_effect=AssertionError("Direct run must not call a model"))
    monkeypatch.setattr(code_helper, "_get_gemini", model)
    script = tmp_path / "owned_tree.py"
    script.write_text('''import os, subprocess, sys, time
from pathlib import Path
folder = Path(__file__).parent
mode = sys.argv[1] if len(sys.argv) > 1 else 'root'
(folder / (mode + '.pid')).write_text(str(os.getpid()))
if mode != 'grandchild':
    child = 'child' if mode == 'root' else 'grandchild'
    subprocess.Popen([getattr(sys, "_base_executable", None) or sys.executable, __file__, child], creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
if mode == 'root':
    deadline = time.monotonic() + 10
    while not (folder / 'grandchild.pid').exists():
        if time.monotonic() > deadline: raise RuntimeError('Owned descendants did not start')
        time.sleep(0.02)
    (folder / 'ready').write_text('owned tree ready')
time.sleep(30)
(folder / (mode + '.late')).write_text('must never survive Stop')
''', encoding="utf-8")
    spawned = []
    original_spawn = owned_process.subprocess.Popen
    def observe_spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        spawned.append(psutil.Process(process.pid))
        return process
    monkeypatch.setattr(owned_process.subprocess, "Popen", observe_spawn)
    bridge = SharedAgent(lambda *_: None)
    bridge.attach("owned-model", "session", "owner")
    registry = ToolRegistry()
    receipt = bridge.prepare(registry, "code_helper", {"action": "run", "file_path": str(script), "args": [], "timeout": 20})
    assert receipt["requires_approval"] is True and not (tmp_path / "root.pid").exists()
    tree = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(bridge.execute, registry, receipt["token"])
        try:
            deadline = time.monotonic() + 12
            while not (tmp_path / "ready").exists():
                assert time.monotonic() < deadline, "Owned tree never became ready"
                assert not future.done(), "Canonical run ended before owned descendants were ready"
                time.sleep(0.02)
            tree = [psutil.Process(int((tmp_path / (name + ".pid")).read_text())) for name in ("root", "child", "grandchild")]
            assert tree[1].ppid() == tree[0].pid and tree[2].ppid() == tree[1].pid
            assert any(parent.pid == os.getpid() for parent in tree[0].parents())
            # Include gate/venv redirectors as well as the three fixture roles.
            observed = {process.pid: process for process in [*spawned, *tree, *spawned[0].children(recursive=True)]}
            # Capture creation identities before Stop; pid reuse cannot count as a survivor.
            for process in observed.values(): process.create_time()
            bridge.cancel_current()
            result = future.result(timeout=5)
        finally:
            bridge.cancel_current()
    assert result["ok"] is False and result["cancelled"] is True and result["status"] == "cancelled"
    assert result["exit_code"] is None and result["output_complete"] is False
    _, alive = psutil.wait_procs(list(observed.values()), timeout=3)
    assert not alive, "Owned Python process or descendant survived direct Stop"
    assert not list(tmp_path.glob("*.late"))
    with pytest.raises(ValueError, match="consumed"):
        bridge.execute(registry, receipt["token"])
    model.assert_not_called()

def test_shared_owned_call_exception_cleans_scope_and_active_event(monkeypatch):
    from local_mode import owned_process

    bridge = SharedAgent(lambda *_: None)
    registry = Mock()
    registry.risky.return_value = True
    def execute(*args, **kwargs):
        bridge.cancel_current()
        raise RuntimeError("owned handler failure")
    registry.execute.side_effect = execute
    receipt = bridge.prepare(registry, "owned", {})
    with pytest.raises(RuntimeError, match="owned handler failure"):
        bridge.execute(registry, receipt["token"])
    assert not bridge._owned_calls
    monkeypatch.setattr(owned_process.subprocess, "Popen", Mock(side_effect=RuntimeError("owned fixture would launch")))
    with pytest.raises(RuntimeError, match="would launch"):
        owned_process.run_owned(["owned"], payload=b"{}", timeout=1)