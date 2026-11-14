"""Release regressions using disposable stores and deterministic embeddings only."""
import json

import pytest

from local_mode import document_store as store, embedding_jobs, embeddings, local_embeddings


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    monkeypatch.setattr(local_embeddings, "preferences", lambda: {
        "backend": "local", "chunk_size": 256, "chunk_overlap": 20,
    })
    monkeypatch.setattr(embeddings, "selected", lambda **kwargs: {
        "provider": "local", "model": "fixture", "vector": [1.0, 0.0],
    })
    monkeypatch.setattr(embeddings, "batch_selected", lambda texts, **kwargs: [
        {"provider": "local", "model": "fixture", "vector": [1.0, 0.0]} for _ in texts
    ])
    return tmp_path


@pytest.mark.parametrize("semantic", [False, True])
def test_ranking_considers_older_documents_and_keeps_winning_citation(isolated, semantic):
    text = "heading\nneedle needle needle\n"
    summary = json.dumps({"provider": "local", "model": "fixture", "chunks": 1})
    with store.connection() as db:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (
            "old-winner", "old.txt", str(isolated / "old.txt"), text, len(text), 0, summary,
        ))
        db.execute("INSERT INTO document_sources VALUES (?,?)", (
            "old-winner", json.dumps({"source_kind": "workspace", "coordinate_space": "text_file"}),
        ))
        db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)", (
            "old-winner", 0, 8, len(text), "local", "fixture", 2, "[1.0, 0.0]",
        ))
        db.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", [
            (f"recent-{i}", "recent.txt", str(isolated / f"recent-{i}.txt"),
             "needle", 6, i + 1, summary) for i in range(1000)
        ])
        db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)", [
            (f"recent-{i}", 0, 0, 6, "local", "fixture", 2, "[0.0, 1.0]")
            for i in range(1000)
        ])
    result = store.search("needle", limit=2, semantic=semantic)
    assert result["total"] == 1001
    hit = result["items"][0]
    assert hit["id"] == "old-winner"
    citation = hit["citation"]
    assert citation["source_kind"] == "workspace"
    assert citation["path"] == str(isolated / "old.txt")
    assert hit["excerpt"] == text[citation["start"]:citation["end"]]
    if semantic:
        assert (citation["line_start"], citation["line_end"]) == (2, 2)


@pytest.mark.parametrize("semantic", [False, True])
def test_cancelled_query_never_starts_embedding_transport(isolated, monkeypatch, semantic):
    def unexpected(**kwargs):
        pytest.fail("Cancelled query started embedding transport")
    monkeypatch.setattr(embeddings, "selected", unexpected)
    with embedding_jobs.operation():
        embedding_jobs.event().set()
        with pytest.raises(embedding_jobs.Cancelled):
            store.search("needle", semantic=semantic)


@pytest.mark.parametrize("cancel", [False, True])
def test_inflight_reindex_failure_preserves_exact_old_vectors(isolated, monkeypatch, cancel):
    folder = isolated / ".cache/attachments"
    folder.mkdir(parents=True)
    path = folder / "attachment-fixture.txt"
    path.write_text("needle " * 100, encoding="utf-8")
    item = store.ingest(str(path))
    store.embed(item["id"])
    def snapshot():
        with store.connection() as db:
            return (db.execute("SELECT embedding FROM documents WHERE id=?", (item["id"],)).fetchone()[0],
                    [tuple(row) for row in db.execute("SELECT * FROM chunks ORDER BY idx")])
    before = snapshot()
    before_hit = store.search("needle", semantic=True)["items"][0]
    def interrupted(texts, **kwargs):
        if cancel:
            embedding_jobs.event().set()
            return [{"provider": "local", "model": "replacement", "vector": [0.0, 1.0]} for _ in texts]
        raise RuntimeError("fixture transport disconnected")
    monkeypatch.setattr(embeddings, "batch_selected", interrupted)
    with embedding_jobs.operation():
        with pytest.raises(embedding_jobs.Cancelled if cancel else RuntimeError):
            store.embed(item["id"])
    assert snapshot() == before
    assert store.search("needle", semantic=True)["items"][0] == before_hit

@pytest.mark.parametrize("semantic", [False, True])
def test_cancellation_during_ranking_does_not_publish_partial_hits(isolated, monkeypatch, semantic):
    with store.connection() as db:
        db.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", [
            (str(i), "needle.txt", str(isolated / f"{i}.txt"), "needle", 6, i,
             json.dumps({"provider": "local", "model": "fixture", "vector": [1.0, 0.0]}))
            for i in range(2)
        ])
    original = store._citation
    visits = []
    def cancel_during_citation(*args):
        visits.append(args[0]["id"])
        embedding_jobs.event().set()
        return original(*args)
    monkeypatch.setattr(store, "_citation", cancel_during_citation)
    with embedding_jobs.operation():
        with pytest.raises(embedding_jobs.Cancelled):
            store.search("needle", semantic=semantic)
    assert visits == ["1"]
