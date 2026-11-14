"""Credential admission and opened-object checks use owned temporary files only."""
import os
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

from actions import file_controller
from local_mode import file_archives, file_policy, rag_sources
from local_mode.tools import ToolRegistry, validate_paths


@pytest.mark.parametrize("relative", [
    "private.key", "private.pem.backup", "private.gpg", "private.jks",
    "secrets.toml", "api_keys.json.bak", "config/bridge_token.txt.bak",
    ".azure/token.json", ".kube/config", ".config/gcloud/tokens.json",
    ".ssh\u00a0/key", ".config/gcloud\u2000/token.json", ".ssh\ufeff/key",
    "data/network/proxy_cache.json.bak",
])
def test_shared_policy_blocks_credential_paths_before_dispatch(tmp_path, relative):
    with pytest.raises(ValueError, match="keys or tokens"):
        validate_paths({"path": str(tmp_path / relative)})


@pytest.mark.parametrize("relative", [
    "private.gpg", "private.asc", "private.jks", "private.key.bak",
    "config/bridge_token.txt.bak", "data/network/network_preferences.json.bak",
    ".azure/token.json", ".config/gcloud\u2000/tokens.json",
])
def test_late_protected_zip_member_never_publishes_early_data(tmp_path, relative):
    source = tmp_path / "owned.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("early.txt", "owned")
        bundle.writestr(relative, "SYNTHETIC_CREDENTIAL")
    target = tmp_path / "out"
    with pytest.raises(ValueError, match="protected"):
        file_archives.extract_zip(source, target)
    assert not target.exists()


def test_read_and_search_keep_literal_data_and_prune_private_descendants(tmp_path):
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("Error: literal user data", encoding="utf-8")
    private = tmp_path / ".azure"
    private.mkdir()
    (private / "token.json").write_text("SYNTHETIC_CREDENTIAL", encoding="utf-8")
    assert file_controller.read_file(str(ordinary)) == "Error: literal user data"
    for result in (file_controller.find_files(path=str(tmp_path)), file_controller.get_largest_files(str(tmp_path))):
        assert "ordinary.txt" in result
        assert "token.json" not in result
        assert ".azure" not in result
    with pytest.raises(ValueError, match="cannot be indexed"):
        rag_sources._safe_file(private / "token.json", tmp_path)


def test_text_reads_are_bounded_without_modifying_the_source(tmp_path):
    path = tmp_path / "large.txt"
    original = "a" * 500000
    path.write_text(original, encoding="utf-8")
    result = file_controller.read_file(str(path), max_chars=8)
    assert result == "aaaaaaaa\n\n... (truncated)"
    assert path.read_text(encoding="utf-8") == original


def test_open_rejects_replaced_inode_before_yielding_any_bytes(tmp_path, monkeypatch):
    selected, replacement = tmp_path / "selected.txt", tmp_path / "replacement.txt"
    selected.write_bytes(b"selected")
    replacement.write_bytes(b"replacement")
    original = os.open
    def swap(path, flags, *args, **kwargs):
        if Path(path) == selected:
            os.replace(replacement, selected)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(file_policy.os, "open", swap)
    entered = False
    with pytest.raises(ValueError, match="changed before opening"):
        with file_policy.checked_open(selected):
            entered = True
    assert not entered


def test_same_inode_does_not_authorize_a_protected_opened_path(tmp_path, monkeypatch):
    private = tmp_path / ".ssh"
    private.mkdir()
    protected = private / "owned.txt"
    protected.write_bytes(b"SYNTHETIC_CREDENTIAL")
    selected = tmp_path / "ordinary.txt"
    os.link(protected, selected)
    original = os.open
    def redirected(path, flags, *args, **kwargs):
        return original(protected if Path(path) == selected else path, flags, *args, **kwargs)
    monkeypatch.setattr(file_policy.os, "open", redirected)
    entered = False
    with pytest.raises(ValueError, match="keys or tokens"):
        with file_policy.checked_open(selected):
            entered = True
    assert not entered


def test_open_rejects_changes_while_reading(tmp_path):
    selected = tmp_path / "owned.txt"
    selected.write_bytes(b"before")
    with pytest.raises(ValueError, match="changed while reading"):
        with file_policy.checked_open(selected) as source:
            assert source.read() == b"before"
            selected.write_bytes(b"after-with-different-size")


def test_unicode_opened_paths_are_not_lossily_aliased():
    with pytest.raises(UnicodeError):
        file_policy._plain_windows_path("C:/owned/\ud800")
    assert file_policy._plain_windows_path(r"\\?\C:\owned\file.txt") == r"C:\owned\file.txt"


