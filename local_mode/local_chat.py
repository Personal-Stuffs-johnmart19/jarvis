"""Managed loopback llama.cpp chat server, sharing Jarvis's provider/tool pipeline."""
from __future__ import annotations
import atexit
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import requests
from local_mode.config import ROOT
from local_mode.messages import OwnedRuntimeError

class LocalChat:
    def __init__(self, *, port=18081, alias="jarvis-local", persist=True):
        self.lock = threading.RLock()
        self.port, self.alias, self.persist = port, alias, persist
        self.cancel_load = threading.Event()
        self.process = None
        self.log = None
        self.device = ""
        self.model = ""
        self.projector = ""
        self.mode = "vulkan"
        try:
            saved = json.loads(self.settings_path().read_text(encoding="utf8")) if persist else {}
            self.model = saved.get("model", "")
            self.projector = saved.get("projector", "")
            self.mode = saved.get("mode", "vulkan")
        except (OSError, ValueError, AttributeError):
            pass

    @staticmethod
    def settings_path():
        return ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data") / "llama-chat.json"

    def status(self):
        process = self.process
        return {"running": process is not None and process.poll() is None,
                "device": self.device, "model": self.model, "projector": self.projector, "mode": self.mode,
                "baseURL": f"http://127.0.0.1:{self.port}/v1", "context": 8192}

    def stop(self):
        # Wake a lazy load before waiting for its lifecycle lock.
        self.cancel_load.set()
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
            if self.log:
                self.log.close()
                self.log = None
            return self.status()

    def start(self, model: str, projector: str = "", mode: str = "vulkan", cancel_event=None, selection=None):
        def cancelled():
            return self.cancel_load.is_set() or (cancel_event is not None and cancel_event.is_set())
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Managed model load cancelled")
        if mode not in {"vulkan", "cpu"}:
            raise ValueError("Choose Vulkan or CPU")
        managed_settings = {}
        try:
            saved = json.loads(self.settings_path().read_text(encoding="utf8")) if self.persist else {}
        except (OSError, ValueError):
            saved = {}
        if isinstance(saved, dict) and saved.get("kind") in {"llm", "vision"} and saved.get("model") == model:
            from local_mode.model_library import validate_selected_model
            validate_selected_model({**saved, "model_path": model, "projector_path": projector}, saved["kind"])
            managed_settings = saved
        paths = []
        for value in [model, projector]:
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError("Invalid GGUF path")
            if value:
                path = Path(value).resolve(strict=True)
                if not path.is_file() or path.suffix.lower() != ".gguf":
                    raise ValueError("Select a GGUF file")
                paths.append(path)
        if not model:
            raise ValueError("Select a chat model")
        # Reuse the pinned, checksum-verified runtime installed for embeddings.
        from local_mode.local_embeddings import ensure_runtime
        executable = ensure_runtime(cancel_event=cancel_event) if selection is not None else ensure_runtime()
        with self.lock:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Managed model load cancelled")
            if self.status()["running"] and self.model == str(paths[0]) and self.projector == projector and self.mode == mode:
                return self.status()
            self.stop()
            self.cancel_load.clear()
            folder = ROOT / "models" / "llama-chat"
            folder.mkdir(parents=True, exist_ok=True)
            session = requests.Session()
            session.trust_env = False
            try:
                session.get(f"http://127.0.0.1:{self.port}/health", timeout=1)
            except requests.ConnectionError:
                pass
            else:
                raise RuntimeError(f"Port {self.port} is occupied; no existing server was stopped")
            try:
                for device in (["Vulkan0"] if mode == "vulkan" else ["none"]):
                    if cancelled():
                        raise RuntimeError("Managed model load cancelled")
                    if selection is not None:
                        from local_mode.model_library import validate_selected_model
                        validate_selected_model(selection, selection["kind"])
                    self.log = (folder / ("server.log" if self.persist else f"server-{self.port}.log")).open("ab")
                    args = [str(executable), "--model", str(paths[0]), "--alias", self.alias,
                            "--jinja", "--ctx-size", "8192", "--parallel", "1",
                            "--host", "127.0.0.1", "--port", str(self.port), "--no-webui",
                            "--device", device, "--n-gpu-layers", "99" if device != "none" else "0"]
                    if len(paths) == 2:
                        args += ["--mmproj", str(paths[1])]
                    if device == "none":
                        args += ["--no-kv-offload", "--no-op-offload", "--no-mmproj-offload"]
                    self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=self.log,
                        stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    deadline = time.monotonic() + 120
                    while self.process.poll() is None and time.monotonic() < deadline:
                        if cancelled():
                            self.stop()
                            raise RuntimeError("Managed model load cancelled")
                        try:
                            if session.get(f"http://127.0.0.1:{self.port}/health", timeout=1).status_code == 200:
                                if cancelled():
                                    self.stop()
                                    raise RuntimeError("Managed model load cancelled")
                                self.device, self.model = device, str(paths[0])
                                self.projector, self.mode = projector, mode
                                if self.persist:
                                    target = self.settings_path()
                                    target.parent.mkdir(parents=True, exist_ok=True)
                                    temporary = target.with_suffix(".tmp")
                                    temporary.write_text(json.dumps({**managed_settings,"model":self.model,"projector":projector,"mode":mode}), encoding="utf8")
                                    temporary.replace(target)
                                return self.status()
                        except requests.RequestException:
                            pass
                        time.sleep(.25)
                    exit_code = self.process.poll()
                    failure = f"exit code {exit_code}" if exit_code is not None else "startup timed out"
                    was_cancelled = cancelled()
                    self.stop()
                    if was_cancelled:
                        raise RuntimeError("Managed model load cancelled")
                    self.cancel_load.clear()
                raise OwnedRuntimeError("Local {0} chat failed to start ({1}); see models/llama-chat/server.log. Select CPU explicitly to use CPU chat.", mode, failure)
            except Exception:
                self.stop()
                raise
            finally:
                session.close()

runner = LocalChat()
atexit.register(runner.stop)
