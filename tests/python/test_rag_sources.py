"""Offline source-to-common-retriever coverage; no model or personal data."""
import json
from pathlib import Path

import pytest

from local_mode import document_store as store
from local_mode import rag_sources as sources


@pytest.fixture
def source_env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversations = tmp_path / "conversations"
    conversations.mkdir()
    monkeypatch.setattr(sources, "_conversations_root", lambda: conversations)
    from local_mode import embeddings
    def fake(text="", backend=None, **kwargs):
        return {"provider": "local", "model": "offline", "vector": [float("needle" in text), 0.1]}
    monkeypatch.setattr(embeddings, "selected", fake)
    monkeypatch.setattr(embeddings, "batch_selected", lambda texts, backend=None: [fake(t) for t in texts])
    return workspace, conversations


def test_workspace_common_retrieval_and_exact_lines(source_env):
    workspace, _ = source_env
    text = "heading\n" + "line\n" * 400 + "needle target\nlast\n"
    file = workspace / "guide.md"
    file.write_bytes(text.encode("utf-8"))
    item = sources.ingest_workspace("guide.md", str(workspace))
    assert item["source"]["source_kind"] == "workspace"
    for semantic in (False, True):
        if semantic:
            store.embed(item["id"])
        hit = store.search("needle", semantic=semantic)["items"][0]
        citation = hit["citation"]
        assert hit["excerpt"] == text[citation["start"]:citation["end"]]
        assert citation["path"] == str(file.resolve())
        assert citation["line_start"] == text.count("\n", 0, citation["start"]) + 1
        assert citation["line_end"] == text.count("\n", 0, citation["end"] - 1) + 1
        assert citation["start"] <= text.index("needle") < citation["end"]
        assert len(hit["excerpt"]) <= store.CHUNK_SIZE


def test_snapshot_refresh_deduplicates_source_and_invalidates_vectors(source_env):
    workspace, _ = source_env
    file = workspace / "a.txt"
    file.write_text("needle old", encoding="utf-8")
    first = sources.ingest_workspace(str(file), str(workspace))
    store.embed(first["id"])
    same = sources.ingest_workspace(str(file), str(workspace))
    assert same["id"] == first["id"] and same["indexed"]
    file.write_text("replacement", encoding="utf-8")
    # Existing approved snapshot is stable until explicitly reimported.
    assert "needle" in store.search("needle")["items"][0]["excerpt"]
    store.embed(first["id"])
    refreshed = sources.ingest_workspace(str(file), str(workspace))
    assert refreshed["id"] == first["id"] and not refreshed["indexed"]
    assert store.search("needle")["items"] == []
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_identical_files_keep_distinct_provenance(source_env):
    workspace, _ = source_env
    for name in ("a.txt", "b.txt"):
        (workspace / name).write_text("needle same", encoding="utf-8")
        sources.ingest_workspace(name, str(workspace))
    hits = store.search("needle")["items"]
    assert len(hits) == 2
    assert len({h["citation"]["path"] for h in hits}) == 2


def test_chat_import_resolves_saved_turn_not_supplied_text(source_env):
    _, conversations = source_env
    turns = [{"id": "turn-a", "role": "user", "parts": [{"type": "text", "text": "first"}, {"type": "file", "url": "secret"}, {"type": "text", "text": "needle"}]},
             {"id": "turn-b", "role": "assistant", "parts": [{"type": "text", "text": "unselected content"}]}]
    path = conversations / "session-saved.json"
    path.write_text(json.dumps(turns), encoding="utf-8")
    item = sources.ingest_chat("saved", "turn-a")
    store.embed(item["id"])
    for semantic in (False, True):
        hit = store.search("needle", semantic=semantic)["items"][0]
        assert hit["excerpt"] == "first\nneedle"
        assert hit["citation"]["session_id"] == "saved"
        assert hit["citation"]["turn_id"] == "turn-a"
        assert hit["citation"]["coordinate_space"] == "chat_text_parts"
        assert hit["citation"]["line_end"] == 2
    with pytest.raises(ValueError, match="exactly one"):
        sources.ingest_chat("saved", "invented")
    with pytest.raises(ValueError, match="Invalid"):
        sources.ingest_chat("../saved", "turn-a")
    assert store.remove(item["id"])["removed"]
    assert sources.provenance(item["id"]) == {}


