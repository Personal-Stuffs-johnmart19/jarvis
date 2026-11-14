"""Voice selection errors retain app-owned provenance across the RPC boundary."""
import json
from pathlib import Path

import pytest

from local_mode import voice_settings
from local_mode.messages import OwnedValueError, message


ERROR = "Unknown voice. Choose a voice from the list."


@pytest.mark.parametrize("voice", ["unknown:голос", "sapi:", "sapi:bad\nvoice", "sapi:" + "x" * 1020])
def test_invalid_voice_has_structured_localizable_error(voice):
    with pytest.raises(OwnedValueError) as error:
        voice_settings.validate_voice(voice)
    assert isinstance(error.value, ValueError)
    assert str(error.value) == ERROR
    assert error.value.payload() == {"source": "jarvis", "message": ERROR, "values": []}
    root = Path(__file__).resolve().parents[2]
    for directory in (root / "local_mode/locales", root / "apps/desktop/src/locales"):
        en = json.loads((directory / "en.json").read_text(encoding="utf-8"))
        ru = json.loads((directory / "ru.json").read_text(encoding="utf-8"))
        assert en[ERROR] == ERROR
        assert ru[ERROR] == "Неизвестный голос. Выберите озвучку из списка."
    assert message(ERROR, locale="ru") == "Неизвестный голос. Выберите озвучку из списка."


def test_failed_voice_save_preserves_selection_and_raw_os_identifier(tmp_path, monkeypatch):
    preference = tmp_path / "voice.json"
    monkeypatch.setattr(voice_settings, "PREFERENCES", preference)
    identifier = "sapi:Пользовательский голос / English"
    voice_settings.save_voice(identifier)
    saved = preference.read_bytes()
    assert voice_settings.read_voice() == identifier
    assert json.loads(saved)["voice"] == identifier
    with pytest.raises(OwnedValueError):
        voice_settings.save_voice("unknown:голос")
    assert preference.read_bytes() == saved


def test_managed_piper_validation_preserves_owned_error_and_raw_diagnostic(monkeypatch):
    from local_mode import piper_models
    raw = "raw provider detail / ошибка"
    failure = OwnedValueError("Invalid Piper voice configuration.", raw)

    def reject(voice):
        raise failure

    monkeypatch.setattr(piper_models, "resolve", reject)
    with pytest.raises(OwnedValueError) as error:
        voice_settings.validate_voice("piper:managed:missing")
    assert error.value is failure
    assert error.value.payload()["values"] == [raw]
