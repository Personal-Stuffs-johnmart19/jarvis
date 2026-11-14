"""Chunked document RAG with a mocked embedding transport (no model calls)."""

import json

import pytest

from local_mode import document_store as store


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "ROOT", tmp_path)
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    folder = tmp_path / ".cache/attachments"
    folder.mkdir(parents=True)
    state = {"model": "m1", "dims": 3, "provider": "local", "calls": []}

    def fake(text="", path="", backend=None):
        state["calls"].append(text)
        low = text.casefold()
        vector = [float("alpha" in low), float("omega" in low), 0.1]
        vector += [0.0] * (state["dims"] - 3)
        return {
            "provider": state.get("provider", "local"),
            "model": state["model"],
            "vector": vector[: state["dims"]],
        }

    from local_mode import embeddings
    monkeypatch.setattr("local_mode.embeddings.selected", fake)
    monkeypatch.setattr(
        "local_mode.embeddings.batch_selected",
        lambda texts, backend=None: [embeddings.selected(text=t, backend=backend) for t in texts],
    )

    def upload(text):
        path = folder / f"attachment-{abs(hash(text))}.txt"
        path.write_text(text, encoding="utf-8")
        return store.ingest(str(path))

    return state, upload


def test_chunk_spans_bounded_overlapping_deterministic():
    text = ("word " * 2000) + "tail"
    spans = store.chunk_spans(text)
    assert spans == store.chunk_spans(text)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (a, b), (c, d) in zip(spans, spans[1:]):
        assert b - a <= store.CHUNK_SIZE and c < b and c > a
    assert store.chunk_spans("") == []


def test_long_document_searchable_beyond_12k(env):
    state, upload = env
    text = "lorem ipsum dolor " * 1500 + " omega marker " + "sit amet " * 500
    position = text.index("omega")
    item = upload(text)
    result = store.embed(item["id"])
    assert result["chunks"] > 10
    assert max(len(c) for c in state["calls"]) <= store.CHUNK_SIZE
    hit = store.search("omega", semantic=True)["items"][0]
    citation = hit["citation"]
    assert citation["start"] <= position < citation["end"]
    assert "omega" in hit["excerpt"] and hit["offset"] == citation["start"]


def test_no_full_document_vector_or_text_duplicate(env):
    _, upload = env
    item = upload("alpha " * 1000)
    store.embed(item["id"])
    with store.connection() as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(chunks)")]
        saved = db.execute("SELECT embedding FROM documents").fetchone()[0]
    assert "text" not in columns
    assert "vector" not in json.loads(saved)


def test_identity_change_never_mixes_and_rebuild_keeps_documents(env):
    state, upload = env
    item = upload("alpha " * 600)
    store.embed(item["id"])
    state["model"], state["dims"] = "m2", 4
    assert store.search("alpha", semantic=True)["items"] == []
    outcome = store.rebuild()
    assert outcome["failed"] == [] and outcome["rebuilt"][0]["dimensions"] == 4
    assert store.search("alpha", semantic=True)["total"] == 1
    with store.connection() as db:
        assert {r[0] for r in db.execute("SELECT model FROM chunks")} == {"m2"}
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_failed_reembed_keeps_old_index(env, monkeypatch):
    state, upload = env
    item = upload("alpha " * 600)
    store.embed(item["id"])

    def boom(**_):
        raise RuntimeError("transport down")

    monkeypatch.setattr("local_mode.embeddings.selected", boom)
    with pytest.raises(RuntimeError):
        store.embed(item["id"])
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] > 0


def test_legacy_whole_document_vector_still_searchable(env):
    _, upload = env
    item = upload("alpha legacy")
    with store.connection() as db:
        db.execute(
            "UPDATE documents SET embedding=? WHERE id=?",
            (json.dumps({"provider": "local", "model": "m1", "vector": [1.0, 0.0, 0.1]}), item["id"]),
        )
    result = store.search("alpha", semantic=True)
    assert result["total"] == 1
    citation = result["items"][0]["citation"]
    assert citation["path"] == item["path"]
    assert (citation["line_start"], citation["line_end"]) == (1, 1)
    assert citation["end"] == len("alpha legacy")


def test_legacy_whole_document_provider_mismatch_ignored(env):
    state, upload = env
    item = upload("alpha legacy cloud")
    with store.connection() as db:
        # Saved under cloud provider
        db.execute(
            "UPDATE documents SET embedding=? WHERE id=?",
            (json.dumps({"provider": "cloud", "model": "m1", "vector": [1.0, 0.0, 0.1]}), item["id"]),
        )
    # Search is currently under local provider
    res = store.search("alpha", semantic=True)
    assert res["items"] == []