@pytest.mark.parametrize("name", [".env.local", "secret.pem", "api_keys.json", "credentials/token.txt"])
def test_workspace_secrets_blocked(source_env, name):
    workspace, _ = source_env
    path = workspace / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("needle private", encoding="utf-8")
    with pytest.raises(ValueError):
        sources.ingest_workspace(str(path), str(workspace))
    assert store.search("needle")["items"] == []


def test_outside_binary_oversize_and_query_bounds(source_env):
    workspace, _ = source_env
    outside = workspace.parent / "outside.txt"
    outside.write_text("needle", encoding="utf-8")
    with pytest.raises(ValueError):
        sources.ingest_workspace(str(outside), str(workspace))
    file = workspace / "binary.txt"
    file.write_bytes(b"text\0secret")
    with pytest.raises(ValueError):
        sources.ingest_workspace(str(file), str(workspace))
    file.write_bytes(b"x" * (sources.MAX_BYTES + 1))
    with pytest.raises(ValueError, match="2 MiB"):
        sources.ingest_workspace(str(file), str(workspace))
    for limit in (0, -1, 21, True):
        with pytest.raises(ValueError):
            store.search("needle", limit=limit)


def test_workspace_config_source_is_allowed_and_symlink_escape_rejected(source_env):
    workspace, _ = source_env
    config = workspace / "config"
    config.mkdir()
    file = config / "schema.py"
    file.write_text("needle config source", encoding="utf-8")
    assert sources.ingest_workspace(str(file), str(workspace))["characters"] > 0
    outside = workspace.parent / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private", encoding="utf-8")
    link = workspace / "link"
    if sources.os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="inside"):
            sources.ingest_workspace(str(link / "private.txt"), str(workspace))
    finally:
        # Unlink only the reparse-point itself, never its target tree.
        if sources.os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_saved_chat_rejects_duplicate_turns_and_non_text_content(source_env):
    _, conversations = source_env
    path = conversations / "session-real.json"
    turn = {"id": "turn", "role": "user", "parts": [{"type": "text", "text": "needle"}]}
    path.write_text(json.dumps([turn, turn]), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        sources.ingest_chat("real", "turn")
    turn["parts"] = [{"type": "tool-result", "output": "needle secret"}]
    path.write_text(json.dumps([turn]), encoding="utf-8")
    with pytest.raises(ValueError, match="text characters"):
        sources.ingest_chat("real", "turn")
    with pytest.raises(TypeError):
        sources.ingest_chat("real", "turn", text="invented")


def test_native_chat_root_separates_qa_app_data(monkeypatch):
    # This helper is server-owned; no path argument is exposed to the model.
    monkeypatch.setattr(sources.os, "name", "nt")
    monkeypatch.setenv("APPDATA", "C:/synthetic-appdata")
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    assert sources._conversations_root() == Path("C:/synthetic-appdata/com.mrcrous.jarvis.qa/conversations")
    monkeypatch.delenv("JARVIS_DESKTOP_TEST")
    assert sources._conversations_root() == Path("C:/synthetic-appdata/com.mrcrous.jarvis/conversations")


@pytest.mark.parametrize("change", ["refresh", "remove"])
def test_embedding_rejects_source_change_during_transport(source_env, monkeypatch, change):
    workspace, _ = source_env
    path = workspace / "race.txt"
    path.write_text("needle before", encoding="utf-8")
    item = sources.ingest_workspace(str(path), str(workspace))
    store.embed(item["id"])
    original = store._embed_batch

    def changed_during_embedding(texts, backend=None):
        result = original(texts, backend=backend)
        if change == "refresh":
            path.write_text("replacement after", encoding="utf-8")
            sources.ingest_workspace(str(path), str(workspace))
        else:
            store.remove(item["id"])
        return result

    monkeypatch.setattr(store, "_embed_batch", changed_during_embedding)
    with pytest.raises(ValueError, match="Source changed during indexing"):
        store.embed(item["id"])
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (item["id"],)).fetchone()[0] == 0
        row = db.execute("SELECT text, embedding FROM documents WHERE id=?", (item["id"],)).fetchone()
        if change == "refresh":
            assert row["text"] == "replacement after" and row["embedding"] is None
        else:
            assert row is None


