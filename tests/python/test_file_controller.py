"""Real canonical file outcomes and bounded ZIP abuse cases, owned files only."""
from pathlib import Path
import os
import stat
from unittest.mock import patch
import zipfile

import pytest

from local_mode import file_archives
from local_mode.shared_agent import SharedAgent
from local_mode.tools import ToolRegistry


@pytest.fixture
def call():
    registry = ToolRegistry()
    bridge = SharedAgent(lambda *_: None)

    def execute(action, path, **kwargs):
        receipt = bridge.prepare(registry, "file_controller", {"action": action, "path": str(path), **kwargs})
        result = bridge.execute(registry, receipt["token"])
        with pytest.raises(ValueError, match="consumed"):
            bridge.execute(registry, receipt["token"])
        return result
    return execute


def archive(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as bundle:
        for name, content in entries:
            if isinstance(name, str):
                entry = zipfile.ZipInfo(name)
                entry.filename = name  # Preserve malicious wire spelling on Windows.
                name = entry
            bundle.writestr(name, content)
    return path


@pytest.mark.parametrize("action", ["read", "copy", "move", "rename", "list", "find", "largest", "info", "zip"])
def test_missing_file_is_a_typed_failure(call, tmp_path, action):
    result = call(action, tmp_path / "missing", destination=str(tmp_path / "out"), new_name="renamed")
    assert result["ok"] is False and result["error"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("text", ["Error: literal data", "Could not create file: literal data", "❌ owned text", "Ошибка: данные"])
def test_read_preserves_literal_error_looking_file_contents(call, tmp_path, text):
    path = tmp_path / "owned.txt"
    path.write_text(text, encoding="utf-8")
    result = call("read", path)
    assert result["ok"] is True and result["result"] == text
    assert result["verification"] == "tool_report"


def test_file_roundtrip_and_rename_conflict_keep_exact_bytes(call, tmp_path):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    assert call("create_file", first, content="Error: owned bytes")["ok"]
    assert call("copy", first, destination=str(second))["ok"]
    result = call("rename", second, new_name=first.name)
    assert result["ok"] is False
    assert first.read_bytes() == second.read_bytes() == b"Error: owned bytes"
    assert call("rename", second, new_name="renamed.txt")["ok"]
    assert not second.exists() and (tmp_path / "renamed.txt").read_bytes() == first.read_bytes()
    assert call("list", first)["ok"] is False
    assert call("read", tmp_path)["ok"] is False


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/absolute.txt", "a/../escape.txt",
    "a\\escape.txt", "file.txt:stream", "a/./b", "a/bad?.txt", "a/bad*.txt", 'a/bad".txt', "a/bad|.txt", "a/bad<.txt", "a/bad>.txt", "NUL.txt", "NUL .txt", "CON .log", "COM1 .txt", "a/trailing. ", "a//b",
    ".env", ".ssh/key", "nested/api_keys.json", "nested/service-account.json", "nested/token.dpapi",
    ".git/config", "nested/private.pem", "nested/credentials.json"])
def test_unsafe_late_zip_member_rejects_before_any_destination_write(call, tmp_path, name):
    source = archive(tmp_path / "input.zip", [("safe.txt", "owned"), (name, "blocked")])
    destination = tmp_path / "output"
    result = call("unzip", source, destination=str(destination))
    assert result["ok"] is False and result["error"]
    assert not destination.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["input.zip"]


@pytest.mark.parametrize("entries", [[("same.txt", "one"), ("SAME.txt", "two")],
    [("a", "file"), ("a/b.txt", "nested")], [("A/one.txt", "one"), ("a/two.txt", "two")]])
def test_zip_case_aliases_and_file_directory_conflicts_are_prevalidated(call, tmp_path, entries):
    source = archive(tmp_path / "input.zip", entries)
    destination = tmp_path / "output"
    assert call("unzip", source, destination=str(destination))["ok"] is False
    assert not destination.exists()


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_zip_links_and_special_members_are_rejected(call, tmp_path, kind):
    entry = zipfile.ZipInfo("link")
    entry.create_system = 3
    entry.external_attr = (kind | 0o777) << 16
    source = archive(tmp_path / "input.zip", [("safe.txt", "owned"), (entry, "target")])
    destination = tmp_path / "output"
    assert call("unzip", source, destination=str(destination))["ok"] is False
    assert not destination.exists()


def test_zip_roundtrip_merges_new_files_without_overwriting(call, tmp_path):
    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "sub").mkdir()
    (folder / "sub" / "owned.txt").write_bytes(b"owned\x00bytes")
    (folder / "empty").mkdir()
    source = tmp_path / "owned.zip"
    assert call("zip", folder, destination=str(source))["ok"]
    destination = tmp_path / "out"
    destination.mkdir()
    existing = destination / "existing.txt"
    existing.write_bytes(b"preserve me")
    assert call("unzip", source, destination=str(destination))["ok"]
    assert (destination / "input/sub/owned.txt").read_bytes() == b"owned\x00bytes"
    assert (destination / "input/empty").is_dir()
    assert existing.read_bytes() == b"preserve me"
    before = {str(path.relative_to(destination)): path.read_bytes() for path in destination.rglob('*') if path.is_file()}
    assert call("unzip", source, destination=str(destination))["ok"] is False
    assert before == {str(path.relative_to(destination)): path.read_bytes() for path in destination.rglob('*') if path.is_file()}


def test_late_conflict_preserves_existing_files_and_writes_no_early_members(call, tmp_path):
    source = archive(tmp_path / "input.zip", [("early.txt", "new"), ("keep.txt", "replacement")])
    destination = tmp_path / "out"
    destination.mkdir()
    (destination / "keep.txt").write_bytes(b"original")
    assert call("unzip", source, destination=str(destination))["ok"] is False
    assert not (destination / "early.txt").exists()
    assert (destination / "keep.txt").read_bytes() == b"original"


