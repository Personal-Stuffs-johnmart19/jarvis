"""Exact QA suggestion provenance restoration; no host or provider imports."""
import json

import pytest

from tests.desktop.qa_suggestions import preserve_qa_suggestions


ITEMS = [{"label": "SUGGESTIONS_SERVICE_QA", "prompt": "Show capabilities"}]


@pytest.mark.parametrize("original", [None, '{ "version":1, "items":[{"label":"Заметка","prompt":"Мій текст"}] }\r\n'.encode("utf-8")])
def test_suggestion_fixture_restores_original_bytes_or_absence_even_on_failure(tmp_path, original):
    qa = tmp_path / ".cache" / "desktop-test"
    qa.mkdir(parents=True)
    path = qa / "quick-suggestions.json"
    if original is not None:
        path.write_bytes(original)
    with pytest.raises(RuntimeError, match="owned scenario failed"):
        with preserve_qa_suggestions(tmp_path, ITEMS) as owned:
            assert owned == path
            owned.write_text(json.dumps({"version": 1, "items": ITEMS}), encoding="utf-8")
            raise RuntimeError("owned scenario failed")
    assert (path.read_bytes() if path.exists() else None) == original
    assert not (qa / ".provider-suggestions.lock").exists()


def test_fixture_does_not_remove_another_runner_lock_or_foreign_suggestion_edit(tmp_path):
    qa = tmp_path / ".cache" / "desktop-test"
    qa.mkdir(parents=True)
    lock = qa / ".provider-suggestions.lock"
    lock.write_bytes(b"other runner")
    with pytest.raises(FileExistsError):
        with preserve_qa_suggestions(tmp_path, ITEMS):
            pytest.fail("Another runner already owns the shared fixture")
    assert lock.read_bytes() == b"other runner"
    lock.unlink()  # Only the lock this test created.
    with pytest.raises(RuntimeError, match="outside this fixture"):
        with preserve_qa_suggestions(tmp_path, ITEMS) as path:
            path.write_bytes(b"another writer's content")
    assert path.read_bytes() == b"another writer's content"
    assert not lock.exists()
