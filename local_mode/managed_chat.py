"""Independent, lazily loaded managed llama.cpp runtimes. No catalogue-time loads."""
from __future__ import annotations

import atexit
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import os
import re
import socket
import threading

from local_mode.config import ROOT
from local_mode.local_chat import LocalChat


def model_identity(kind, repo_id, revision):
    identity = f"jarvis-managed:{kind}:{repo_id}@{revision}"
    _parse(identity)
    return identity


def _parse(identity):
    if not isinstance(identity, str) or len(identity) > 400:
        raise ValueError("Invalid managed model identity")
    match = re.fullmatch(r"jarvis-managed:(llm|vision):([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-fA-F]{40}|[0-9a-fA-F]{64})", identity)
    if not match:
        raise ValueError("Select a pinned managed chat model")
    if any(part in {".", ".."} or len(part) > 96 for part in match.group(2).split("/")):
        raise ValueError("Invalid managed model repository")
    return match.groups()


def _request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Invalid managed model request ID")
    return value


class _Cancellation:
    def __init__(self, *events):
        self.events = [event for event in events if event is not None]

    def is_set(self):
        return any(event.is_set() for event in self.events)

    def check(self):
        if self.is_set():
            raise RuntimeError("Managed model load cancelled")


@contextmanager
def _acquire(lock, cancellation):
    while not lock.acquire(timeout=.05):
        cancellation.check()
    try:
        cancellation.check()
        yield
    finally:
        lock.release()


class _Entry:
    def __init__(self, runner):
        self.runner = runner
        self.lock = threading.Lock()
        self.unloaded = threading.Event()
        self.selection = None


