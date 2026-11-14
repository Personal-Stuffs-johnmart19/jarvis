"""Generated helpers save drafts; only a separately approved run executes code."""
from types import SimpleNamespace
from unittest.mock import Mock
import os
import stat

import pytest

from actions import code_helper as helper
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent
from local_mode.tools import ToolRegistry


@pytest.fixture
def generated(monkeypatch):
    model = SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(
        text="from pathlib import Path\nPath('must-not-run.txt').write_text('executed')\n")))
    monkeypatch.setattr(helper, "_get_gemini", lambda: model)
    run = Mock(side_effect=AssertionError("generated code must not execute"))
    monkeypatch.setattr(helper, "_run_action", run)
    return model, run


@pytest.fixture
def canonical():
    shared = SharedAgent(lambda *_: None)
    shared.attach("fixture", "session", "owner")
    core = ToolRegistry()
    registry = ServiceRegistry(SimpleNamespace(shared_agent=shared, event=lambda *_: None), lambda: core)
    return shared, registry


@pytest.mark.parametrize("args", [None, "", "literal string & untouched"])
def test_build_generates_once_saves_without_execution_and_requires_new_run_receipt(
        tmp_path, generated, canonical, args):
    model, run = generated
    shared, registry = canonical
    path = tmp_path / "draft.py"
    params = {"action": "build", "description": "Create a draft", "output_path": str(path)}
    if args is not None:
        params["args"] = args
    receipt = shared.prepare(registry, "code_helper", params)
    assert receipt["requires_approval"] is True
    model.generate_content.assert_not_called()
    assert not path.exists()
    result = shared.execute(registry, receipt["token"])
    assert result["ok"] is True and result["status"] == "saved"
    assert result["execution_pending"] is True and result["file_path"] == str(path)
    assert "separate approval" in result["result"]
    assert path.read_text() == model.generate_content.return_value.text.strip()
    assert not (tmp_path / "must-not-run.txt").exists()
    model.generate_content.assert_called_once()
    run.assert_not_called()
    with pytest.raises(ValueError, match="consumed"):
        shared.execute(registry, receipt["token"])
    fresh = shared.prepare(registry, "code_helper", {"action": "run", "file_path": str(path)})
    assert fresh["requires_approval"] is True and fresh["token"] != receipt["token"]
    run.assert_not_called()


@pytest.mark.parametrize("operation", ["fsync", "publish"])
def test_atomic_save_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch, operation):
    path = tmp_path / "existing.py"
    original = b"ORIGINAL\r\n\xff"
    path.write_bytes(original)
    def fail(*_):
        raise OSError("synthetic persistence failure")
    if operation == "publish":
        monkeypatch.setattr(helper, "_publish_file", fail)
    else:
        monkeypatch.setattr(helper.os, operation, fail)
    with pytest.raises(OSError, match="synthetic persistence failure"):
        helper._save_file(path, "new content")
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_atomic_save_publishes_exact_utf8(tmp_path):
    path = tmp_path / "nested" / "draft.py"
    content = "Русский 日本語 😀\r\nnext\n"
    assert helper._save_file(path, content) == f"Saved to: {path}"
    assert path.read_bytes() == content.encode("utf-8")
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("action", ["write", "edit", "optimize", "build"])
def test_persistence_failure_is_typed_through_registry_and_never_executes(
        tmp_path, monkeypatch, generated, canonical, action):
    model, run = generated
    shared, registry = canonical
    path = tmp_path / "existing.py"
    original = b"print('unchanged')\r\n"
    path.write_bytes(original)
    def fail(*_):
        raise PermissionError("synthetic replace denied")
    monkeypatch.setattr(helper, "_publish_file", fail)
    params = {"action": action, "description": "Update owned code",
              "file_path": str(path), "output_path": str(path)}
    receipt = shared.prepare(registry, "code_helper", params)
    result = shared.execute(registry, receipt["token"])
    assert result["ok"] is False and result["action"] == "code_helper"
    assert result["status"] in {"save_failed", "generation_or_save_failed"}
    assert "synthetic replace denied" in result["error"]
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    model.generate_content.assert_called_once()
    run.assert_not_called()


@pytest.mark.parametrize("action", ["write", "edit", "optimize", "build"])
def test_generation_failure_returns_typed_failure(tmp_path, monkeypatch, action):
    path = tmp_path / "existing.py"
    path.write_text("original")
    model = Mock()
    model.generate_content.side_effect = ValueError("synthetic generation failure")
    monkeypatch.setattr(helper, "_get_gemini", lambda: model)
    result = helper.code_helper({"action": action, "description": "Update code",
                                 "file_path": str(path), "output_path": str(path)})
    assert result["ok"] is False and "synthetic generation failure" in result["error"]
    assert path.read_text() == "original"
    model.generate_content.assert_called_once()


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable permission bits")
def test_save_preserves_existing_executable_permissions(tmp_path):
    path = tmp_path / "script.sh"
    path.write_text("old")
    path.chmod(0o755)
    helper._save_file(path, "new")
    assert path.read_text() == "new"
    assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_save_follows_existing_symlink_without_replacing_link(tmp_path):
    destination = tmp_path / "real.py"
    destination.write_text("old")
    link = tmp_path / "alias.py"
    try:
        link.symlink_to(destination.name)
    except OSError as error:
        pytest.skip(f"Host does not permit symlinks: {error}")
    helper._save_file(link, "updated")
    assert link.is_symlink() and os.readlink(link) == destination.name
    assert destination.read_text() == "updated"


@pytest.mark.skipif(os.name != "nt", reason="Windows ReplaceFileW recovery semantics")
def test_windows_partial_publication_failure_restores_original(tmp_path, monkeypatch):
    destination = tmp_path / "original.py"
    destination.write_bytes(b"original")
    def partially_replace(temporary, target, backup):
        os.replace(target, backup)
        raise OSError("synthetic ReplaceFileW partial failure")
    monkeypatch.setattr(helper, "_replace_windows", partially_replace)
    with pytest.raises(OSError, match="partial failure"):
        helper._save_file(destination, "updated")
    assert destination.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL identity")
def test_windows_save_preserves_existing_protected_dacl(tmp_path):
    import ctypes
    from ctypes import wintypes
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    get_security = security.GetFileSecurityW
    get_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get_security.restype = wintypes.BOOL
    set_security = security.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    get_dacl = security.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    def descriptor(path):
        size = wintypes.DWORD()
        get_security(str(path), 4, None, 0, ctypes.byref(size))
        value = ctypes.create_string_buffer(size.value)
        if not get_security(str(path), 4, value, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not get_dacl(value, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        assert present and acl.value
        header = ctypes.string_at(acl, 8)
        return ctypes.string_at(acl, int.from_bytes(header[2:4], "little"))
    path = tmp_path / "protected.py"
    path.write_text("old")
    original = ctypes.c_void_p()
    # Explicit owner/system access differs from a fresh file's inherited DACL.
    if not convert("D:P(A;;FA;;;OW)(A;;FA;;;SY)", 1, ctypes.byref(original), None):
        raise ctypes.WinError(ctypes.get_last_error())
    local_free = ctypes.WinDLL("kernel32").LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        if not set_security(str(path), 4, original):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        local_free(original)
    protected = descriptor(path)
    helper._save_file(path, "updated")
    assert path.read_text() == "updated"
    assert descriptor(path) == protected