def test_embedding_batch_and_cache_reduces_transport_calls(env):
    state, _ = env
    repeated = ["repeated chunk", "unique chunk", "repeated chunk"]
    state["calls"].clear()
    results = store._embed_batch(repeated)
    assert len(results) == 3
    assert results[0]["vector"] == results[2]["vector"]
    # Intra-batch deduplication ensures unique chunks embedded is fewer than total chunks
    assert len(state["calls"]) == 2
    assert state["calls"] == ["repeated chunk", "unique chunk"]

    # A new query legitimately makes one transport call (no unsafe cross-request cache)
    state["calls"].clear()
    store.search("repeated chunk", semantic=True)
    assert len(state["calls"]) == 1


def test_search_exposes_index_mismatch_and_safe_auto_rebuild(env):
    state, upload = env
    item = upload("alpha unique content " * 100)
    store.embed(item["id"])
    
    # Change active model identity
    state["model"], state["dims"] = "new-model", 4
    
    # Normal search alerts mismatch instead of silently returning empty results
    mismatch_res = store.search("alpha", semantic=True)
    assert mismatch_res["items"] == []
    assert mismatch_res.get("index_mismatch") is True
    assert mismatch_res["mismatch_details"]["indexed_documents"] >= 1
    
    # With auto_rebuild=True, safe rebuild is executed and matching results returned
    auto_res = store.search("alpha", semantic=True, auto_rebuild=True)
    assert auto_res["total"] >= 1
    assert auto_res.get("index_mismatch") is not True


def test_cloud_batch_sdk_transport_call_counts_and_content_shape(monkeypatch):
    from contextlib import contextmanager
    from google.genai import types
    from local_mode import embeddings, gemini

    class MockItem:
        def __init__(self, dims=768):
            self.values = [0.05] * dims

    class MockEmbedResponse:
        def __init__(self, count):
            self.embeddings = [MockItem() for _ in range(count)]

    transport_calls = []

    class MockClient:
        def __init__(self):
            self.models = self

        def embed_content(self, model, contents, config=None):
            transport_calls.append({"model": model, "contents": contents, "config": config})
            return MockEmbedResponse(len(contents))

    client = MockClient()

    @contextmanager
    def fake_create_client(**kwargs):
        yield client

    monkeypatch.setattr(gemini, "create_client", fake_create_client)

    sample_texts = ["first text snippet", "second text snippet", "third text snippet"]
    results = embeddings.batch_embed(sample_texts)

    # Real SDK transport call count must be 1 for the whole batch
    assert len(transport_calls) == 1
    call = transport_calls[0]
    assert call["model"] == embeddings.MODEL

    # Verify content shape: list of types.Content objects each with parts
    contents = call["contents"]
    assert isinstance(contents, list) and len(contents) == 3
    for idx, expected in enumerate(sample_texts):
        c = contents[idx]
        assert isinstance(c, types.Content)
        assert len(c.parts) == 1
        assert c.parts[0].text == expected

    # Verify result count, dimensions and normalized vector
    assert len(results) == 3
    for r in results:
        assert r["model"] == embeddings.MODEL
        assert r["dimensions"] == 768
        assert len(r["vector"]) == 768
        norm = sum(v * v for v in r["vector"])
        assert abs(norm - 1.0) < 1e-4


def test_cloud_batch_bounded_batch_splitting(monkeypatch):
    from contextlib import contextmanager
    from local_mode import embeddings, gemini

    class MockItem:
        def __init__(self, dims=768):
            self.values = [0.05] * dims

    class MockEmbedResponse:
        def __init__(self, count):
            self.embeddings = [MockItem() for _ in range(count)]

    transport_calls = []

    class MockClient:
        def __init__(self):
            self.models = self

        def embed_content(self, model, contents, config=None):
            transport_calls.append(len(contents))
            return MockEmbedResponse(len(contents))

    client = MockClient()

    @contextmanager
    def fake_create_client(**kwargs):
        yield client

    monkeypatch.setattr(gemini, "create_client", fake_create_client)

    # 100 items with batch size 64 should produce 2 calls: 64 and 36
    texts = [f"item {i}" for i in range(100)]
    results = embeddings.batch_embed(texts)
    assert transport_calls == [64, 36]
    assert len(results) == 100


