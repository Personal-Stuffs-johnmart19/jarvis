"""Canonical local catalogue for explicitly uploaded files, text and embeddings."""

from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from local_mode.config import ROOT
from local_mode import embedding_jobs
from local_mode.messages import OwnedError, OwnedValueError
from local_mode.file_policy import checked_open, opened_path



@contextmanager
def connection():
    folder = ROOT / (
        ".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data"
    )
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "documents.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, name TEXT, path TEXT, text TEXT, size INTEGER, created REAL, embedding TEXT)"
    )
    # Chunks keep offsets and vectors only; source text stays in documents.
    db.execute(
        "CREATE TABLE IF NOT EXISTS chunks (document_id TEXT, idx INTEGER, start INTEGER, end INTEGER, provider TEXT, model TEXT, dimensions INTEGER, vector TEXT, PRIMARY KEY (document_id, idx))"
    )
    db.execute("CREATE TABLE IF NOT EXISTS document_sources (document_id TEXT PRIMARY KEY, provenance TEXT NOT NULL)")
    try:
        with db:
            yield db
    finally:
        db.close()


def ingest(path: str, name: str = "") -> dict:
    source = Path(path).resolve(strict=True)
    directory = (ROOT / ".cache/attachments").resolve()
    if (
        not directory.is_relative_to(ROOT.resolve())
        or not source.parent.samefile(directory)
        or not source.is_file()
    ):
        raise ValueError("Only files explicitly uploaded through Jarvis can be indexed")
    if not source.name.startswith("attachment-"):
        raise ValueError("Use the Jarvis file picker to import this file first")
    if len(name) > 255 or any(c in name for c in "\r\n/\\"):
        raise ValueError("Invalid document name")
    with checked_open(source) as stream:
        actual = opened_path(stream)
        if actual.parent != directory or not actual.name.startswith("attachment-"):
            raise OwnedValueError("Only files explicitly uploaded through Jarvis can be indexed")
        source = actual
        data = stream.read(12 * 1024 * 1024 + 1)
    if not data or len(data) > 12 * 1024 * 1024:
        raise ValueError("Document must contain 1 byte to 12 MiB")
    identifier = hashlib.sha256(data).hexdigest()
    with connection() as db:
        old = db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        if old:
            db.execute(
                "UPDATE documents SET path=? WHERE id=?", (str(source), identifier)
            )
            return metadata(
                db.execute(
                    "SELECT * FROM documents WHERE id=?", (identifier,)
                ).fetchone()
            )
    suffix = source.suffix.lower()
    if suffix in {".pdf", ".docx"}:
        from local_mode.documents import read_document_data

        text = read_document_data(source, data)
    elif suffix in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".mp3",
        ".wav",
        ".m4a",
        ".mp4",
        ".mov",
    }:
        text = ""  # Media uses the original file and a capable vision/audio model.
    else:
        text = data.decode("utf-8-sig")
        if "\0" in text or len(text) > 500000:
            raise ValueError("Text must be UTF-8, without NUL, up to 500000 characters")
    with connection() as db:
        db.execute(
            "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,NULL)",
            (
                identifier,
                name or source.name,
                str(source),
                text,
                len(data),
                time.time(),
            ),
        )
        return metadata(
            db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        )


CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
MAX_CHUNKS = 512


