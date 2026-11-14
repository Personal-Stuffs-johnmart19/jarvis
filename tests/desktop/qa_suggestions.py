"""Preserve owned QA quick-suggestion bytes and built-in/default provenance."""
from contextlib import contextmanager
import json
from pathlib import Path


@contextmanager
def preserve_qa_suggestions(root, expected_items):
    root = Path(root).resolve()
    qa = root / ".cache" / "desktop-test"
    owned_qa = qa.resolve()
    if owned_qa != qa or not owned_qa.is_relative_to(root):
        raise ValueError("QA suggestions must stay inside the repository QA root")
    qa.mkdir(parents=True, exist_ok=True)
    path = qa / "quick-suggestions.json"
    lock = qa / ".provider-suggestions.lock"
    for candidate in (path, lock):
        if candidate.is_symlink() or candidate.resolve().parent != owned_qa:
            raise ValueError("QA suggestion paths must not redirect outside their owned root")
    # Same backup/lock as provider QA; failed acquisition never removes its lock.
    handle = lock.open("xb")
    try:
        with handle:
            original = path.read_bytes() if path.exists() else None
            try:
                yield path
            finally:
                if qa.resolve() != owned_qa or path.is_symlink() or path.resolve().parent != owned_qa:
                    raise ValueError("QA suggestion restore path changed; current file was preserved")
                current = path.read_bytes() if path.exists() else None
                if current != original:
                    try:
                        owned = json.loads(current) == {"version": 1, "items": expected_items}
                    except (TypeError, ValueError):
                        owned = False
                    if not owned:
                        raise RuntimeError("QA suggestions changed outside this fixture; current file was preserved")
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)
    finally:
        if qa.resolve() == owned_qa and not lock.is_symlink() and lock.resolve().parent == owned_qa:
            lock.unlink()