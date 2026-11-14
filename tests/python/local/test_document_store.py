from unittest.mock import Mock
import pytest
from local_mode import document_store as store


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    from local_mode import local_embeddings
    monkeypatch.setattr(local_embeddings, "preferences_path", lambda: tmp_path / "embedding-settings.json")
    folder = tmp_path / ".cache/attachments"
    folder.mkdir(parents=True)
    path = folder / "attachment-report.md"
    path.write_text(
        "Alpha project. GPU Vulkan.\nSecond paragraph: semantic search.",
        encoding="utf8",
    )
    return path


def test_upload_is_persistent_deduplicated_paginated_and_searchable(uploaded):
    item = store.ingest(str(uploaded), "report.md")
    assert item["characters"] and not item["indexed"]
    assert store.ingest(str(uploaded), "report.md")["id"] == item["id"]
    first = store.read(item["id"], 0, 10)
    assert first["text"] == "Alpha proj" and first["next_offset"] == 10
    assert store.search("Vulkan")["items"][0]["id"] == item["id"]
    assert store.search("absent")["items"] == []
    assert store.search()["total"] == 1


def test_no_reading_files_outside_uploaded_storage(uploaded, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("private")
    with pytest.raises(ValueError, match="explicitly uploaded"):
        store.ingest(str(outside))


def test_embedding_stored_and_semantic_query_requires_matching_model(
    uploaded, monkeypatch
):
    from local_mode import embeddings

    provider = Mock(
        return_value={
            "model": "test-model",
            "dimensions": 2,
            "vector": [1.0, 0.0],
            "input_tokens": 3,
        }
    )
    monkeypatch.setattr(embeddings, "selected", provider)
    # Indexing now batches chunks; mock that transport as well to avoid real model inference.
    monkeypatch.setattr(embeddings, "batch_selected", lambda texts, backend=None: [provider(text=text, backend=backend) for text in texts])
    item = store.ingest(str(uploaded), "report.md")
    assert "vector" not in store.embed(item["id"], "local")
    assert store.read(item["id"])["indexed"]
    assert (
        store.search("similar", semantic=True, backend="local")["items"][0]["score"]
        == 1
    )
    provider.return_value = {"model": "different-model", "vector": [1.0, 0.0]}
    assert not store.search("similar", semantic=True, backend="local")["items"]


def test_changed_original_is_not_embedded_under_old_id(uploaded, monkeypatch):
    from local_mode import embeddings

    provider = Mock()
    monkeypatch.setattr(embeddings, "selected", provider)
    item = store.ingest(str(uploaded))
    uploaded.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        store.embed(item["id"])
    provider.assert_not_called()


def test_document_services_share_approval_policy(uploaded):
    from types import SimpleNamespace
    from local_mode.services import ServiceRegistry

    registry = ServiceRegistry(SimpleNamespace(event=Mock()), lambda: None)
    item = registry.ui_call(
        "documents.import", {"path": str(uploaded), "name": "report.md"}
    )
    assert not registry.risky("documents.read", {"identifier": item["id"]})
    assert not registry.risky("documents.search", {"query": "GPU"})
    assert registry.risky("documents.search", {"query": "GPU", "semantic": True})
    assert registry.risky("documents.embed", {"identifier": item["id"]})
    with pytest.raises(ValueError, match="approval"):
        registry.execute("documents.embed", {"identifier": item["id"]})


def test_remove_index_preserves_source_file(uploaded):
    item = store.ingest(str(uploaded))
    assert store.remove(item["id"])["removed"]
    assert uploaded.is_file()
    assert not store.search()["items"]


def test_shared_chunk_settings_persist_and_index_records_actual_spans(uploaded, monkeypatch):
    import json
    from local_mode import local_embeddings, embeddings
    local_embeddings.preferences("cpu", chunk_size=256, chunk_overlap=32)
    assert local_embeddings.preferences()["chunk_overlap"] == 32
    uploaded.write_text("word " * 200, encoding="utf8")
    item = store.ingest(str(uploaded))
    batches = []
    def batch(texts, backend=None):
        batches.append(texts)
        return [{"model":"synthetic", "vector":[1.0,0.0], "dimensions":2} for _ in texts]
    monkeypatch.setattr(embeddings, "batch_selected", batch)
    store.embed(item["id"], "cpu")
    with store.connection() as db:
        old = db.execute("SELECT embedding FROM documents WHERE id=?", (item["id"],)).fetchone()[0]
        meta = json.loads(old)
        rows = db.execute("SELECT start,end FROM chunks WHERE document_id=? ORDER BY idx", (item["id"],)).fetchall()
    assert meta["chunk_size"] == 256 and meta["chunk_overlap"] == 32
    assert len(rows) > 1 and all(end-start <= 256 for start,end in rows)
    assert rows[1][0] == rows[0][1] - 32
    local_embeddings.preferences(chunk_size=512, chunk_overlap=64)
    with store.connection() as db:
        assert db.execute("SELECT embedding FROM documents WHERE id=?", (item["id"],)).fetchone()[0] == old
    store.rebuild(backend="cpu")
    with store.connection() as db:
        meta = json.loads(db.execute("SELECT embedding FROM documents WHERE id=?", (item["id"],)).fetchone()[0])
    assert meta["chunk_size"] == 512 and len(batches) == 2


@pytest.mark.parametrize("values", [dict(chunk_size=True), dict(chunk_size=255), dict(chunk_size=8193), dict(chunk_overlap=-1), dict(chunk_size=256,chunk_overlap=256), dict(chunk_size=512.5)])
def test_invalid_chunk_settings_do_not_mutate_saved_preferences(uploaded, values):
    from local_mode import local_embeddings
    before = local_embeddings.preferences("cpu", chunk_size=512, chunk_overlap=64)
    with pytest.raises(ValueError):
        local_embeddings.preferences(**values)
    assert local_embeddings.preferences() == before


def test_high_overlap_stops_constructing_spans_at_limit(monkeypatch):
    class CountedText(str):
        searches = 0
        def rfind(self, *args):
            self.searches += 1
            return super().rfind(*args)
    text = CountedText("x" * 500000)
    monkeypatch.setattr(store, "MAX_CHUNKS", 3)
    with pytest.raises(ValueError, match="more than 3 chunks"):
        store.chunk_spans(text, 256, 255)
    assert text.searches == 6
    assert store.chunk_spans("x" * 258, 256, 255) == [(0,256),(1,257),(2,258)]


def test_chunk_settings_use_shared_service_and_require_agent_approval(uploaded):
    from types import SimpleNamespace
    from local_mode.services import ServiceRegistry
    registry = ServiceRegistry(SimpleNamespace(event=Mock()), lambda: None)
    args = {"chunk_size":512, "chunk_overlap":64}
    assert not registry.risky("embedding.settings", {})
    assert registry.risky("embedding.settings", args)
    with pytest.raises(ValueError, match="approval"):
        registry.execute("embedding.settings", args)
    assert registry.ui_call("embedding.settings", args)["chunk_size"] == 512
    assert registry.ui_call("embedding.settings", {})["chunk_overlap"] == 64