def chunk_spans(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """Deterministic (start, end) source-offset spans, each <= size, overlapping."""
    if size <= overlap or overlap < 0:
        raise ValueError("Chunk size must exceed overlap")
    spans, start = [], 0
    while start < len(text):
        if len(spans) >= MAX_CHUNKS:
            raise ValueError(f"Document needs more than {MAX_CHUNKS} chunks")
        end = min(start + size, len(text))
        if end < len(text):
            low = start + size - 200
            cut = max(text.rfind(" ", low, end), text.rfind("\n", low, end))
            if cut > start + overlap:
                end = cut + 1
        spans.append((start, end))
        if end >= len(text):
            break
        start = end - overlap
    return spans


def identity(result: dict) -> dict:
    """Provider/model/dimension identity; vectors with different identity never mix."""
    from local_mode import embeddings

    model = result["model"]
    provider = result.get("provider") or (
        "cloud" if model == embeddings.MODEL else "local"
    )
    return {"provider": provider, "model": model, "dimensions": len(result["vector"])}


def metadata(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "path": row["path"],
        "size": row["size"],
        "characters": len(row["text"]),
        "indexed": bool(row["embedding"]),
        "untrusted_content": True,
    }


def read(identifier: str, offset: int = 0, limit: int = 6000):
    with connection() as db:
        row = db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Uploaded document not found")
        end = offset + limit
        return {
            **metadata(row),
            "text": row["text"][offset:end],
            "next_offset": end if end < len(row["text"]) else None,
        }


def _citation(row, start, end, idx):
    text = row["text"]
    start, end = max(0, min(start, len(text))), max(0, min(end, len(text)))
    # PDF/DOCX extraction has text coordinates, not physical page/file lines.
    extracted = Path(row["path"]).suffix.lower() in {".pdf", ".docx"}
    saved_source = row["source_provenance"] if "source_provenance" in row.keys() else None
    source = json.loads(saved_source) if saved_source else {}
    return {
        **source,
        "path": row["path"],
        "coordinate_space": source.get("coordinate_space", ("extracted_text" if extracted else "text_file") if text else "file"),
        "line_start": text.count("\n", 0, start) + 1 if start < end else None,
        "line_end": text.count("\n", 0, max(start, end - 1)) + 1 if start < end else None,
        "document_id": row["id"],
        "name": row["name"],
        "chunk": idx,
        "start": start,
        "end": end,
    }


def search(
    query: str = "",
    limit: int = 10,
    semantic: bool = False,
    backend: str | None = None,
    auto_rebuild: bool = False,
):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError("Retrieval limit must be between 1 and 20")
    if not isinstance(query, str) or len(query) > 1000:
        raise ValueError("Query must contain at most 1000 characters")
    embedding_jobs.check()
    vector = None
    if semantic:
        if not query.strip():
            raise ValueError("Semantic search requires a query")
        from local_mode.embeddings import selected

        vector = selected(text=query, backend=backend)
    hits = []
    total = 0

    def record_hit(hit):
        nonlocal total
        total += 1
        # Keep only the requested top results; SQLite rows are streamed below.
        hits.append(hit)
        hits.sort(key=lambda item: item["score"], reverse=True)
        if len(hits) > limit:
            hits.pop()

    if vector:
        ident = identity(vector)
        query_vector = vector["vector"]
        best = {}
        with connection() as db:
            found = db.execute(
                "SELECT document_id, idx, start, end, vector FROM chunks WHERE provider=? AND model=? AND dimensions=?",
                (ident["provider"], ident["model"], ident["dimensions"]),
            )
            for chunk in found:
                embedding_jobs.check()
                values = json.loads(chunk["vector"])
                if len(values) != len(query_vector):
                    continue
                score = sum(a * b for a, b in zip(values, query_vector))
                current = best.get(chunk["document_id"])
                if current is None or score > current[0]:
                    best[chunk["document_id"]] = (
                        score,
                        chunk["start"],
                        chunk["end"],
                        chunk["idx"],
                    )
            for row in db.execute(
                "SELECT documents.*, document_sources.provenance AS source_provenance FROM documents "
                "LEFT JOIN document_sources ON documents.id=document_sources.document_id "
                "ORDER BY documents.created DESC"
            ):
                embedding_jobs.check()
                if row["id"] in best:
                    score, start, end, idx = best[row["id"]]
                    record_hit(
                        {
                            **metadata(row),
                            "score": score,
                            "offset": start,
                            "excerpt": row["text"][start:end],
                            "citation": _citation(row, start, end, idx),
                        }
                    )
                    continue
                # Legacy whole-document vector: usable only on matching provider/model/dimension.
                saved = json.loads(row["embedding"]) if row["embedding"] else None
                if (
                    saved
                    and "vector" in saved
                    and saved.get("provider", "cloud" if saved.get("model") == "gemini-embedding-2" else "local") == ident["provider"]
                    and saved.get("model") == ident["model"]
                    and len(saved["vector"]) == len(query_vector)
                ):
                    record_hit(
                        {
                            **metadata(row),
                            "score": sum(
                                a * b for a, b in zip(saved["vector"], query_vector)
                            ),
                            "offset": 0,
                            "excerpt": row["text"][:1200],
                            "citation": _citation(row, 0, min(1200, len(row["text"])), None),
                        }
                    )
    else:
        words = set(re.findall(r"\w+", query.casefold()))
        with connection() as db:
            for row in db.execute(
                "SELECT documents.*, document_sources.provenance AS source_provenance FROM documents "
                "LEFT JOIN document_sources ON documents.id=document_sources.document_id "
                "ORDER BY documents.created DESC"
            ):
                embedding_jobs.check()
                haystack = (row["name"] + " " + row["text"]).casefold()
                score = sum(haystack.count(word) for word in words) if words else 1
                if not score:
                    continue
                positions = [row["text"].casefold().find(w) for w in words]
                offset = max(0, min((p for p in positions if p >= 0), default=0) - 150)
                end = offset + 1200
                record_hit(
                    {
                        **metadata(row),
                        "score": score,
                        "offset": offset,
                        "excerpt": row["text"][offset:end],
                        "citation": _citation(row, offset, min(end, len(row["text"])), None),
                    }
                )
    embedding_jobs.check()
    response = {"items": hits, "total": total, "untrusted_content": True}
    if vector:
        with connection() as db:
            active_count = db.execute(
                "SELECT COUNT(*) FROM chunks WHERE provider=? AND model=? AND dimensions=?",
                (ident["provider"], ident["model"], ident["dimensions"]),
            ).fetchone()[0]
            total_indexed = db.execute(
                "SELECT COUNT(*) FROM documents WHERE embedding IS NOT NULL"
            ).fetchone()[0]
            if total_indexed > 0 and active_count == 0 and len(hits) == 0:
                if auto_rebuild:
                    rebuild_result = rebuild(backend=backend)
                    response["rebuilt"] = rebuild_result
                    # Re-run search now that index has been rebuilt under current identity
                    return search(
                        query=query,
                        limit=limit,
                        semantic=semantic,
                        backend=backend,
                        auto_rebuild=False,
                    )
                response["index_mismatch"] = True
                response["mismatch_details"] = {
                    "current": ident,
                    "indexed_documents": total_indexed,
                }
    return response


def _embed_batch(texts: list[str], backend: str | None = None) -> list[dict]:
    """Transport batching with intra-request duplicate dedup to avoid repetitive network roundtrips."""
    if not texts:
        return []
    from local_mode.embeddings import batch_selected

    unique_texts: list[str] = []
    index_map: dict[str, int] = {}
    for t in texts:
        if t not in index_map:
            index_map[t] = len(unique_texts)
            unique_texts.append(t)

    unique_results = batch_selected(unique_texts, backend=backend)
    return [unique_results[index_map[t]] for t in texts]


def embed(identifier: str, backend: str | None = None):
    """Chunk and embed a document; the old index is replaced only after all chunks succeed."""
    embedding_jobs.check()
    item = read(identifier, 0, 1)
    from local_mode.embeddings import selected
    from local_mode.local_embeddings import preferences
    config = preferences()
    chunking = {key: config[key] for key in ("chunk_size", "chunk_overlap")}

    from local_mode.rag_sources import provenance
    source = provenance(identifier)
    content = None
    # Explicit source imports are immutable local snapshots. Embedding uses that
    # reviewed text, never rereads a changed file or sends the entire chat JSON.
    if not source:
        with checked_open(item["path"]) as stream:
            content = stream.read(12 * 1024 * 1024 + 1)
        if hashlib.sha256(content).hexdigest() != identifier:
            raise OwnedValueError("Uploaded file changed; upload it again before indexing")
    with connection() as db:
        text = db.execute(
            "SELECT text FROM documents WHERE id=?", (identifier,)
        ).fetchone()["text"]
    if text.strip():
        spans = chunk_spans(text, chunking["chunk_size"], chunking["chunk_overlap"])
        chunk_texts = [text[s:e] for s, e in spans]
        results = _embed_batch(chunk_texts, backend=backend)
    else:
        # Media/no extracted text: one vector for the uploaded file, no text offsets.
        if content is None:
            raise OwnedValueError("Uploaded document has no content to embed")
        spans = [(0, 0)]
        from local_mode.embeddings import attachment_data
        results = [selected(path=item["path"], backend=backend,
                            file_part=attachment_data(Path(item["path"]), content))]
    ident = None
    for result in results:
        values = result["vector"]
        if not values or any(not math.isfinite(v) for v in values):
            raise ValueError("Embedding returned an invalid vector")
        current = identity(result)
        if ident is not None and current != ident:
            raise ValueError("Embedding identity changed during indexing; retry")
        ident = current
    with connection() as db:
        # Acquire the writer lock before comparing: an approved reimport or
        # deletion during embedding must not attach old vectors to new text.
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT text FROM documents WHERE id=?", (identifier,)).fetchone()
        if current is None or current["text"] != text:
            raise ValueError("Source changed during indexing; retry")
        embedding_jobs.check()
        db.execute("DELETE FROM chunks WHERE document_id=?", (identifier,))
        db.executemany(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    identifier,
                    index,
                    start,
                    end,
                    ident["provider"],
                    ident["model"],
                    ident["dimensions"],
                    json.dumps(result["vector"]),
                )
                for index, ((start, end), result) in enumerate(zip(spans, results))
            ],
        )
        db.execute(
            "UPDATE documents SET embedding=? WHERE id=?",
            (json.dumps({**ident, **chunking, "chunks": len(spans), "version": 3}), identifier),
        )
        embedding_jobs.check()
    summary = {
        **ident,
        "document_id": identifier,
        "indexed": True,
        "chunks": len(spans),
        "input_tokens": sum(r.get("input_tokens") or 0 for r in results),
    }
    reasons = [r["fallback_reason"] for r in results if r.get("fallback_reason")]
    if reasons:
        summary["fallback_reason"] = reasons[0]
    return summary


