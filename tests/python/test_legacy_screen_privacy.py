"""Legacy capture entrypoints must never acquire pixels or invoke a provider."""
import builtins
import importlib
from unittest.mock import Mock

import pytest


@pytest.fixture
def capture_guard(monkeypatch):
    original = builtins.__import__
    forbidden = {"mss", "cv2", "pyautogui", "PIL", "google", "local_mode.model"}

    def guarded(name, *args, **kwargs):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
            raise AssertionError("Legacy capture tried to import " + name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


@pytest.mark.parametrize("params", [
    {"text": "Read my screen", "angle": "screen"},
    {"text": "What is in front of the camera?", "angle": "camera"},
    {},
])
def test_legacy_screen_process_only_returns_explicit_replacement(capture_guard, params):
    module = importlib.import_module("actions.screen_processor")
    module = importlib.reload(module)
    result = module.screen_process(params)
    assert result.startswith("Blocked:")
    assert "screen_capture" in result
    assert "confirm each frame" in result
    assert "http://127.0.0.1:1234" in result
    assert "manually attach" in result
    assert "No screen or camera image was captured or sent" in result


@pytest.mark.parametrize("action,description", [
    ("screen_debug", "Find the error"),
    ("auto", "What is wrong on my screen?"),
])
def test_explicit_and_inferred_screen_debug_do_not_read_capture_or_send(capture_guard, monkeypatch, action, description):
    module = importlib.import_module("actions.code_helper")
    module = importlib.reload(module)
    model = Mock(side_effect=AssertionError("must not invoke a provider"))
    read = Mock(side_effect=AssertionError("must not read the related file"))
    save = Mock(side_effect=AssertionError("must not write model-generated fixes"))
    monkeypatch.setattr(module, "_get_gemini", model)
    monkeypatch.setattr(module, "_read_file", read)
    monkeypatch.setattr(module, "_save_file", save)
    result = module.code_helper({"action": action, "description": description, "file_path": "private.py"})
    assert result.startswith("Blocked:")
    assert "screen_capture" in result
    model.assert_not_called()
    read.assert_not_called()
    save.assert_not_called()


def test_non_screen_code_helper_keeps_existing_dispatch(monkeypatch):
    from actions import code_helper as module
    explain = Mock(return_value="Explained existing code")
    monkeypatch.setattr(module, "_explain_action", explain)
    assert module.code_helper({"action": "explain", "code": "print(1)"}) == "Explained existing code"
    explain.assert_called_once_with("", "print(1)", None)

@pytest.mark.parametrize("name,args", [
    ("screen_process", {"angle": "camera", "text": "look"}),
    ("code_helper", {"action": "screen_debug", "description": "look"}),
])
def test_canonical_handler_reports_retired_capture_as_failure(name, args):
    from local_mode.tools import ToolRegistry
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.special = {}
    registry.player = None
    registry.speak = None
    result = registry._invoke(name, args)
    assert result["ok"] is False
    assert result["action"] == name
    assert "screen_capture" in result["result"]

@pytest.mark.parametrize("name,args", [
    ("screen_process", {"angle": "screen", "text": "look"}),
    ("code_helper", {"action": "screen_debug"}),
])
def test_retired_capture_guidance_keeps_owned_payload_and_russian_translation(name, args):
    from local_mode.messages import OwnedText, text_payload
    from local_mode.results import present_result, user_message
    from local_mode.tools import ToolRegistry
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.special = {}
    registry.player = registry.speak = None
    result = registry._invoke(name, args)
    assert result["ok"] is False
    assert isinstance(result["result"], OwnedText)
    payload = text_payload(result["result"])
    assert payload["source"] == "jarvis"
    assert payload["values"] == ["screen_capture", "http://127.0.0.1:1234"]
    english = present_result(result, locale="en")["result"]
    russian = present_result(result, locale="ru")["result"]
    assert english.startswith("Blocked:")
    assert russian.startswith("Заблокировано:")
    assert "подтвердите каждый кадр отдельно" in russian
    assert "прикрепите изображение вручную" in russian
    assert user_message(result, locale="ru") == russian
    for rendered in [english, russian]:
        assert "screen_capture" in rendered
        assert "http://127.0.0.1:1234" in rendered


def test_retired_screen_catalogue_is_unavailable_and_discloses_replacement():
    from local_mode.services import ServiceRegistry
    registry = ServiceRegistry(Mock(), lambda: Mock())
    entry = next(item for item in registry.catalog(query="screen_process", limit=100)["items"] if item["name"] == "screen_process")
    assert entry["available"] is False
    assert entry["replacement"] == "screen_capture"
    assert "manually attached" in entry["description"]
    code_entry = next(item for item in registry.descriptors if item["name"] == "code_helper")
    assert code_entry["available"] is True
