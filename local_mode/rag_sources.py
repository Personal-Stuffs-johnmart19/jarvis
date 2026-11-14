"""Approved, bounded snapshots from real workspace files and persisted chat turns."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import time
from local_mode.messages import OwnedValueError

MAX_TEXT = 500000
MAX_BYTES = 2 * 1024 * 1024


def _safe_file(path: Path, root: Path) -> Path:
    from local_mode.file_policy import check_readable
    try:
        check_readable(path)
        source = path.resolve(strict=True)
        check_readable(source)
    except ValueError as error:
        raise ValueError('Credentials, configuration and private keys cannot be indexed') from error
    root = root.resolve(strict=True)
    if not root.is_dir() or not source.is_relative_to(root) or not source.is_file():
        raise ValueError("Source must be a file inside the selected workspace")
    blocked = {".git", ".ssh", ".aws", ".gnupg", ".codex", "credentials"}
    names = {"api_keys.json", "google_token.json", "bootstrap-key.json", "bridge_token.txt", "bridge_token.json", "network_preferences.json", "proxy_cache.json", "id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc", ".git-credentials"}
    if (any(part.casefold() in blocked for part in source.parts)
            or source.name.casefold().startswith(".env") or source.name.casefold() in names
            or source.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"}):
        raise ValueError("Credentials, configuration and private keys cannot be indexed")
    return source


def _opened_path(stream, selected: Path) -> Path:
    from local_mode.file_policy import opened_path
    return opened_path(stream, selected)


def _read(path: Path, root: Path) -> bytes:
    from local_mode.file_policy import checked_open
    with checked_open(path) as stream:
        opened = os.fstat(stream.fileno())
        actual = _opened_path(stream, path)
        validated = _safe_file(actual, root)
        if validated != path or not os.path.samestat(opened, validated.stat()):
            raise ValueError("Opened source differs from selected file")
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("Source exceeds 2 MiB")
    return data


def _snapshot(text: str, provenance: dict, name: str, *, expected: tuple | None = None) -> dict:
    from local_mode import document_store as store
    if not isinstance(text, str) or not text.strip() or "\0" in text or len(text) > MAX_TEXT:
        raise ValueError("Source must contain 1 to 500000 text characters without NUL")
    raw = text.encode("utf-8")
    identity = dict(provenance)
    terminal = identity.get("source_kind") == "terminal"
    if terminal:
        identity.pop("source_truncated", None)
    identifier = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()
    legacy_ids = [hashlib.sha256(json.dumps({**identity, "source_truncated": truncated}, sort_keys=True).encode("utf-8")).hexdigest() for truncated in (False, True)] if terminal else []
    if expected is not None:
        identifier = expected[0]
    provenance = {**provenance, "content_hash": hashlib.sha256(raw).hexdigest()}
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if terminal and expected is None:
            # Reuse older IDs without deleting historical citations or creating a
            # second document when only truncation metadata changes. Fixed ordering
            # keeps the canonical choice stable even after timestamps change.
            existing = db.execute("SELECT id FROM documents WHERE id IN (?,?,?) ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, id LIMIT 1", (identifier, *legacy_ids, identifier)).fetchone()
            if existing is not None:
                identifier = existing["id"]
        old = db.execute("SELECT documents.text, document_sources.provenance FROM documents LEFT JOIN document_sources ON documents.id=document_sources.document_id WHERE documents.id=?", (identifier,)).fetchone()
        if expected is not None and (old is None or old["text"] != expected[1] or old["provenance"] != expected[2]):
            raise OwnedValueError("RAG source changed while refreshing; retry.")
        if old is None:
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,NULL)",
                       (identifier, name, provenance.get("path", ""), text, len(raw), time.time()))
        elif old["text"] != text:
            db.execute("DELETE FROM chunks WHERE document_id=?", (identifier,))
            db.execute("UPDATE documents SET text=?, size=?, created=?, embedding=NULL WHERE id=?",
                       (text, len(raw), time.time(), identifier))
        db.execute("INSERT OR REPLACE INTO document_sources VALUES (?,?)", (identifier, json.dumps(provenance)))
        row = db.execute("SELECT * FROM documents WHERE id=?", (identifier,)).fetchone()
        result = {**store.metadata(row), "source": provenance}
        if expected is not None:
            result.update(refreshed=True, changed=old["text"] != text)
        return result


def _workspace_snapshot(path: str, workspace: str, name: str = "", *, expected_path: str | None = None) -> tuple:
    """Read a single explicit workspace file locally; do not traverse directories."""
    root = Path(workspace).expanduser().resolve(strict=True)
    candidate = Path(path).expanduser()
    source = _safe_file(candidate if candidate.is_absolute() else root / candidate, root)
    if name and (len(name) > 255 or any(c in name for c in "\r\n/\\")):
        raise ValueError("Invalid document name")
    if expected_path is not None and (source != Path(expected_path) or root != Path(workspace)):
        raise OwnedValueError("RAG source location changed; import it again.")
    text = _read(source, root).decode("utf-8-sig")
    return text, {"source_kind": "workspace", "path": str(source), "workspace": str(root)}, name or source.name


def ingest_workspace(path: str, workspace: str, name: str = "") -> dict:
    return _snapshot(*_workspace_snapshot(path, workspace, name))


def _conversations_root() -> Path:
    # Matches Tauri's com.mrcrous.jarvis app_data_dir on Windows. The model
    # cannot provide an arbitrary chat storage directory or manufacture turns.
    base = os.environ.get("APPDATA")
    if os.name != "nt" or not base:
        raise ValueError("Saved desktop chat retrieval is available on Windows only")
    identifier = "com.mrcrous.jarvis.qa" if "JARVIS_DESKTOP_TEST" in os.environ else "com.mrcrous.jarvis"
    return Path(base) / identifier / "conversations"


def _chat_snapshot(session_id: str, turn_id: str, *, expected_path: str | None = None) -> tuple:
    """Read exact persisted SDK text parts for one real native chat message ID."""
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise ValueError("Invalid saved chat session ID")
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 255:
        raise ValueError("Invalid saved chat turn ID")
    root = _conversations_root().resolve(strict=True)
    path = _safe_file(root / f"session-{session_id}.json", root)
    if expected_path is not None and path != Path(expected_path):
        raise OwnedValueError("RAG source location changed; import it again.")
    messages = json.loads(_read(path, root))
    if not isinstance(messages, list):
        raise ValueError("Saved conversation must contain a message array")
    matches = [m for m in messages if isinstance(m, dict) and m.get("id") == turn_id]
    if len(matches) != 1:
        raise ValueError("Saved chat turn ID must identify exactly one message")
    message = matches[0]
    if message.get("role") not in {"user", "assistant"}:
        raise ValueError("Only user and assistant chat messages can be indexed")
    parts = message.get("parts")
    if not isinstance(parts, list):
        raise ValueError("Saved chat turn has no text parts")
    texts = [p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)]
    text = "\n".join(texts)
    return text, {"source_kind": "chat", "session_id": session_id, "turn_id": turn_id,
                  "path": str(path), "coordinate_space": "chat_text_parts"}, f"Chat {session_id} / {turn_id}"


def ingest_chat(session_id: str, turn_id: str) -> dict:
    return _snapshot(*_chat_snapshot(session_id, turn_id))


def provenance(identifier: str) -> dict:
    from local_mode import document_store as store
    with store.connection() as db:
        row = db.execute("SELECT provenance FROM document_sources WHERE document_id=?", (identifier,)).fetchone()
    return json.loads(row[0]) if row else {}


def refresh(identifier: str) -> dict:
    """Refresh only one approved saved source; never embed or scan new sources."""
    from local_mode import document_store as store
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise OwnedValueError("Invalid RAG document ID.")
    with store.connection() as db:
        row = db.execute("SELECT documents.text, documents.name, document_sources.provenance FROM documents JOIN document_sources ON documents.id=document_sources.document_id WHERE documents.id=?", (identifier,)).fetchone()
    if row is None:
        raise OwnedValueError("This document has no refreshable saved source.")
    source = json.loads(row["provenance"])
    kind = source.get("source_kind")
    if kind == "workspace":
        loaded = _workspace_snapshot(source["path"], source["workspace"], row["name"], expected_path=source["path"])
    elif kind == "chat":
        loaded = _chat_snapshot(source["session_id"], source["turn_id"], expected_path=source["path"])
    elif kind == "terminal":
        from local_mode.terminal_sources import _terminal_snapshot
        loaded = _terminal_snapshot(source["session_id"], source["task_id"], source["stream"], expected_path=source["path"])
    else:
        raise OwnedValueError("This document has no refreshable saved source.")
    text, current, _ = loaded
    # Metadata such as truncation may change; the approved source identity may not.
    keys = ("source_kind", "path", "workspace", "session_id", "turn_id", "task_id", "stream", "tool_name")
    if any(source.get(key) != current.get(key) for key in keys):
        raise OwnedValueError("RAG source identity changed; import it again.")
    return _snapshot(text, current, row["name"], expected=(identifier, row["text"], row["provenance"]))