def rebuild(backend: str | None = None, identifier: str | None = None):
    """Re-embed indexed documents under the current identity; documents are kept."""
    with connection() as db:
        ids = [
            row["id"]
            for row in db.execute(
                "SELECT id FROM documents WHERE embedding IS NOT NULL"
                + (" AND id=?" if identifier else ""),
                (identifier,) if identifier else (),
            )
        ]
    rebuilt, failed = [], []
    for item in ids:
        try:
            embedding_jobs.check()
            rebuilt.append(embed(item, backend))
        except embedding_jobs.Cancelled:
            return {"rebuilt": rebuilt, "failed": failed, "cancelled": True}
        except Exception as error:  # Old index remains for a failed document.
            if embedding_jobs.event() is not None and embedding_jobs.event().is_set():
                return {"rebuilt": rebuilt, "failed": failed, "cancelled": True}
            failed.append({"document_id": item, "error": error.payload() if isinstance(error, OwnedError) else str(error)[:200]})
    return {"rebuilt": rebuilt, "failed": failed}


def remove(identifier: str):
    """Remove the index only; existing chat references keep their source file."""
    with connection() as db:
        db.execute("DELETE FROM chunks WHERE document_id=?", (identifier,))
        db.execute("DELETE FROM document_sources WHERE document_id=?", (identifier,))
        result = db.execute("DELETE FROM documents WHERE id=?", (identifier,))
        return {
            "ok": True,
            "removed": bool(result.rowcount),
            "source_file_deleted": False,
        }