def test_cloud_batch_validates_dimensions_and_finiteness(monkeypatch):
    from contextlib import contextmanager
    from local_mode import embeddings, gemini

    class BadItem:
        def __init__(self, values):
            self.values = values

    class MockEmbedResponse:
        def __init__(self, values_list):
            self.embeddings = [BadItem(vals) for vals in values_list]

    response_holder = []

    class MockClient:
        def __init__(self):
            self.models = self

        def embed_content(self, model, contents, config=None):
            return response_holder[0]

    client = MockClient()

    @contextmanager
    def fake_create_client(**kwargs):
        yield client

    monkeypatch.setattr(gemini, "create_client", fake_create_client)

    # Test wrong vector length
    response_holder.append(MockEmbedResponse([[0.1] * 10]))
    with pytest.raises(ValueError, match="768-dimensional"):
        embeddings.batch_embed(["test"])

    # Test non-finite value (nan/inf)
    response_holder[0] = MockEmbedResponse([[float("nan")] + [0.1] * 767])
    with pytest.raises(ValueError, match="768-dimensional"):
        embeddings.batch_embed(["test"])

    # Test wrong count of vectors returned
    response_holder[0] = MockEmbedResponse([])
    with pytest.raises(ValueError, match="expected"):
        embeddings.batch_embed(["test"])


def test_cloud_batch_exact_token_totals(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from local_mode import embeddings, gemini

    class MockItem:
        def __init__(self, dims=768):
            self.values = [0.05] * dims

    class MockEmbedResponse:
        def __init__(self, count, prompt_tokens):
            self.embeddings = [MockItem() for _ in range(count)]
            self.usage_metadata = SimpleNamespace(prompt_token_count=prompt_tokens)

    class MockClient:
        def __init__(self):
            self.models = self

        def embed_content(self, model, contents, config=None):
            return MockEmbedResponse(len(contents), 7)

    client = MockClient()

    @contextmanager
    def fake_create_client(**kwargs):
        yield client

    monkeypatch.setattr(gemini, "create_client", fake_create_client)

    results = embeddings.batch_embed(["text1", "text2", "text3"])
    tokens = [r["input_tokens"] for r in results]
    assert tokens == [3, 2, 2]
    assert sum(tokens) == 7


def test_documents_rebuild_service_registration_and_offline_execution(env):
    state, upload = env
    from types import SimpleNamespace
    from unittest.mock import Mock
    from local_mode.services import ServiceRegistry

    item = upload("alpha content for service rebuild test " * 20)
    store.embed(item["id"])

    runtime = SimpleNamespace(suggestions_path="", event=Mock())
    registry = ServiceRegistry(runtime, lambda: None)

    # Service registration and descriptor checks
    desc = next(d for d in registry.all_descriptors() if d["name"] == "documents.rebuild")
    assert desc["approval"] == "required"
    assert desc["category"] == "documents"

    # Approval enforcement
    with pytest.raises(ValueError, match="approval"):
        registry.execute("documents.rebuild", {})

    # Offline execution with approval
    state["model"], state["dims"] = "m_rebuilt", 5
    res = registry.execute("documents.rebuild", {}, approved=True)
    assert len(res["rebuilt"]) == 1
    assert res["failed"] == []
    assert res["rebuilt"][0]["dimensions"] == 5
    assert store.search("alpha", semantic=True)["total"] == 1



def test_citations_keep_exact_source_and_exclusive_end_line_coordinates():
    row = {"id": "doc", "name": "sample", "path": "C:/uploads/sample.txt", "text": "first\nsecond\nthird"}
    citation = store._citation(row, 6, 13, 0)
    assert citation["path"] == row["path"]
    assert (citation["start"], citation["end"]) == (6, 13)
    assert (citation["line_start"], citation["line_end"]) == (2, 2)
    assert citation["coordinate_space"] == "text_file"
    assert store._citation(row, 0, len(row["text"]), None)["line_end"] == 3
    assert store._citation(row, 0, 0, None)["line_start"] is None
    row["path"] = "C:/uploads/scan.pdf"
    assert store._citation(row, 0, 5, None)["coordinate_space"] == "extracted_text"


def test_approved_rebuild_failure_keeps_previous_index_and_source(env, monkeypatch):
    state, upload = env
    item = upload("alpha preserved source")
    store.embed(item["id"])
    with store.connection() as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM chunks WHERE document_id=?", (item["id"],))]
    def fail(*args, **kwargs):
        raise RuntimeError("offline provider failure")
    monkeypatch.setattr(store, "_embed_batch", fail)
    from local_mode.services import ServiceRegistry
    from unittest.mock import Mock
    registry = ServiceRegistry(Mock(), lambda: Mock())
    result = registry.execute("documents.rebuild", {"identifier": item["id"], "backend": "local"}, approved=True)
    assert result["rebuilt"] == []
    assert result["failed"] == [{"document_id": item["id"], "error": "offline provider failure"}]
    with store.connection() as db:
        after = [tuple(row) for row in db.execute("SELECT * FROM chunks WHERE document_id=?", (item["id"],))]
    assert after == before
    assert store.search("alpha", semantic=True)["total"] == 1
    assert store.read(item["id"])["text"] == "alpha preserved source"
