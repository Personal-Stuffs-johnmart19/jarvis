"""Speech catalogue readiness is configuration-only and never plays or probes audio."""
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import pytest
import api_keys
from local_mode import credentials, voice_settings


@pytest.mark.parametrize("keys,saved,expected", [([],None,False),([" ",""],"\t",False),(["configured-shared-key"],None,True),([],"encrypted-shared-key",True)])
def test_cloud_speech_choices_require_actual_nonblank_shared_credential(monkeypatch, keys, saved, expected):
    monkeypatch.setattr(api_keys,"get_all_keys",lambda:keys)
    monkeypatch.setattr(credentials,"load_key",lambda:saved)
    choices=voice_settings.available_neural_voices()
    assert any(item["id"].startswith("gemini:") for item in choices) is expected
    assert {item["id"] for item in choices} >= {"supertonic:F1","piper:denis","silero:xenia"}
    # Unavailable saved IDs remain valid metadata: never rename them to SAPI.
    assert any(item["id"]=="gemini:auto" for item in voice_settings.neural_voices())


def test_unreadable_shared_credentials_hide_cloud_speech_without_model_probe(monkeypatch):
    monkeypatch.setattr(api_keys,"get_all_keys",lambda:[])
    def unreadable():raise RuntimeError("cannot decrypt local credential")
    monkeypatch.setattr(credentials,"load_key",unreadable)
    assert not voice_settings.gemini_tts_configured()
    assert not any(item["id"].startswith("gemini:") for item in voice_settings.available_neural_voices())


def test_revoked_cloud_choice_cannot_overwrite_saved_voice(monkeypatch):
    from local_mode import voice
    speaker=voice.LocalSpeaker.__new__(voice.LocalSpeaker)
    speaker.voice="gemini:auto"
    monkeypatch.setattr(voice,"gemini_tts_configured",lambda:False)
    save=Mock();monkeypatch.setattr(voice,"save_voice",save)
    with pytest.raises(ValueError,match="shared Google API key"):
        speaker.configure("gemini:flash")
    save.assert_not_called()
    assert speaker.voice=="gemini:auto" and "Gemini TTS" in speaker.label


def test_available_catalogue_refreshes_after_key_revoke_and_preserves_os_voice_names(monkeypatch):
    from local_mode import voice
    current={"keys":["configured"]}
    monkeypatch.setattr(api_keys,"get_all_keys",lambda:current["keys"])
    monkeypatch.setattr(credentials,"load_key",lambda:None)
    com=ModuleType("comtypes");client=ModuleType("comtypes.client")
    com.CoInitialize=Mock();com.CoUninitialize=Mock();com.client=client
    item=SimpleNamespace(Id="os-raw-id",GetDescription=lambda:"Microsoft Ирина",GetAttribute=lambda name:"419")
    client.CreateObject=lambda *args,**kwargs:SimpleNamespace(GetVoices=lambda:[item])
    monkeypatch.setitem(sys.modules,"comtypes",com);monkeypatch.setitem(sys.modules,"comtypes.client",client)
    assert any(item["id"]=="gemini:auto" for item in voice.LocalSpeaker.voices())
    current["keys"]=[]
    choices=voice.LocalSpeaker.voices()
    assert not any(item["id"].startswith("gemini:") for item in choices)
    assert next(item for item in choices if item["id"]=="sapi:os-raw-id")["name"]=="Windows — Microsoft Ирина"
    com.CoUninitialize.assert_called()


def test_speech_saved_message_has_english_source_and_russian_translation(monkeypatch):
    import json
    from pathlib import Path
    from local_mode import voice
    speaker=voice.LocalSpeaker.__new__(voice.LocalSpeaker)
    speaker.voice="silero:xenia";speaker._lock=threading.RLock();speaker._closing=threading.Event();speaker.cancel=Mock()
    monkeypatch.setattr(voice,"save_voice",Mock())
    message=speaker.configure("silero:xenia")
    root=Path(__file__).resolve().parents[2]
    en=json.loads((root/'apps/desktop/src/locales/en.json').read_text(encoding='utf-8'))
    ru=json.loads((root/'apps/desktop/src/locales/ru.json').read_text(encoding='utf-8'))
    assert message=="Speech settings saved. The next response will use the selected voice."
    assert en[message]==message and ru[message]!=message


def test_legacy_catalogue_failure_does_not_hide_usable_shared_store_key(monkeypatch):
    def corrupt_catalogue():
        raise ValueError("malformed legacy key catalogue")
    monkeypatch.setattr(api_keys,"get_all_keys",corrupt_catalogue)
    monkeypatch.setattr(credentials,"load_key",lambda:"usable-shared-store-key")
    assert voice_settings.gemini_tts_configured()
    assert any(item["id"]=="gemini:auto" for item in voice_settings.available_neural_voices())
