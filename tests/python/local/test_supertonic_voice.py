"""GPU/резерв моделируются; тесты не открывают аудиоустройства и не скачивают файлы."""
import io
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
import wave
import pytest
from local_mode.supertonic_voice import RussianVoice, choose_gpu, process_environment, validate_wav
from local_mode.voice import LocalSpeaker
from local_mode import voice_settings


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
        stream.writeframes(b"\x01\x00" * 2400)
    return buffer.getvalue()


def test_auto_gpu_accepts_pascal_and_prefers_discrete_over_integrated():
    output = 'CPU:0 "CPU" [CPU]\nVulkan:0 "Intel UHD Graphics" [GPU]\nVulkan:1 "NVIDIA GeForce GTX 1050 Ti" [GPU]'
    assert choose_gpu(output) == (1, "NVIDIA GeForce GTX 1050 Ti")
    assert choose_gpu('Vulkan:0 "AMD Radeon RX 7900 XTX" [GPU]')[0] == 0
    with pytest.raises(RuntimeError): choose_gpu('CPU:0 "CPU" [CPU]')


def test_all_ten_voices_persist_with_automatic_gpu_default(tmp_path):
    with patch.object(voice_settings, "PREFERENCES", tmp_path/"voice.json"):
        assert voice_settings.read_voice() == "supertonic:F1"
        for name in voice_settings.SUPERTONIC_SPEAKERS:
            voice_settings.save_voice("supertonic:"+name)
            assert voice_settings.read_voice() == "supertonic:"+name
        with pytest.raises(ValueError): voice_settings.save_voice("supertonic:F9")
    assert len([item for item in voice_settings.neural_voices() if item["id"].startswith("supertonic:")]) == 10


def test_native_process_does_not_receive_cloud_secrets():
    with patch.dict("os.environ", {"GEMINI_API_KEY":"test", "TELEGRAM_TOKEN":"test", "PATH":"system"}, clear=True):
        assert process_environment() == {"PATH":"system"}


def test_wav_rejects_empty_and_truncated_data():
    assert validate_wav(wav_bytes()) == (24000, 2400)
    with pytest.raises((ValueError, wave.Error, EOFError)): validate_wav(b"not wave")
    with pytest.raises((ValueError, wave.Error, EOFError)): validate_wav(wav_bytes()[:-200])


def test_failed_gpu_initialization_uses_silero_cpu_and_reports_reason():
    cpu = Mock(wav=Mock(return_value=wav_bytes()))
    with patch("local_mode.voice.read_voice", return_value="supertonic:F1"), patch("local_mode.supertonic_voice.RussianVoice", side_effect=RuntimeError("нет Vulkan")), patch("local_mode.silero_voice.RussianVoice", return_value=cpu), patch("local_mode.voice.play_wav") as play:
        speaker = LocalSpeaker(); speaker.on_status = Mock()
        try:
            speaker.say("Проверка"); speaker.wait()
            cpu.wav.assert_called_once_with("Проверка", "xenia")
            play.assert_called_once()
            assert "Silero CPU" in speaker.backend_status
            assert not speaker.error
        finally: speaker.close()


def test_gpu_engine_is_reused_and_closed_with_speaker():
    gpu = SimpleNamespace(gpu="RX 7900 XTX", cancelled=False, play=Mock(), close=Mock(), cancel=Mock())
    with patch("local_mode.voice.read_voice", return_value="supertonic:M1"), patch("local_mode.supertonic_voice.RussianVoice", return_value=gpu) as factory:
        speaker=LocalSpeaker()
        try:
            speaker.say("Первый"); speaker.wait(); speaker.say("Второй"); speaker.wait()
            factory.assert_called_once(); assert gpu.play.call_count == 2
            assert gpu.play.call_args.args[2] == "M1"
        finally: speaker.close()
        gpu.close.assert_called_once()


def test_mid_reply_gpu_failure_does_not_repeat_already_spoken_fragment():
    engine=RussianVoice.__new__(RussianVoice)
    engine.unavailable=False; engine.close=Mock()
    engine.wav=Mock(side_effect=[wav_bytes(), RuntimeError("нет памяти")])
    fallback=Mock(return_value=wav_bytes()); notice=Mock()
    output=Mock(); output.__enter__=Mock(return_value=output); output.__exit__=Mock(return_value=False)
    with patch("sounddevice.OutputStream", return_value=output):
        engine.play("слово "*70, Event(), fallback=fallback, on_fallback=notice)
    assert engine.wav.call_count == 2
    assert fallback.call_count == 2
    assert output.write.call_count == 5
    notice.assert_called_once()
    engine.close.assert_called_once()


def test_cancel_terminates_only_current_inference():
    engine=RussianVoice.__new__(RussianVoice)
    engine.generating=Event(); engine.cancelled=False; engine.process=Mock()
    engine.process.poll.return_value=None
    engine.cancel(); engine.process.terminate.assert_not_called()
    engine.generating.set(); engine.cancel()
    engine.process.terminate.assert_called_once(); assert engine.cancelled


def test_corrupted_native_runtime_cannot_pass_verification(tmp_path):
    from local_mode import audio_cpp_install as install
    with patch.object(install,"RUNTIME",tmp_path):
        (tmp_path/"audiocpp_server.exe").write_bytes(b"tampered")
        assert not install.verify_runtime()


@pytest.mark.parametrize("legacy", ["silero:xenia", "silero:eugene", "piper:denis", "sapi:test"])
def test_legacy_preferences_use_automatic_gpu_without_manual_selection(tmp_path, legacy):
    import json
    path=tmp_path/"voice.json"
    path.write_text(json.dumps({"voice":legacy}),encoding="utf-8")
    original=path.read_bytes()
    with patch.object(voice_settings,"PREFERENCES",path):
        speaker=LocalSpeaker()
        assert speaker.voice == "supertonic:F1"
        speaker.close()
    assert path.read_bytes() == original


