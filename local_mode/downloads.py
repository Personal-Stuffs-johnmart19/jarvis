"""Проверяемая атомарная загрузка локальных моделей с закреплённых URL."""
from __future__ import annotations
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
import requests
import tempfile
from threading import Lock
from typing import Literal

_PUBLICATION_LOCK = Lock()


def check_cancelled(cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Download cancelled")


@contextmanager
def cancellable_lock(lock, cancel_event=None):
    check_cancelled(cancel_event)
    if cancel_event is None:
        lock.acquire()
    else:
        while not lock.acquire(timeout=.05):
            check_cancelled(cancel_event)
    try:
        check_cancelled(cancel_event)
        yield
    finally:
        lock.release()


def _validate_content(path: Path, content_format: str | None) -> None:
    """Check explicitly named companion formats, without claiming publisher integrity."""
    if content_format is None:
        return
    text = path.read_text(encoding="utf-8")
    if not text.strip() or "\x00" in text:
        raise ValueError("Downloaded companion must contain nonempty UTF-8 text.")
    if content_format == "json-object" and not isinstance(json.loads(text), dict):
        raise ValueError("Downloaded companion must contain a JSON object.")


def download_file(url: str, target: Path, *, max_bytes: int, sha256: str | None = None, cancel_event=None,
                  content_format: Literal["json-object", "utf8-text"] | None = None) -> None:
    if content_format not in (None, "json-object", "utf8-text"):
        raise ValueError("Unsupported downloaded companion format.")
    with cancellable_lock(_PUBLICATION_LOCK, cancel_event):
        if target.is_file():
            valid_content = True
            if content_format is not None and 0 < target.stat().st_size <= max_bytes:
                try:
                    _validate_content(target, content_format)
                except (ValueError, UnicodeError):
                    valid_content = False
            if sha256 is None and 0 < target.stat().st_size <= max_bytes and valid_content:
                return
            with target.open("rb") as existing:
                digest = hashlib.sha256()
                size = 0
                while chunk := existing.read(64 * 1024):
                    check_cancelled(cancel_event)
                    size += len(chunk)
                    if size > max_bytes:
                        break
                    digest.update(chunk)
                if 0 < size <= max_bytes and valid_content and (sha256 is None or digest.hexdigest() == sha256):
                    return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        digest = hashlib.sha256()
        check_cancelled(cancel_event)
        timeout = (3, 1) if cancel_event is not None else (15, 180)
        with requests.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            size = 0
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, prefix=target.name + ".", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                def chunks():
                    # read1 yields available data without waiting to fill 64KiB;
                    # a slowly trickling peer cannot defer cancellation forever.
                    reader = getattr(getattr(response, "raw", None), "read1", None)
                    if cancel_event is not None and reader is not None:
                        while True:
                            check_cancelled(cancel_event)
                            chunk = reader(64 * 1024, decode_content=True)
                            if not chunk:
                                return
                            yield chunk
                    else:
                        yield from response.iter_content(64 * 1024)
                for chunk in chunks():
                    check_cancelled(cancel_event)
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Загрузка модели превысила допустимый размер.")
                    digest.update(chunk)
                    output.write(chunk)
        if size == 0:
            raise ValueError("Downloaded model or companion is empty.")
        if sha256 and digest.hexdigest() != sha256:
            raise ValueError("Не совпала SHA256 загруженной модели.")
        check_cancelled(cancel_event)
        _validate_content(temporary, content_format)
        with cancellable_lock(_PUBLICATION_LOCK, cancel_event):
            temporary.replace(target)
    except Exception:
        check_cancelled(cancel_event)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
