"""Actual private queue consumers must cross both packaged bridge boundaries."""
import io
import json
from pathlib import Path
import re
from types import SimpleNamespace

from local_mode import desktop_host
from local_mode.services import ServiceRegistry

ROOT = Path(__file__).resolve().parents[2]


def test_actual_queued_consumers_match_exact_native_and_python_admission():
    source = (ROOT / "apps/desktop/workbench/src/modules/ai/components/QueuedTaskApprovals.tsx").read_text(encoding="utf-8")
    consumers = set(re.findall(r'method:\s*"(tasks\.ui\.[^"]+)"', source))
    bridge = (ROOT / "apps/desktop/src-tauri/src/bridge.rs").read_text(encoding="utf-8")
    allowlist = re.search(r'const QUEUED_UI_METHODS:.*?=\s*&\[(.*?)\];', bridge, re.S).group(1)
    native = set(re.findall(r'"([^"]+)"', allowlist))
    assert consumers == native == desktop_host.QUEUED_UI_METHODS
    assert consumers <= desktop_host.PRIORITY_METHODS
    for unknown in ("tasks.ui.unknown", "tasks.ui.start.extra", "tasks.ui.Start", "tasks.ui.read", "tasks.ui.cancel"):
        assert unknown not in native
        assert unknown not in desktop_host.PRIORITY_METHODS
    registry = ServiceRegistry(SimpleNamespace(), lambda: None)
    assert not any(item["name"].startswith("tasks.ui.") for item in registry.descriptors)


def test_queue_control_lane_runs_when_ordinary_admission_is_saturated(monkeypatch):
    dispatched, submitted = [], []

    class SaturatedSlots:
        def __init__(self, *_args):
            pass

        def acquire(self, **_kwargs):
            return False

    class Pool:
        def __init__(self, **_kwargs):
            pass

        def submit(self, *_args):
            submitted.append(True)

        def shutdown(self, **_kwargs):
            pass

    runtime = SimpleNamespace(start=lambda: None, close=lambda: None, interface_locale="en",
        dispatch=lambda method, params: dispatched.append(method) or {"ok": True})
    monkeypatch.setattr(desktop_host, "DesktopRuntime", lambda *_args: runtime)
    monkeypatch.setattr(desktop_host, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(desktop_host.threading, "BoundedSemaphore", SaturatedSlots)
    from local_mode import camera_devices
    monkeypatch.setattr(camera_devices, "select_camera", lambda *_args: None)
    methods = sorted(desktop_host.QUEUED_UI_METHODS) + ["services.read", "tasks.ui.unknown"]
    requests = b"".join((json.dumps({"version": 1, "id": index, "method": method, "params": {}}) + "\n").encode()
        for index, method in enumerate(methods))
    output = io.StringIO()
    monkeypatch.setattr(desktop_host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(requests)))
    monkeypatch.setattr(desktop_host.sys, "stdout", output)
    assert desktop_host._serve() == 0
    assert dispatched == sorted(desktop_host.QUEUED_UI_METHODS)
    assert submitted == []
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert all(reply.get("result") == {"ok": True} for reply in replies[:-2])
    assert all("error" in reply for reply in replies[-2:])
