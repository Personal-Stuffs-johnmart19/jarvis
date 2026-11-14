"""Built-in template provenance must not change saved user content."""
import json
import re
from pathlib import Path
from unittest.mock import Mock

from local_mode.services import ServiceRegistry
from local_mode.suggestion_defaults import SUGGESTIONS


def test_builtin_suggestion_catalog_is_english_and_has_separate_translations(tmp_path):
    runtime = Mock(suggestions_path=tmp_path / "suggestions.json")
    registry = ServiceRegistry(runtime, lambda: Mock())
    catalog = registry.ui_call("suggestions.catalog", {})
    assert catalog == {"items": list(SUGGESTIONS), "builtin": True}
    assert registry.ui_call("suggestions.get", {}) == catalog["items"]
    root = Path(__file__).resolve().parents[2]
    ru = json.loads((root / "apps/desktop/src/locales/ru.json").read_text(encoding="utf8"))
    for label, prompt in SUGGESTIONS:
        assert not re.search(r"[А-Яа-яЁё]", label + prompt)
        assert label in ru and prompt in ru


def test_saved_suggestions_remain_user_authored_even_if_label_matches_default(tmp_path):
    runtime = Mock(suggestions_path=tmp_path / "suggestions.json")
    registry = ServiceRegistry(runtime, lambda: Mock())
    items = [["◉ News", "Моя личная команда"]]
    registry.ui_call("suggestions.save", {"items": items})
    assert registry.ui_call("suggestions.catalog", {}) == {"items": [("◉ News", "Моя личная команда")], "builtin": False}
    runtime.event.assert_any_call("suggestions.changed", {"items": [("◉ News", "Моя личная команда")], "builtin": False})