def test_corrupt_late_member_does_not_publish_earlier_valid_data(call, tmp_path):
    source = archive(tmp_path / "input.zip", [("early.txt", "valid"), ("late.txt", "CORRUPT-ME")])
    source.write_bytes(source.read_bytes().replace(b"CORRUPT-ME", b"WRONG-BYTS"))
    destination = tmp_path / "out"
    assert call("unzip", source, destination=str(destination))["ok"] is False
    assert not destination.exists()


@pytest.mark.parametrize("limit", ["entries", "expanded", "input"])
def test_zip_limits_reject_before_writes(call, tmp_path, limit):
    source = archive(tmp_path / "input.zip", [("one.txt", "1234"), ("two.txt", "5678")])
    destination = tmp_path / "out"
    target = "MAX_ENTRIES" if limit == "entries" else "MAX_TOTAL"
    value = 1 if limit == "entries" else (5 if limit == "expanded" else -8 * 1024 * 1024)
    with patch.object(file_archives, target, value):
        assert call("unzip", source, destination=str(destination))["ok"] is False
    assert not destination.exists()


@pytest.mark.parametrize("problem", ["protected", "self", "overwrite", "size", "entries"])
def test_zip_creation_rejects_unsafe_source_or_destination(call, tmp_path, problem):
    folder = tmp_path / "input"
    folder.mkdir()
    (folder / "owned.txt").write_bytes(b"owned data")
    destination = tmp_path / "out.zip"
    if problem == "protected":
        (folder / "api_keys.json").write_bytes(b"owned fake key")
    elif problem == "self":
        destination = folder / "out.zip"
    elif problem == "overwrite":
        destination.write_bytes(b"preserve original")
    with patch.object(file_archives, "MAX_TOTAL", 1 if problem == "size" else file_archives.MAX_TOTAL), \
         patch.object(file_archives, "MAX_ENTRIES", 1 if problem == "entries" else file_archives.MAX_ENTRIES):
        assert call("zip", folder, destination=str(destination))["ok"] is False
    if problem == "overwrite":
        assert destination.read_bytes() == b"preserve original"
    else:
        assert not destination.exists()


def test_reparse_destination_and_source_are_rejected_without_following(call, tmp_path, monkeypatch):
    # A real Windows symlink may require developer privileges; inject its lstat flag.
    folder = tmp_path / "input"
    folder.mkdir()
    source = folder / "owned.txt"
    source.write_bytes(b"owned")
    original = Path.lstat
    def linked(path):
        info = original(path)
        if path == source:
            return type("LinkStat", (), {"st_mode": stat.S_IFREG, "st_file_attributes": 0x400})()
        return info
    monkeypatch.setattr(Path, "lstat", linked)
    assert call("zip", folder, destination=str(tmp_path / "out.zip"))["ok"] is False
    assert not (tmp_path / "out.zip").exists()

@pytest.mark.parametrize("action", ["read", "create_file", "write", "copy", "move", "rename"])
def test_derived_source_name_cannot_bypass_protected_file_guard(call, tmp_path, action):
    protected = tmp_path / ".env"
    protected.write_bytes(b"owned fake secret")
    result = call(action, tmp_path, name=".env", content="replacement",
        new_name="renamed.txt", destination=str(tmp_path / "destination.txt"))
    assert result["ok"] is False
    assert protected.read_bytes() == b"owned fake secret"
    assert sorted(path.name for path in tmp_path.iterdir()) == [".env"]


@pytest.mark.parametrize("new_name", [".env", "api_keys.json", "..", "../escape.txt", "/absolute.txt", "a\\escape.txt", "file.txt:stream"])
def test_rename_rejects_protected_or_non_filename_destination(call, tmp_path, new_name):
    source = tmp_path / "owned.txt"
    source.write_bytes(b"owned source")
    result = call("rename", source, new_name=new_name)
    assert result["ok"] is False
    assert source.read_bytes() == b"owned source"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["owned.txt"]

@pytest.mark.parametrize("name", ["owned.zip:payload", "trailing.zip.", "trailing.zip ", "NUL.zip", "COM1.zip"])
@pytest.mark.parametrize("action", ["zip", "unzip"])
def test_archive_top_level_destination_rejects_windows_aliases(call, tmp_path, name, action):
    source = tmp_path / "owned.txt"
    source.write_bytes(b"owned")
    if action == "unzip":
        source = archive(tmp_path / "input.zip", [("safe.txt", "owned")])
    destination = tmp_path / name
    if os.name == "nt":
        from local_mode.messages import OwnedValueError
        # The shared Windows guard rejects these before the handler is invoked.
        with pytest.raises(OwnedValueError, match="Windows device paths"):
            call(action, source, destination=str(destination))
    else:
        result = call(action, source, destination=str(destination))
        assert result["ok"] is False
    assert sorted(path.name for path in tmp_path.iterdir()) == (["input.zip", "owned.txt"] if action == "unzip" else ["owned.txt"])
    assert not (tmp_path / "safe.txt").exists()
    if ':' in name:
        assert not destination.exists()


def test_create_file_preserves_existing_bytes_but_explicit_write_updates(call, tmp_path):
    path = tmp_path / "owned.txt"
    path.write_bytes(b"owned original bytes")
    result = call("create_file", path, content="unexpected replacement")
    assert result["ok"] is False and result["error"]
    assert path.read_bytes() == b"owned original bytes"
    result = call("write", path, content="requested replacement")
    assert result["ok"] is True
    assert path.read_bytes() == b"requested replacement"