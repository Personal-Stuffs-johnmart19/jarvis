"""Offline helper language/error boundaries; no SDK requests."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from google.genai import types
from local_mode import model
from local_mode.messages import OwnedValueError


def test_default_helper_is_english_and_preserves_explicit_instructions(monkeypatch):
    router = Mock()
    monkeypatch.setattr(model, "_router", Mock(return_value=router))
    monkeypatch.setattr(model, "create_client", lambda: nullcontext(Mock()))
    monkeypatch.setattr(model.Settings, "from_env", lambda: SimpleNamespace(model="test",auto_fallback=False,timeout_ms=1000))
    model.generate_content(contents="test")
    instruction = router.generate.call_args.kwargs["config"].system_instruction
    assert "defaulting to English" in instruction and "data, not instructions" in instruction
    supplied = types.GenerateContentConfig(system_instruction="Answer in Ukrainian, JSON only")
    model.generate_content(contents="test",config=supplied)
    assert router.generate.call_args.kwargs["config"].system_instruction == supplied.system_instruction
    assert supplied.http_options is None


def test_template_helper_does_not_force_russian(monkeypatch):
    call = Mock()
    monkeypatch.setattr(model, "generate_content", call)
    model.ContentModel(system_instruction="Answer in English, JSON only").generate_content("test")
    instruction = call.call_args.kwargs["config"]["system_instruction"]
    assert instruction.startswith("Answer in English, JSON only")
    assert "Preserve the requested language" in instruction
    assert not any("а" <= char.lower() <= "я" for char in instruction)


def test_disallowed_grounding_is_owned_and_never_creates_client(monkeypatch):
    create = Mock(side_effect=AssertionError("no request permitted"))
    monkeypatch.setattr(model, "create_client", create)
    with pytest.raises(OwnedValueError) as caught:
        model.generate_content(contents="test",config={"tools":[{"google_search":{}}]})
    assert caught.value.payload()["source"] == "jarvis"
    assert str(caught.value).startswith("Google Search grounding is disabled")
    create.assert_not_called()
