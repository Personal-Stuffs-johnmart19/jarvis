"""Offline cancellable pinned-runtime download and atomic extraction checks."""
import concurrent.futures
import hashlib
import io
import os
import threading
import zipfile

import pytest

from local_mode import downloads, local_embeddings as runtime


def archive_bytes(names=("llama-server.exe", "ggml.dll")):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name in names:
            archive.writestr(name, b"verified runtime bytes")
    return output.getvalue()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Pinned Windows runtime installation")
    data = archive_bytes()
    monkeypatch.setattr(runtime, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(runtime, "RUNTIME_SHA", hashlib.sha256(data).hexdigest())
    def download(url, target, **kwargs):
        downloads.check_cancelled(kwargs.get("cancel_event"))
        assert kwargs["sha256"] == hashlib.sha256(data).hexdigest()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    monkeypatch.setattr(runtime, "download_file", download)
    return data, download


def test_fresh_runtime_publishes_only_complete_verified_directory(installation):
    executable = runtime.ensure_runtime(cancel_event=threading.Event())
    assert executable.read_bytes() == b"verified runtime bytes"
    assert executable.with_name("ggml.dll").is_file()
    assert executable.with_name(".installed").read_text() == runtime.RUNTIME_SHA
    assert not list(runtime.CACHE.glob(f".{runtime.BUILD}-*"))
    assert runtime.ensure_runtime() == executable


def test_cancel_during_extraction_leaves_no_runtime_or_marker(installation, monkeypatch):
    event = threading.Event()
    read = zipfile.ZipExtFile.read
    def cancelled_read(self, *args, **kwargs):
        data = read(self, *args, **kwargs)
        event.set()
        return data
    monkeypatch.setattr(zipfile.ZipExtFile, "read", cancelled_read)
    with pytest.raises(RuntimeError, match="cancelled"):
        runtime.ensure_runtime(cancel_event=event)
    assert not (runtime.CACHE / runtime.BUILD).exists()
    assert not list(runtime.CACHE.glob(f".{runtime.BUILD}-*"))
    assert not list(runtime.CACHE.rglob(".installed"))


def test_incomplete_legacy_runtime_is_preserved(installation):
    legacy = runtime.CACHE / runtime.BUILD
    legacy.mkdir(parents=True)
    unknown = legacy / "user-note.txt"
    unknown.write_text("preserve")
    executable = runtime.ensure_runtime(cancel_event=threading.Event())
    assert executable.parent != legacy
    assert unknown.read_text() == "preserve"
    assert not (legacy / ".installed").exists()


@pytest.mark.parametrize("names", [("../escape", "llama-server.exe"), ("NUL", "llama-server.exe"), ("ggml.dll",), ("llama-server.exe", "LLAMA-SERVER.EXE")])
def test_invalid_archive_never_publishes_runtime(installation, monkeypatch, names):
    data = archive_bytes(names)
    monkeypatch.setattr(runtime, "RUNTIME_SHA", hashlib.sha256(data).hexdigest())
    def download(url, target, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    monkeypatch.setattr(runtime, "download_file", download)
    with pytest.raises(ValueError):
        runtime.ensure_runtime(cancel_event=threading.Event())
    assert not (runtime.CACHE / runtime.BUILD).exists()
    assert not list(runtime.CACHE.rglob(".installed"))


def test_cancelled_install_waiter_does_not_interrupt_other_install(installation, monkeypatch):
    _, download = installation
    started, finish, cancel = threading.Event(), threading.Event(), threading.Event()
    def held_download(*args, **kwargs):
        started.set()
        assert finish.wait(2)
        return download(*args, **kwargs)
    monkeypatch.setattr(runtime, "download_file", held_download)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(runtime.ensure_runtime, threading.Event())
        assert started.wait(2)
        second = pool.submit(runtime.ensure_runtime, cancel)
        cancel.set()
        with pytest.raises(RuntimeError, match="cancelled"):
            second.result(timeout=2)
        assert not first.done()
        finish.set()
        assert first.result(timeout=2).is_file()


class Response:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        assert size == 64 * 1024
        yield from self.chunks()


def test_download_cancel_removes_only_its_partial_and_preserves_target(tmp_path, monkeypatch):
    event = threading.Event()
    target = tmp_path / "runtime.zip"
    target.write_bytes(b"old invalid archive")
    unrelated = tmp_path / "unrelated.part"
    unrelated.write_bytes(b"keep")
    def chunks():
        yield b"one"
        event.set()
        yield b"two"
    response = Response(chunks)
    def get(url, **kwargs):
        assert kwargs["timeout"] == (3, 1)
        return response
    monkeypatch.setattr(downloads.requests, "get", get)
    with pytest.raises(RuntimeError, match="cancelled"):
        downloads.download_file("https://example.invalid/runtime", target, max_bytes=100, sha256="a" * 64, cancel_event=event)
    assert response.closed
    assert target.read_bytes() == b"old invalid archive"
    assert unrelated.read_bytes() == b"keep"
    assert list(tmp_path.glob("runtime.zip.*.part")) == []


def test_download_checksum_mismatch_cannot_replace_existing_archive(tmp_path, monkeypatch):
    target = tmp_path / "runtime.zip"
    target.write_bytes(b"old")
    response = Response(lambda: [b"wrong"])
    def get(url, **kwargs):
        assert kwargs["timeout"] == (15, 180)
        return response
    monkeypatch.setattr(downloads.requests, "get", get)
    with pytest.raises(ValueError, match="SHA256"):
        downloads.download_file("https://example.invalid/runtime", target, max_bytes=100, sha256="a" * 64)
    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob("runtime.zip.*.part"))


def test_cancelled_download_lock_waiter_never_contacts_network(tmp_path, monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(downloads.requests, "get", lambda *a, **k: pytest.fail("network attempted"))
    downloads._PUBLICATION_LOCK.acquire()
    try:
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            task = pool.submit(downloads.download_file, "https://example.invalid/runtime", tmp_path / "runtime.zip", max_bytes=100, cancel_event=event)
            event.set()
            with pytest.raises(RuntimeError, match="cancelled"):
                task.result(timeout=2)
    finally:
        downloads._PUBLICATION_LOCK.release()


def test_cancellable_download_reads_available_data_without_filling_chunk(tmp_path, monkeypatch):
    from unittest.mock import Mock
    event = threading.Event()
    response = Response(lambda: pytest.fail("buffer-filling iterator used"))
    response.raw = Mock()
    calls = 0
    def read1(size, decode_content):
        nonlocal calls
        assert size == 64 * 1024 and decode_content is True
        calls += 1
        if calls == 2:
            event.set()
        return b"x"
    response.raw.read1.side_effect = read1
    monkeypatch.setattr(downloads.requests, "get", lambda *a, **k: response)
    target = tmp_path / "runtime.zip"
    with pytest.raises(RuntimeError, match="cancelled"):
        downloads.download_file("https://example.invalid/runtime", target, max_bytes=100, cancel_event=event)
    assert calls == 2
    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))