@pytest.mark.parametrize("kind", ["workspace", "chat"])
def test_open_handle_validation_rejects_swapped_source(source_env, monkeypatch, kind):
    workspace, conversations = source_env
    root = workspace if kind == "workspace" else conversations
    selected = root / ("read.txt" if kind == "workspace" else "session-real.json")
    selected.write_text("safe" if kind == "workspace" else json.dumps([{"id": "turn", "role": "user", "parts": [{"type": "text", "text": "safe"}]}]), encoding="utf-8")
    outside = workspace.parent / "outside-secret.txt"
    outside.write_text("must never be read", encoding="utf-8")
    original_open = sources.os.open

    def swapped_open(path, flags, *args, **kwargs):
        # Simulate a path swap after canonical/identity validation but before
        # opening: the returned handle points to a different real file.
        if Path(path) == selected:
            return original_open(outside, flags, *args, **kwargs)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(sources.os, "open", swapped_open)
    with pytest.raises(ValueError, match="changed|differs|inside"):
        if kind == "workspace":
            sources.ingest_workspace(str(selected), str(root))
        else:
            sources.ingest_chat("real", "turn")
    assert store.search("must never")["items"] == []


def test_opened_handle_final_path_blocks_swapped_secret(source_env, monkeypatch):
    workspace, _ = source_env
    selected = workspace / "read.txt"
    selected.write_text("safe", encoding="utf-8")
    secret = workspace / ".env"
    secret.write_text("private", encoding="utf-8")
    monkeypatch.setattr(sources, "_opened_path", lambda stream, path: secret)
    with pytest.raises(ValueError, match="cannot be indexed"):
        sources.ingest_workspace(str(selected), str(workspace))
    assert store.search("private")["items"] == []


def test_explicit_refresh_preserves_id_and_only_invalidates_changed_text(source_env, monkeypatch):
    from unittest.mock import Mock
    workspace, _ = source_env
    path = workspace / "refresh.txt"
    path.write_text("needle original", encoding="utf-8")
    item = sources.ingest_workspace(str(path), str(workspace), "Chosen name")
    store.embed(item["id"])
    from local_mode import embeddings
    transport = Mock(side_effect=AssertionError("Refresh must not embed"))
    monkeypatch.setattr(embeddings, "selected", transport)
    same = sources.refresh(item["id"])
    assert same["id"] == item["id"] and same["indexed"] and not same["changed"]
    path.write_text("updated text", encoding="utf-8")
    changed = sources.refresh(item["id"])
    assert changed["id"] == item["id"] and changed["name"] == "Chosen name"
    assert changed["changed"] and not changed["indexed"]
    assert store.read(item["id"])["text"] == "updated text"
    assert store.search("needle")["items"] == []
    transport.assert_not_called()


def test_failed_or_deleted_source_refresh_keeps_old_snapshot(source_env):
    workspace, _ = source_env
    path = workspace / "deleted.txt"
    path.write_text("needle retained", encoding="utf-8")
    item = sources.ingest_workspace(str(path), str(workspace))
    store.embed(item["id"])
    path.unlink()
    with pytest.raises(FileNotFoundError):
        sources.refresh(item["id"])
    assert store.read(item["id"])["text"] == "needle retained"
    assert store.read(item["id"])["indexed"]


