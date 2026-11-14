"""Offline independent managed-model identities, lifecycle and cancellation."""
import concurrent.futures
import hashlib
import json
import os
import struct
import threading
import time

import pytest

from local_mode import managed_chat as chat, model_library as library


class FakeRunner:
    instances = []
    hook = None

    def __init__(self, *, port, alias, persist):
        self.port, self.alias = port, alias
        self.running = False
        self.starts = []
        self.started = threading.Event()
        self.gate = None
        self.stops = 0
        self.instances.append(self)

    def status(self):
        return {"running": self.running, "baseURL": f"http://127.0.0.1:{self.port}/v1", "context": 8192}

    def start(self, model, projector, mode, *, cancel_event, selection):
        if self.running:
            return self.status()
        self.started.set()
        if self.hook:
            type(self).hook(self)
        while self.gate is not None and not self.gate.wait(.01):
            if cancel_event.is_set():
                raise RuntimeError("Managed model load cancelled")
        if cancel_event.is_set():
            raise RuntimeError("Managed model load cancelled")
        self.starts.append((model, projector, mode))
        self.running = True
        return self.status()

    def stop(self):
        self.stops += 1
        self.running = False


def install(root, repo="test/one", revision="a" * 40):
    folder = root / "llm" / repo.replace("/", "__") / revision
    folder.mkdir(parents=True)
    data = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + b"\0" * 64
    weight = folder / "model.gguf"
    weight.write_bytes(data)
    manifest = {"owner": library.OWNER, "complete": True, "repo_id": repo,
                "revision": revision, "kind": "llm", "bundle_id": "llm-gguf",
                "files": [{"path": weight.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    return chat.model_identity("llm", repo, revision), weight


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "models"
    root.mkdir()
    monkeypatch.setattr(library, "get_models_root", lambda: root)
    monkeypatch.setattr(FakeRunner, "instances", [])
    monkeypatch.setattr(FakeRunner, "hook", None)
    manager = chat.ManagedChat(FakeRunner, tmp_path / "settings.json")
    yield manager, root
    manager.stop()


def wait_for(check):
    deadline = time.monotonic() + 2
    while not check():
        assert time.monotonic() < deadline
        time.sleep(.005)


def test_metadata_lists_distinct_unloaded_identities_without_launch(env):
    manager, root = env
    first, _ = install(root)
    second, _ = install(root, "test/two", "b" * 40)
    records = manager.list_models()
    assert {item["model_id"] for item in records} == {first, second}
    assert all(not item["loaded"] for item in records)
    assert all(item["base_url"] is None for item in records)
    assert FakeRunner.instances == []


def test_two_models_load_concurrently_and_never_replace_each_other(env, monkeypatch):
    manager, root = env
    first, _ = install(root)
    second, _ = install(root, "test/two", "b" * 40)
    barrier = threading.Barrier(2)
    monkeypatch.setattr(FakeRunner, "hook", lambda self: barrier.wait(timeout=2))
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(manager.resolve, identity) for identity in (first, second)]
        records = [job.result(timeout=3) for job in jobs]
    assert records[0]["base_url"] != records[1]["base_url"]
    assert {runner.alias for runner in FakeRunner.instances} == {first, second}
    assert all(runner.running for runner in FakeRunner.instances)
    manager.unload(first)
    assert next(runner for runner in FakeRunner.instances if runner.alias == second).running
    assert manager.resolve(second)["model_id"] == second


def test_cancel_before_registration_never_creates_runtime(env):
    manager, root = env
    identity, _ = install(root)
    manager.cancel("early-request")
    with pytest.raises(RuntimeError, match="cancelled"):
        manager.resolve(identity, request_id="early-request")
    assert not FakeRunner.instances
    assert not manager.requests


def test_cancel_waiting_same_model_request_does_not_cancel_loading_request(env):
    manager, root = env
    identity, _ = install(root)
    entry = manager._entry(identity)
    entry.runner.gate = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(manager.resolve, identity, request_id="first")
        assert entry.runner.started.wait(2)
        second = pool.submit(manager.resolve, identity, request_id="second")
        wait_for(lambda: "second" in manager.requests)
        manager.cancel("second")
        with pytest.raises(RuntimeError, match="cancelled"):
            second.result(timeout=2)
        assert not first.done()
        entry.runner.gate.set()
        assert first.result(timeout=2)["loaded"]
    assert entry.runner.stops == 0


def test_cancel_loading_model_preserves_other_model_and_can_retry(env):
    manager, root = env
    first, _ = install(root)
    second, _ = install(root, "test/two", "b" * 40)
    entry = manager._entry(first)
    entry.runner.gate = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        loading = pool.submit(manager.resolve, first, request_id="first")
        assert entry.runner.started.wait(2)
        assert manager.resolve(second)["loaded"]
        manager.cancel("first")
        with pytest.raises(RuntimeError, match="cancelled"):
            loading.result(timeout=2)
    assert not entry.runner.running
    assert manager.resolve(second)["loaded"]
    entry.runner.gate.set()
    assert manager.resolve(first)["loaded"]


def test_unload_cancels_warmup_and_does_not_publish_stale_endpoint(env):
    manager, root = env
    identity, _ = install(root)
    entry = manager._entry(identity)
    entry.runner.gate = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        loading = pool.submit(manager.resolve, identity)
        assert entry.runner.started.wait(2)
        assert not manager.unload(identity)["loaded"]
        with pytest.raises(RuntimeError, match="cancelled"):
            loading.result(timeout=2)
    assert identity not in manager.entries
    assert not entry.runner.running


def test_tampered_or_removed_weights_are_refused_before_new_inference(env):
    manager, root = env
    identity, weight = install(root)
    assert manager.resolve(identity)["loaded"]
    runner = manager.entries[identity].runner
    stamp = weight.stat()
    weight.write_bytes(b"fail" + weight.read_bytes()[4:])
    os.utime(weight, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1000000))
    with pytest.raises(ValueError, match="mtime changed"):
        manager.resolve(identity)
    assert len(runner.starts) == 1
    manager.unload(identity)
    with pytest.raises(ValueError, match="checksum mismatch"):
        manager.resolve(identity)
    weight.unlink()
    with pytest.raises(ValueError):
        manager.resolve(identity)


def test_cpu_configuration_is_per_identity_and_metadata_only(env):
    manager, root = env
    first, _ = install(root)
    second, _ = install(root, "test/two", "b" * 40)
    manager.configure(first, "cpu")
    assert not FakeRunner.instances
    manager.resolve(first)
    manager.resolve(second)
    assert manager.entries[first].runner.starts[0][2] == "cpu"
    assert manager.entries[second].runner.starts[0][2] == "vulkan"
    assert json.loads(manager.settings_path.read_text())[first] == "cpu"


def test_identity_requires_pinned_revision_and_rejects_arbitrary_paths():
    for identity in ("jarvis-local", "jarvis-managed:llm:test/one@main", "jarvis-managed:llm:../one@" + "a" * 40):
        with pytest.raises(ValueError):
            chat._parse(identity)


def test_shutdown_cancels_loading_and_atomically_rejects_later_resolves(env):
    manager, root = env
    identity, _ = install(root)
    entry = manager._entry(identity)
    entry.runner.gate = threading.Event()
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        loading = pool.submit(manager.resolve, identity, request_id="loading")
        assert entry.runner.started.wait(2)
        manager.stop()
        with pytest.raises(RuntimeError, match="cancelled"):
            loading.result(timeout=2)
    count = len(FakeRunner.instances)
    for operation in (lambda: manager.resolve(identity), lambda: manager.resolve(identity, request_id="late"), lambda: manager.configure(identity)):
        with pytest.raises(RuntimeError, match="shut down"):
            operation()
    assert len(FakeRunner.instances) == count
    assert not manager.entries
    manager.stop()


def test_early_cancellation_evicts_oldest_only(env):
    manager, _ = env
    for index in range(257):
        manager.cancel(f"request-{index}")
    assert len(manager.early_cancels) == 256
    assert "request-0" not in manager.early_cancels
    assert "request-1" in manager.early_cancels
    assert "request-256" in manager.early_cancels


@pytest.mark.parametrize("mode,device,running", [("vulkan", "Vulkan0", False), ("cpu", "none", True)])
def test_local_chat_respects_device_mode_and_runtime_identity(tmp_path, monkeypatch, mode, device, running):
    from local_mode import local_chat, local_embeddings
    from unittest.mock import Mock
    monkeypatch.setattr(local_chat, "ROOT", tmp_path)
    monkeypatch.setattr(local_embeddings, "ensure_runtime", lambda: tmp_path / "llama-server.exe")
    weight = tmp_path / "model.gguf"
    weight.write_bytes(b"GGUF")
    commands = []
    processes = []
    def spawn(args, **kwargs):
        commands.append(args)
        process = Mock()
        process.poll.return_value = 1 if mode == "vulkan" else None
        process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
        processes.append(process)
        return process
    monkeypatch.setattr(local_chat.subprocess, "Popen", spawn)
    session = Mock()
    session.get.side_effect = [local_chat.requests.ConnectionError(), Mock(status_code=200)]
    monkeypatch.setattr(local_chat.requests, "Session", lambda: session)
    runner = local_chat.LocalChat(port=21991, alias="isolated-alias", persist=False)
    if running:
        assert runner.start(str(weight), mode=mode)["running"]
    else:
        with pytest.raises(RuntimeError, match="exit code 1"):
            runner.start(str(weight), mode=mode)
        assert not runner.status()["running"]
        assert runner.log is None
    assert [args[args.index("--device") + 1] for args in commands] == [device]
    assert all(args[args.index("--alias") + 1] == "isolated-alias" for args in commands)
    assert all(args[args.index("--port") + 1] == "21991" for args in commands)
    assert ("--no-kv-offload" in commands[-1]) == (mode == "cpu")
    runner.stop()
    assert not runner.status()["running"]


def test_local_chat_cancelled_before_load_never_installs_runtime(tmp_path, monkeypatch):
    from local_mode import local_chat, local_embeddings
    from unittest.mock import Mock
    ensure = Mock(side_effect=AssertionError("runtime installation attempted"))
    monkeypatch.setattr(local_embeddings, "ensure_runtime", ensure)
    event = threading.Event()
    event.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        local_chat.LocalChat(persist=False).start(str(tmp_path / "absent.gguf"), cancel_event=event)
    ensure.assert_not_called()


def test_local_chat_cancel_during_warmup_stops_process_without_cpu_retry(tmp_path, monkeypatch):
    from local_mode import local_chat, local_embeddings
    from unittest.mock import Mock
    monkeypatch.setattr(local_chat, "ROOT", tmp_path)
    monkeypatch.setattr(local_embeddings, "ensure_runtime", lambda: tmp_path / "llama-server.exe")
    weight = tmp_path / "model.gguf"
    weight.write_bytes(b"GGUF")
    process = Mock()
    process.poll.return_value = None
    process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
    spawn = Mock(return_value=process)
    monkeypatch.setattr(local_chat.subprocess, "Popen", spawn)
    event = threading.Event()
    session = Mock()
    health_calls = 0
    def health(*args, **kwargs):
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            raise local_chat.requests.ConnectionError()
        event.set()
        return Mock(status_code=503)
    session.get.side_effect = health
    monkeypatch.setattr(local_chat.requests, "Session", lambda: session)
    runner = local_chat.LocalChat(port=21992, persist=False)
    with pytest.raises(RuntimeError, match="cancelled"):
        runner.start(str(weight), cancel_event=event)
    assert spawn.call_count == 1
    process.terminate.assert_called_once()
    assert runner.process is None
    assert runner.log is None
    session.close.assert_called_once()


def test_managed_load_forwards_cancellation_to_runtime_installer(tmp_path, monkeypatch):
    from local_mode import local_chat, local_embeddings
    from unittest.mock import Mock
    monkeypatch.setattr(local_embeddings, "CACHE", tmp_path / "missing-runtime")
    install = Mock(side_effect=RuntimeError("Runtime download cancelled"))
    monkeypatch.setattr(local_embeddings, "ensure_runtime", install)
    weight = tmp_path / "model.gguf"
    weight.write_bytes(b"GGUF")
    event = threading.Event()
    with pytest.raises(RuntimeError, match="cancelled"):
        local_chat.LocalChat(persist=False).start(str(weight), selection={"kind": "llm"}, cancel_event=event)
    install.assert_called_once_with(cancel_event=event)


def install_shards(root):
    identity, old = install(root)
    manifest_path = old.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    rows = []
    for index in range(2):
        # Token arrays precede split metadata in real GGUFs; skip without allocation.
        data = b"GGUF" + struct.pack("<IQQ",3,0,3)
        key = b"tokenizer.ggml.tokens"
        data += struct.pack("<Q",len(key))+key+struct.pack("<IIQ",9,8,2)
        for token in [b"hello", b"world"]:
            data += struct.pack("<Q",len(token))+token
        for key,value in [(b"split.no",index),(b"split.count",2)]:
            data += struct.pack("<Q",len(key))+key+struct.pack("<IH",2,value)
        path = old.parent / f"model-{index+1:05d}-of-00002.gguf"
        path.write_bytes(data)
        rows.append({"path":path.name,"size":len(data),"sha256":hashlib.sha256(data).hexdigest()})
    old.unlink()
    manifest["files"] = list(reversed(rows))
    manifest_path.write_text(json.dumps(manifest))
    return identity, old.parent, manifest_path


def test_complete_shards_load_first_part_and_verify_every_part(env):
    manager, root = env
    identity, folder, _ = install_shards(root)
    assert manager.list_models()[0]["model_id"] == identity
    manager.resolve(identity)
    runner = FakeRunner.instances[0]
    assert runner.starts[0][0] == str(folder / "model-00001-of-00002.gguf")
    selected = manager.entries[identity].selection
    assert len(selected["shards"]) == 2
    second = folder / "model-00002-of-00002.gguf"
    second.write_bytes(second.read_bytes().replace(b"hello", b"jello"))
    with pytest.raises(ValueError, match="changed"):
        library.validate_selected_model(selected,"llm")
    manager.unload(identity)
    with pytest.raises(ValueError, match="checksum"):
        manager.resolve(identity)
    assert len(runner.starts) == 1


@pytest.mark.parametrize("change", ["missing", "mixed", "metadata"])
def test_invalid_shard_groups_cannot_start(env, change):
    manager, root = env
    identity, folder, path = install_shards(root)
    manifest = json.loads(path.read_text())
    if change == "missing":
        manifest["files"].pop()
    elif change == "mixed":
        manifest["files"][0]["path"] = "other-00002-of-00002.gguf"
        (folder / "model-00002-of-00002.gguf").rename(folder / manifest["files"][0]["path"])
    else:
        file = folder / manifest["files"][0]["path"]
        data = file.read_bytes()[:-2] + struct.pack("<H",3)
        file.write_bytes(data)
        manifest["files"][0]["sha256"] = hashlib.sha256(data).hexdigest()
    path.write_text(json.dumps(manifest))
    if change != "metadata":
        assert not manager.list_models()
    with pytest.raises(ValueError):
        manager.resolve(identity)
    assert not any(runner.starts for runner in FakeRunner.instances)