def test_explicit_new_manual_choice_survives_restart(tmp_path):
    with patch.object(voice_settings,"PREFERENCES",tmp_path/"voice.json"):
        voice_settings.save_voice("silero:aidar")
        assert LocalSpeaker().voice == "silero:aidar"


def test_startup_preparation_warms_gpu_without_audio_playback(tmp_path):
    gpu=SimpleNamespace(gpu="RX 7900 XTX",cancelled=False,wav=Mock(return_value=wav_bytes()),play=Mock(),close=Mock(),cancel=Mock())
    with patch.object(voice_settings,"PREFERENCES",tmp_path/"absent.json"), patch("local_mode.supertonic_voice.RussianVoice",return_value=gpu), patch("sounddevice.OutputStream") as output:
        speaker=LocalSpeaker()
        try:
            speaker.prepare(); speaker.wait()
            gpu.wav.assert_called_once_with("Готово.","F1")
            gpu.play.assert_not_called(); output.assert_not_called()
            assert "RX 7900 XTX" in speaker.backend_status
        finally:speaker.close()


def test_startup_unsupported_gpu_prepares_silero_cpu_without_sound(tmp_path):
    gpu=SimpleNamespace(gpu="GTX 1050 Ti",cancelled=False,wav=Mock(side_effect=RuntimeError("нет памяти")),close=Mock(),cancel=Mock())
    cpu=Mock(wav=Mock(return_value=wav_bytes()))
    with patch.object(voice_settings,"PREFERENCES",tmp_path/"absent.json"), patch("local_mode.supertonic_voice.RussianVoice",return_value=gpu), patch("local_mode.silero_voice.RussianVoice",return_value=cpu), patch("sounddevice.OutputStream") as output:
        speaker=LocalSpeaker()
        try:
            speaker.prepare(); speaker.wait()
            assert "Silero CPU" in speaker.backend_status
            assert not speaker.error
            cpu.wav.assert_called_once_with("Готово.","xenia")
            cpu.play.assert_not_called(); output.assert_not_called()
        finally:speaker.close()
        gpu.close.assert_called_once()


def test_disabled_speaker_does_not_prepare_gpu(tmp_path):
    with patch.object(voice_settings,"PREFERENCES",tmp_path/"absent.json"), patch("local_mode.supertonic_voice.RussianVoice") as create:
        speaker=LocalSpeaker(enabled=False)
        speaker.prepare(); speaker.wait(); speaker.close()
        create.assert_not_called()


def test_owned_gpu_failure_has_english_source_and_separate_translation():
    from local_mode.messages import OwnedRuntimeError, message
    with pytest.raises(OwnedRuntimeError) as caught:
        choose_gpu('CPU:0 "CPU" [CPU]')
    error = caught.value
    assert str(error).startswith("audio.cpp found no Vulkan GPU")
    assert "не обнаружил" in message(error.template, *error.values, locale="ru")
    assert error.payload()["source"] == "jarvis"


@pytest.mark.parametrize("body,category", [(b"out of memory: raw provider detail", "insufficient GPU memory"), (b"unsupported shader: raw provider detail", "unsupported Vulkan"), (b"other raw provider detail", "GPU synthesis failed")])
def test_owned_http_failures_keep_status_separate_and_preserve_diagnostic(tmp_path, monkeypatch, body, category):
    from contextlib import nullcontext
    from local_mode import supertonic_voice as module
    from local_mode.messages import OwnedRuntimeError, message
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / ".cache").mkdir()
    monkeypatch.setattr("local_mode.tts_controls.request_options", lambda text: ("en", {}))
    engine = RussianVoice.__new__(RussianVoice)
    engine.process = Mock()
    engine.process.poll.return_value = None
    engine.generating = Event()
    engine.base, engine.model_id = "http://127.0.0.1:1", "synthetic"
    engine.client = Mock()
    engine.client.stream.return_value = nullcontext(SimpleNamespace(status_code=503,iter_bytes=lambda: [body]))
    with pytest.raises(OwnedRuntimeError) as caught:
        engine.wav("hello", "F1")
    assert category in str(caught.value)
    assert caught.value.payload()["values"] == [503]
    assert "HTTP 503" in message(caught.value.template,*caught.value.values,locale="ru")
    assert "raw provider detail" in (tmp_path / ".cache/supertonic-last-error.txt").read_text(encoding="utf8")
    assert not engine.generating.is_set()


def test_installer_checksum_failure_is_owned_without_publishing_file(tmp_path, monkeypatch):
    import zipfile
    from local_mode import audio_cpp_install as install
    from local_mode.messages import OwnedValueError
    monkeypatch.setattr(install, "ROOT", tmp_path)
    monkeypatch.setattr(install, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(install, "verify_runtime", lambda: False)
    monkeypatch.setattr(install, "download_file", Mock())
    monkeypatch.setattr(install, "ASSETS", {"archive_url":"synthetic", "archive_sha256":"a"*64, "runtime_files":{"audio.exe":{"size":3,"sha256":"0"*64}}})
    (tmp_path / ".cache").mkdir()
    with zipfile.ZipFile(tmp_path / ".cache/audio-vulkan-portable.zip", "w") as archive:
        archive.writestr("audio.exe",b"bad")
    with pytest.raises(OwnedValueError, match="SHA256"):
        install.ensure_assets(include_default_model=False)
    assert not (tmp_path / "runtime/audio.exe").exists()
