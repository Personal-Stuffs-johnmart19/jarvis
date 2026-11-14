"""Настройки и поток озвучки: тесты не открывают аудиоустройства."""
import threading
from unittest.mock import Mock, patch
import pytest
from local_mode.voice import LocalSpeaker
from local_mode import voice_settings
from local_mode.voice_settings import neural_voices
from local_mode.silero_voice import phrases


def test_explicit_silero_choice_is_respected_even_with_windows_voices_installed():
    with patch("local_mode.voice.read_voice", return_value="silero:xenia"):
        speaker = LocalSpeaker()
    assert speaker.voice == "silero:xenia"
    speaker.close()


def test_voice_preferences_roundtrip_does_not_touch_other_preferences(tmp_path):
    path = tmp_path / "voice.json"
    with patch.object(voice_settings, "PREFERENCES", path):
        assert voice_settings.read_voice() == "supertonic:F1"
        voice_settings.save_voice("silero:eugene")
        assert voice_settings.read_voice() == "silero:eugene"
        with pytest.raises(ValueError):
            voice_settings.save_voice("silero:unknown")
        assert voice_settings.read_voice() == "silero:eugene"


def test_aliases_select_every_neural_voice_without_sapi():
    with patch("local_mode.voice.read_voice", return_value="silero:xenia"), patch("local_mode.voice.save_voice") as save:
        speaker = LocalSpeaker()
        with patch.object(speaker, "voices", side_effect=AssertionError("SAPI не нужен")):
            for name in voice_settings.SILERO_SPEAKERS:
                speaker.select(name)
                assert speaker.voice == "silero:" + name
            speaker.select("piper-ru")
            assert speaker.voice == "piper:denis"
            speaker.select("ru")
            assert speaker.voice == "supertonic:F1"
            speaker.select("silero")
            assert speaker.voice == "silero:xenia"
        assert save.call_count == 8
        speaker.close()


def test_failed_write_does_not_change_active_voice():
    with patch("local_mode.voice.read_voice", return_value="silero:xenia"), patch("local_mode.voice.save_voice", side_effect=OSError("read-only")):
        speaker = LocalSpeaker()
        with pytest.raises(OSError):
            speaker.configure("silero:aidar")
        assert speaker.voice == "silero:xenia"
        speaker.close()


def test_switch_cancels_active_audio_and_uses_new_voice():
    started, cancelled, completed = threading.Event(), threading.Event(), threading.Event()
    def play(text, stop, selected):
        if text == "первый":
            started.set()
            assert stop.wait(2)
            cancelled.set()
        else:
            assert selected == "eugene"
            completed.set()
    engine = Mock(play=play)
    with patch("local_mode.voice.read_voice", return_value="silero:xenia"), patch("local_mode.voice.save_voice"), patch("local_mode.silero_voice.RussianVoice", return_value=engine):
        speaker = LocalSpeaker()
        try:
            speaker.say("первый")
            assert started.wait(2)
            speaker.configure("silero:eugene")
            speaker.say("второй")
            assert cancelled.wait(2) and completed.wait(2)
            speaker.wait()
        finally:
            speaker.close()


def test_backend_error_is_reported_without_silent_sapi_fallback():
    with patch("local_mode.voice.read_voice", return_value="silero:xenia"), patch("local_mode.silero_voice.RussianVoice", side_effect=ValueError("bad model")):
        speaker = LocalSpeaker()
        notified = threading.Event()
        speaker.on_error = lambda text: notified.set()
        try:
            speaker.say("проверка")
            assert notified.wait(2)
            speaker.wait()
            assert "bad model" in speaker.error
            assert speaker.voice == "silero:xenia"
        finally:
            speaker.close()


def test_long_text_splits_without_losing_words():
    text = "Проверяем русскую речь. " * 100
    chunks = phrases(text)
    assert all(0 < len(part) <= 240 for part in chunks)
    assert " ".join(chunks) == text.strip()


def test_download_fallback_pins_the_same_hash():
    import requests
    from local_mode.silero_voice import ensure_model, SHA256, URL, MIRROR, MODEL
    with patch("local_mode.downloads.download_file", side_effect=[requests.ConnectionError("offline"), None]) as download:
        ensure_model()
    assert [call.args[0] for call in download.call_args_list] == [MIRROR, URL]
    assert all(call.kwargs["sha256"] == SHA256 for call in download.call_args_list)