class ManagedChat:
    def __init__(self, runner_factory=LocalChat, settings_path=None):
        self.lock = threading.RLock()
        self.entries = {}
        self.requests = {}
        self.early_cancels = OrderedDict()
        self.closed = False
        self.warmups = threading.BoundedSemaphore(4)
        self.runner_factory = runner_factory
        self.settings_path = settings_path or ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data") / "managed-chat.json"
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf8"))
            self.modes = settings if isinstance(settings, dict) else {}
        except (OSError, ValueError):
            self.modes = {}

    def configure(self, model_id, mode="vulkan"):
        _parse(model_id)
        if mode not in {"vulkan", "cpu"}:
            raise ValueError("Choose Vulkan or CPU")
        with self.lock:
            if self.closed:
                raise RuntimeError("Managed chat is shut down")
            old = self.modes.get(model_id, "vulkan")
            modes = {**self.modes, model_id: mode}
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(modes), encoding="utf8")
            temporary.replace(self.settings_path)
            self.modes = modes
        if old != mode:
            self.unload(model_id)
        with self.lock:
            return {**self._record(model_id, self.entries.get(model_id)), "configured": True}

    def _record(self, model_id, entry=None):
        kind, repo, revision = _parse(model_id)
        status = entry.runner.status() if entry else {}
        loaded = bool(status.get("running"))
        return {"model_id": model_id, "base_url": status.get("baseURL") if loaded else None,
                "context": status.get("context", 8192), "loaded": loaded,
                "status": "loaded" if loaded else "unloaded", "kind": kind,
                "repo_id": repo, "revision": revision, "name": f"{repo} ({revision[:8]})",
                "capabilities": {"vision": kind == "vision"},
                "mode": self.modes.get(model_id, "vulkan"), "device": status.get("device", "")}

    def list_models(self):
        from local_mode import model_library as library
        result = []
        for item in library.list_installed_models():
            kind = item.get("kind")
            if (kind, item.get("bundle_id")) not in {("llm", "llm-gguf"), ("vision", "vision-gguf-mmproj")}:
                continue
            try:
                identity = model_identity(kind, item["repo_id"], item["revision"])
                library.verify_and_get_model_assets(kind, item["repo_id"], item["revision"], verify_checksums=False)
                with self.lock:
                    result.append(self._record(identity, self.entries.get(identity)))
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda item: item["model_id"])

    def _entry(self, identity):
        with self.lock:
            if self.closed:
                raise RuntimeError("Managed chat is shut down")
            if identity not in self.entries:
                if len(self.entries) >= 64:
                    raise RuntimeError("Managed model runtime limit reached; unload another model")
                ports = {entry.runner.port for entry in self.entries.values()}
                for _ in range(128):
                    with socket.socket() as probe:
                        probe.bind(("127.0.0.1", 0))
                        port = probe.getsockname()[1]
                    if port not in ports:
                        break
                else:
                    raise RuntimeError("Cannot allocate an independent managed model port")
                self.entries[identity] = _Entry(self.runner_factory(port=port, alias=identity, persist=False))
            return self.entries[identity]

    def _verify(self, identity, cancellation):
        from local_mode import model_library as library
        kind, repo, revision = _parse(identity)
        manifest, directory, derived = library.verify_and_get_model_assets(kind, repo, revision, verify_checksums=False)
        selection = {**derived, "repo_id": repo, "revision": revision, "kind": kind}
        for record in manifest["files"]:
            cancellation.check()
            path = library.ensure_safe_path(directory, record["path"])
            before = path.lstat()
            identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            digest = hashlib.sha256()
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as stream:
                if identity(os.fstat(stream.fileno())) != identity(before):
                    raise ValueError("Managed model file changed before verification")
                size = 0
                while chunk := stream.read(1024 * 1024):
                    cancellation.check()
                    size += len(chunk)
                    if size > record["size"]:
                        raise ValueError("Managed model exceeds published size")
                    digest.update(chunk)
                after = os.fstat(stream.fileno())
            if (identity(before) != identity(after) or identity(path.lstat()) != identity(after)
                    or after.st_size != record["size"] or digest.hexdigest() != record["sha256"].lower()):
                raise ValueError("Managed model weights changed or checksum mismatch")
            if str(path) in derived.get("shard_paths", []):
                selection.setdefault("shards", []).append({"path": str(path), "size": after.st_size,
                    "mtime_ns": after.st_mtime_ns, "sha256": record["sha256"]})
            for field, prefix in (("model_path", "model"), ("projector_path", "projector")):
                if derived[field] == str(path):
                    selection[prefix + "_size"] = after.st_size
                    selection[prefix + "_mtime_ns"] = after.st_mtime_ns
                    if prefix == "model":
                        selection["sha256"] = record["sha256"]
        if "shards" in selection:
            library.validate_shard_metadata(derived["shard_paths"])
            order = derived["shard_paths"]
            selection["shards"].sort(key=lambda item: order.index(item["path"]))
        library.validate_selected_model(selection, kind)
        cancellation.check()
        return selection

    def resolve(self, model_id, request_id=None, cancel_event=None):
        from local_mode import model_library as library
        _parse(model_id)
        local_cancel = threading.Event()
        if request_id is not None:
            _request_id(request_id)
            with self.lock:
                if self.closed:
                    raise RuntimeError("Managed chat is shut down")
                if request_id in self.requests or len(self.requests) >= 32:
                    raise RuntimeError("Managed model request busy or duplicate ID")
                if request_id in self.early_cancels:
                    self.early_cancels.pop(request_id)
                    local_cancel.set()
                self.requests[request_id] = local_cancel
        entry = None
        try:
            cancellation = _Cancellation(local_cancel, cancel_event)
            cancellation.check()
            entry = self._entry(model_id)
            cancellation = _Cancellation(local_cancel, cancel_event, entry.unloaded)
            with _acquire(entry.lock, cancellation), _acquire(self.warmups, cancellation):
                was_running = bool(entry.runner.status().get("running"))
                try:
                    if entry.selection is None or not was_running:
                        entry.selection = self._verify(model_id, cancellation)
                    else:
                        library.validate_selected_model(entry.selection, entry.selection["kind"])
                    cancellation.check()
                    mode = self.modes.get(model_id, "vulkan")
                    entry.runner.start(entry.selection["model_path"], entry.selection["projector_path"], mode,
                                       cancel_event=cancellation, selection=entry.selection)
                    cancellation.check()
                    with self.lock:
                        if self.entries.get(model_id) is not entry:
                            raise RuntimeError("Managed model was unloaded")
                        return self._record(model_id, entry)
                except BaseException:
                    if not was_running:
                        entry.runner.stop()
                        entry.selection = None
                    raise
        finally:
            if request_id is not None:
                with self.lock:
                    self.requests.pop(request_id, None)

    def cancel(self, request_id):
        _request_id(request_id)
        with self.lock:
            if self.closed:
                return {"cancelled": True}
            event = self.requests.get(request_id)
            if event is not None:
                event.set()
            else:
                if len(self.early_cancels) >= 256:
                    self.early_cancels.popitem(last=False)
                self.early_cancels[request_id] = True
        return {"cancelled": True}

    def unload(self, model_id):
        _parse(model_id)
        with self.lock:
            entry = self.entries.pop(model_id, None)
            if entry:
                entry.unloaded.set()
        if entry:
            entry.runner.stop()
        return self._record(model_id)

    def stop(self):
        with self.lock:
            self.closed = True
            entries = list(self.entries.values())
            self.entries.clear()
            for event in self.requests.values():
                event.set()
            for entry in entries:
                entry.unloaded.set()
        for entry in entries:
            entry.runner.stop()


manager = ManagedChat()
atexit.register(manager.stop)
