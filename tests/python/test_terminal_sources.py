"""Offline terminal-task ingestion from real persisted SDK conversation records."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from local_mode import document_store, rag_sources, terminal_sources


@pytest.fixture
def saved_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(document_store, "ROOT", tmp_path)
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    root = tmp_path / "conversations"
    root.mkdir()
    monkeypatch.setattr(rag_sources, "_conversations_root", lambda: root)
    from local_mode import embeddings
    def vector(text="", backend=None, **kwargs):
        return {"provider": "local", "model": "offline", "vector": [float("needle" in text), 0.1]}
    monkeypatch.setattr(embeddings, "selected", vector)
    monkeypatch.setattr(embeddings, "batch_selected", lambda texts, backend=None: [vector(t) for t in texts])
    part = {"type": "tool-bash_run", "state": "output-available", "toolCallId": "real-task",
            "input": {"command": "example"}, "approval": {"id": "approved", "approved": True},
            "output": {"stdout": "heading\n" + "unchanged line\n" * 140 + "needle\nlast", "stderr": "warning needle\n", "exit_code": 1, "timed_out": False, "truncated": True}}
    messages = [{"id": "actual-turn", "role": "assistant", "parts": [part]}]
    def save():
        (root / "session-real.json").write_bytes(json.dumps(messages).encode("utf-8"))
    save()
    return root, part, messages, save


def test_actual_task_stream_common_retrieval_exact_provenance(saved_terminal):
    root, part, _, _ = saved_terminal
    item = terminal_sources.ingest_terminal("real", "real-task")
    assert item["source"]["source_truncated"] is True
    assert item["untrusted_content"] is True
    assert terminal_sources.ingest_terminal("real", "real-task")["id"] == item["id"]
    for semantic in (False, True):
        if semantic:
            document_store.embed(item["id"])
        hit = document_store.search("needle", semantic=semantic)["items"][0]
        citation = hit["citation"]
        assert citation["source_kind"] == "terminal"
        assert citation["session_id"] == "real" and citation["turn_id"] == "actual-turn"
        assert citation["task_id"] == citation["tool_call_id"] == "real-task"
        assert citation["stream"] == "stdout" and citation["tool_name"] == "bash_run"
        assert citation["coordinate_space"] == "saved_terminal_stream"
        assert citation["path"] == str(root / "session-real.json")
        text = part["output"]["stdout"]
        assert hit["excerpt"] == text[citation["start"]:citation["end"]]
        assert len(hit["excerpt"]) <= document_store.CHUNK_SIZE
        assert citation["line_start"] == text.count("\n", 0, citation["start"]) + 1
    stderr = terminal_sources.ingest_terminal("real", "real-task", "stderr")
    assert stderr["id"] != item["id"]
    assert document_store.read(stderr["id"])["text"] == "warning needle\n"


@pytest.mark.parametrize("kind", ["tool-utility_run", "tool-run_code", "dynamic-tool", "tool-jarvis_action"])
def test_supported_saved_terminal_tool_routes(saved_terminal, kind):
    _, part, _, save = saved_terminal
    part["type"] = kind
    if kind == "dynamic-tool":
        part["toolName"] = "bash_run"
    if kind == "tool-jarvis_action":
        part["input"] = {"name": "run_code", "args": {"language": "python", "code": "example"}}
    save()
    assert terminal_sources.ingest_terminal("real", "real-task")["source"]["task_id"] == "real-task"


@pytest.mark.parametrize("mutation", ["pending", "denied", "not_terminal", "duplicate", "user", "unstructured", "no_message_id"])
def test_not_an_authoritative_completed_terminal_result_rejected(saved_terminal, mutation):
    _, part, messages, save = saved_terminal
    if mutation == "pending":
        part["state"] = "input-available"
    elif mutation == "denied":
        part["approval"]["approved"] = False
    elif mutation == "not_terminal":
        part["type"] = "tool-read_file"
    elif mutation == "duplicate":
        messages[0]["parts"].append(part.copy())
    elif mutation == "user":
        messages[0]["role"] = "user"
    elif mutation == "unstructured":
        part["output"] = "invented stdout"
    elif mutation == "no_message_id":
        messages[0].pop("id")
    save()
    with pytest.raises(ValueError):
        terminal_sources.ingest_terminal("real", "real-task")
    assert document_store.search("needle")["items"] == []


def test_exact_saved_ids_no_forged_text_or_private_scrollback(saved_terminal):
    with pytest.raises(ValueError, match="exactly one"):
        terminal_sources.ingest_terminal("real", "invented-task")
    with pytest.raises(ValueError):
        terminal_sources.ingest_terminal("../real", "real-task")
    with pytest.raises(ValueError):
        terminal_sources.ingest_terminal("real", "real-task", "interactive_scrollback")
    with pytest.raises(TypeError):
        terminal_sources.ingest_terminal("real", "real-task", text="invented")


def test_terminal_service_requires_approval_and_then_resolves_saved_output(saved_terminal):
    from jsonschema.exceptions import ValidationError
    from local_mode.services import ServiceRegistry
    registry = ServiceRegistry(SimpleNamespace(event=Mock()), lambda: None)
    args = {"session_id": "real", "task_id": "real-task", "stream": "stderr"}
    with pytest.raises(ValueError, match="approval"):
        registry.execute("documents.import_terminal", args)
    assert document_store.search("needle")["items"] == []
    with pytest.raises(ValidationError):
        registry.validate("documents.import_terminal", {**args, "text": "invented"})
    item = registry.execute("documents.import_terminal", args, approved=True)
    assert document_store.read(item["id"])["text"] == "warning needle\n"
    assert document_store.search("needle")["items"][0]["citation"]["task_id"] == "real-task"


def test_terminal_refresh_keeps_id_when_truncation_metadata_changes(saved_terminal):
    _, part, _, save = saved_terminal
    item = terminal_sources.ingest_terminal("real", "real-task")
    document_store.embed(item["id"])
    part["output"]["truncated"] = False
    save()
    same = rag_sources.refresh(item["id"])
    assert same["id"] == item["id"] and not same["changed"] and same["indexed"]
    assert same["source"]["source_truncated"] is False
    part["output"]["stdout"] = "updated stdout"
    save()
    updated = rag_sources.refresh(item["id"])
    assert updated["id"] == item["id"] and updated["changed"] and not updated["indexed"]
    assert document_store.read(item["id"])["text"] == "updated stdout"


def test_terminal_refresh_refuses_changed_tool_identity(saved_terminal):
    _, part, _, save = saved_terminal
    item = terminal_sources.ingest_terminal("real", "real-task")
    part["type"] = "tool-run_code"
    save()
    with pytest.raises(ValueError, match="identity changed"):
        rag_sources.refresh(item["id"])
    assert "needle" in document_store.read(item["id"])["text"]


def test_terminal_reimport_deduplicates_changed_truncation_metadata(saved_terminal):
    _, part, _, save = saved_terminal
    first = terminal_sources.ingest_terminal("real", "real-task")
    document_store.embed(first["id"])
    part["output"]["truncated"] = False
    save()
    same = terminal_sources.ingest_terminal("real", "real-task")
    assert same["id"] == first["id"] and same["indexed"]
    assert same["source"]["source_truncated"] is False
    with document_store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_terminal_reimport_reuses_legacy_identity_without_losing_vectors(saved_terminal):
    import hashlib
    import time
    _, part, _, save = saved_terminal
    text, source, name = terminal_sources._terminal_snapshot("real", "real-task")
    legacy = hashlib.sha256(json.dumps(source, sort_keys=True).encode("utf-8")).hexdigest()
    with document_store.connection() as db:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,NULL)", (legacy,name,source["path"],text,len(text.encode()),time.time()))
        db.execute("INSERT INTO document_sources VALUES (?,?)", (legacy,json.dumps({**source,"content_hash":hashlib.sha256(text.encode()).hexdigest()})))
    document_store.embed(legacy)
    part["output"]["truncated"] = False
    save()
    assert terminal_sources.ingest_terminal("real", "real-task")["id"] == legacy
    assert document_store.read(legacy)["indexed"]
    assert rag_sources.refresh(legacy)["id"] == legacy
    assert terminal_sources.ingest_terminal("real", "real-task")["id"] == legacy
    with document_store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
