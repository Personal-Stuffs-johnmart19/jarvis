"""Exercise the real stdio dispatcher with only an inert fake desktop runtime."""
import json
import os
import sys
import threading
import types

# Parent assigns our Job before granting this first input line.
json.loads(sys.stdin.buffer.readline())
finished = threading.Event()
real_input = sys.stdin.buffer
camera_calls = []


class FixtureInput:
    def readline(self, size=-1):
        return b"" if finished.is_set() else real_input.readline(size)


sys.stdin = types.SimpleNamespace(buffer=FixtureInput())
sys.modules["local_mode.camera_devices"] = types.SimpleNamespace(select_camera=lambda value: camera_calls.append(value))
from local_mode import desktop_host


class FakeRuntime:
    interface_locale = "en"

    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.Lock()
        self.active = 0
        self.general_release = threading.Event()
        self.model_release = threading.Event()

    def start(self):
        self.emit("fixture.ready", {"pid": os.getpid()})

    def snapshot(self, method):
        with self.lock:
            return {"method": method, "active_general": self.active,
                    "before_general_release": not self.general_release.is_set(),
                    "thread": threading.current_thread().name}

    def dispatch(self, method, params):
        if method == "fixture.block":
            with self.lock:
                self.active += 1
                saturated = self.active == 4
            if saturated:
                self.emit("fixture.saturated", {"active": 4})
            try:
                if not self.general_release.wait(5):
                    raise RuntimeError("Fixture general workers were never released")
                return {"released": True, "slot": params["slot"]}
            finally:
                with self.lock:
                    self.active -= 1
        if method == "control":
            action = params["action"]
            if action == "release_general":
                self.general_release.set()
            elif action == "release_model":
                self.model_release.set()
            elif action == "finish":
                finished.set()
            return self.snapshot(method)
        if method == "managed_chat.resolve" and params.get("fixture_block"):
            self.emit("fixture.model_blocked", {})
            if not self.model_release.wait(5):
                raise RuntimeError("Fixture model worker was never released")
        if method in {"helper.text.complete", "workbench.bootstrap", "managed_chat.list", "managed_chat.resolve"}:
            return self.snapshot(method)
        raise AssertionError("Unexpected fixture RPC method: " + method)

    def close(self):
        self.general_release.set()
        self.model_release.set()
        self.emit("fixture.closed", {"active_general": self.active, "camera_calls": camera_calls})


desktop_host.DesktopRuntime = FakeRuntime
raise SystemExit(desktop_host._serve())