@pytest.mark.parametrize("alias,canonical", [("extract", "unzip"), ("organize_folder", "organize")])
def test_mutation_aliases_use_the_canonical_real_admission_gate(tmp_path, alias, canonical):
    for action in (alias, canonical):
        registry = ToolRegistry()
        invoke = Mock(return_value={"ok": True})
        registry._invoke = invoke
        args = {"action": action, "path": str(tmp_path), "destination": str(tmp_path / "out"), "scope": str(tmp_path)}
        registry.validate("file_controller", args)
        result = registry.execute("file_controller", args)
        assert result["requires_confirmation"] is True
        invoke.assert_not_called()


def test_approved_extract_alias_reaches_the_actual_handler_once(tmp_path):
    source = tmp_path / "owned.zip"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("literal.txt", "Error: literal data")
    destination = tmp_path / "out"
    registry = ToolRegistry()
    request = {"action": "extract", "path": str(source), "destination": str(destination)}
    assert registry.execute("file_controller", request)["requires_confirmation"]
    result = registry.confirm(True)
    assert result["ok"] is True
    assert (destination / "literal.txt").read_text(encoding="utf-8") == "Error: literal data"
    assert registry.confirm(True)["ok"] is False



@pytest.mark.parametrize("action", ["copy", "move"])
def test_recursive_copy_move_source_cannot_hide_private_descendants(tmp_path, action):
    source = tmp_path / "input"
    (source / ".azure").mkdir(parents=True)
    (source / "ordinary.txt").write_bytes(b"owned")
    protected = source / ".azure" / "token.json"
    protected.write_bytes(b"SYNTHETIC_CREDENTIAL")
    destination = tmp_path / "output"
    result = ToolRegistry().execute("file_controller", {
        "action": action, "path": str(source), "destination": str(destination),
    }, approved=True)
    assert result["ok"] is False
    assert not destination.exists()
    assert protected.read_bytes() == b"SYNTHETIC_CREDENTIAL"


def test_copy_does_not_replace_existing_destination(tmp_path):
    source, destination = tmp_path / "source.txt", tmp_path / "destination.txt"
    source.write_bytes(b"replacement")
    destination.write_bytes(b"original")
    result = ToolRegistry().execute("file_controller", {
        "action": "copy", "path": str(source), "destination": str(destination),
    })
    assert result["ok"] is False
    assert destination.read_bytes() == b"original"


def test_code_helper_reads_literal_owned_text_and_rejects_protected_and_oversized_source(tmp_path, monkeypatch):
    from actions import code_helper
    source = tmp_path / "owned.py"
    source.write_bytes(b"Error: literal source\r\n")
    assert code_helper._read_file(str(source)) == ("Error: literal source\r\n", "")
    protected = tmp_path / "private.key"
    protected.write_bytes(b"SYNTHETIC_CREDENTIAL")
    from local_mode.messages import OwnedValueError
    with pytest.raises(OwnedValueError, match="keys or tokens"):
        code_helper._read_file(str(protected))
    monkeypatch.setattr(code_helper, "MAX_SOURCE_BYTES", 4)
    with pytest.raises(OwnedValueError, match="exceeds"):
        code_helper._read_file(str(source))
    assert source.read_bytes() == b"Error: literal source\r\n"


@pytest.mark.parametrize("value,expected", [
    ("invalid\ud800", "Tools require a valid Unicode file path."),
    ("bad\npath", "Tools require a valid file path."),
])
def test_shared_policy_guard_errors_keep_owned_provenance(value, expected):
    from local_mode.messages import OwnedValueError
    with pytest.raises(OwnedValueError) as caught:
        file_policy.check_readable(value)
    assert caught.value.payload() == {"source": "jarvis", "message": expected, "values": []}


@pytest.mark.parametrize("name", [".git-credentials", ".git-credentials.bak"])
def test_git_credential_store_policy_is_shared_with_python_admission(tmp_path, name):
    from local_mode.messages import OwnedValueError
    with pytest.raises(OwnedValueError) as caught:
        validate_paths({"path": str(tmp_path / name)})
    assert caught.value.payload()["source"] == "jarvis"
    assert caught.value.payload()["message"] == "Tools cannot access keys or tokens."


@pytest.mark.parametrize("template", ["File changed before opening", "Source file exceeds 2 MiB."])
def test_code_helper_controlled_read_errors_reach_registry_as_owned_failures(tmp_path, monkeypatch, template):
    from contextlib import contextmanager
    from actions import code_helper
    from local_mode.messages import OwnedValueError, OwnedText
    source = tmp_path / "owned.py"
    source.write_text("owned source", encoding="utf-8")
    @contextmanager
    def refused(_):
        raise OwnedValueError(template)
        yield
    monkeypatch.setattr(file_policy, "checked_open", refused)
    generate = Mock(side_effect=AssertionError("a rejected source must not reach a model"))
    monkeypatch.setattr(code_helper, "_get_gemini", generate)
    result = ToolRegistry().execute("code_helper", {"action": "explain", "file_path": str(source)})
    assert result["ok"] is False
    assert isinstance(result["error"], OwnedText)
    assert result["error"].payload() == {"source": "jarvis", "message": template, "values": []}
    generate.assert_not_called()