def test_refresh_does_not_resurrect_concurrently_removed_document(source_env, monkeypatch):
    workspace, _ = source_env
    path = workspace / "race.txt"
    path.write_text("needle", encoding="utf-8")
    item = sources.ingest_workspace(str(path), str(workspace))
    original = sources._workspace_snapshot
    def removed(*args, **kwargs):
        loaded = original(*args, **kwargs)
        store.remove(item["id"])
        return loaded
    monkeypatch.setattr(sources, "_workspace_snapshot", removed)
    with pytest.raises(ValueError, match="changed while refreshing"):
        sources.refresh(item["id"])
    assert store.search("needle")["items"] == []
    assert sources.provenance(item["id"]) == {}


def test_chat_refresh_reads_same_saved_turn(source_env):
    _, conversations = source_env
    path = conversations / "session-refresh.json"
    turns = [{"id":"turn", "role":"user", "parts":[{"type":"text","text":"old"}]}]
    path.write_text(json.dumps(turns), encoding="utf-8")
    item = sources.ingest_chat("refresh", "turn")
    turns[0]["parts"][0]["text"] = "updated saved turn"
    path.write_text(json.dumps(turns), encoding="utf-8")
    assert sources.refresh(item["id"])["id"] == item["id"]
    assert store.read(item["id"])["text"] == "updated saved turn"


def test_refresh_service_requires_approval_and_rejects_replacement_paths(source_env):
    from unittest.mock import Mock
    from jsonschema.exceptions import ValidationError
    from local_mode.services import ServiceRegistry
    workspace, _ = source_env
    path = workspace / "approved.txt"
    path.write_text("needle", encoding="utf-8")
    item = sources.ingest_workspace(str(path), str(workspace))
    registry = ServiceRegistry(Mock(), lambda: None)
    with pytest.raises(ValueError, match="approval"):
        registry.execute("documents.refresh", {"identifier":item["id"]})
    with pytest.raises(ValidationError):
        registry.validate("documents.refresh", {"identifier":item["id"],"path":"another.txt"})
    result = registry.execute("documents.refresh", {"identifier":item["id"]}, approved=True)
    assert result["refreshed"] and not result["changed"]


def test_refresh_does_not_overwrite_concurrent_approved_snapshot(source_env, monkeypatch):
    workspace, _ = source_env
    path = workspace / "changed-race.txt"
    path.write_text("original", encoding="utf-8")
    item = sources.ingest_workspace(str(path), str(workspace))
    original = sources._workspace_snapshot
    def replaced(*args, **kwargs):
        loaded = original(*args, **kwargs)
        path.write_text("concurrent approved snapshot", encoding="utf-8")
        sources._snapshot(*original(str(path), str(workspace)))
        return loaded
    monkeypatch.setattr(sources, "_workspace_snapshot", replaced)
    with pytest.raises(ValueError, match="changed while refreshing"):
        sources.refresh(item["id"])
    assert store.read(item["id"])["text"] == "concurrent approved snapshot"


def test_refresh_rejects_moved_saved_source_before_read(source_env, monkeypatch, tmp_path):
    from unittest.mock import Mock
    _, conversations = source_env
    turns = [{"id":"turn", "role":"user", "parts":[{"type":"text","text":"approved"}]}]
    (conversations / "session-moved.json").write_text(json.dumps(turns), encoding="utf-8")
    item = sources.ingest_chat("moved", "turn")
    changed_root = tmp_path / "other-conversations"
    changed_root.mkdir()
    (changed_root / "session-moved.json").write_text(json.dumps(turns), encoding="utf-8")
    monkeypatch.setattr(sources, "_conversations_root", lambda: changed_root)
    read = Mock(side_effect=AssertionError("Moved source must not be read"))
    monkeypatch.setattr(sources, "_read", read)
    with pytest.raises(ValueError, match="location changed"):
        sources.refresh(item["id"])
    read.assert_not_called()
    assert store.read(item["id"])["text"] == "approved"
