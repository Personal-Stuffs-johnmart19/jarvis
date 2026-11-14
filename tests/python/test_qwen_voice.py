"""Downloaded Qwen voice availability, GPU policy, worker routing and cancellation."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from local_mode import voice_settings
from local_mode.qwen_voice import QwenVoice, VOICE_ID, ensure_model
from local_mode.voice import LocalSpeaker


def test_qwen_voice_is_persistable_but_hidden_until_model_is_installed(tmp_path, monkeypatch):
    from local_mode import qwen_voice
    monkeypatch.setattr(qwen_voice, "MODEL", tmp_path / "voice.gguf")
    monkeypatch.setattr(qwen_voice, "BASE_MODEL", tmp_path / "base.gguf")
    monkeypatch.setattr(voice_settings, "PREFERENCES", tmp_path / "voice.json")
    monkeypatch.setattr(voice_settings, "gemini_tts_configured", lambda: False)
    voice_settings.save_voice(VOICE_ID)
    assert voice_settings.read_voice() == VOICE_ID
    assert VOICE_ID not in {v["id"] for v in voice_settings.available_neural_voices()}
    qwen_voice.MODEL.write_bytes(b"installed")
    assert VOICE_ID in {v["id"] for v in voice_settings.available_neural_voices()}


def test_qwen_constructor_requires_vulkan_and_forwards_download_cancellation(monkeypatch):
    import threading
    from local_mode import qwen_voice
    ensure = Mock()
    native = Mock()
    monkeypatch.setattr(qwen_voice, "ensure_base_model", ensure)
    monkeypatch.setattr(qwen_voice, "ensure_reference", lambda cancel: qwen_voice.REFERENCE_DIRECTORY / "reference.wav")
    monkeypatch.setattr("local_mode.supertonic_voice.RussianVoice.__init__", native)
    cancel = threading.Event()
    QwenVoice(cancel_event=cancel)
    ensure.assert_called_once_with(cancel)
    assert native.call_args.kwargs["model_spec"]["family"] == "qwen3_tts"
    assert native.call_args.kwargs["model_spec"]["path"] == str(qwen_voice.BASE_MODEL)
    assert native.call_args.kwargs["model_spec"]["session_options"] == {"qwen3_tts.voice_prompt_cache_slots": 1}
    assert "backend" not in native.call_args.kwargs["model_spec"]
    assert native.call_args.kwargs["cancel_event"] is cancel


def test_qwen_download_uses_pinned_checksum_and_cancel_event(monkeypatch):
    import threading
    from local_mode import qwen_voice
    download=Mock()
    monkeypatch.setattr(qwen_voice, "download_file", download)
    cancel=threading.Event()
    ensure_model(cancel)
    args=download.call_args
    assert '/resolve/874fcb41ececda20bcbc0782d6b4ea7f7d069ea8/' in args.args[0]
    assert len(args.kwargs['sha256']) == 64
    assert args.kwargs['cancel_event'] is cancel


def test_qwen_requests_anchored_base_voice_in_both_languages():
    engine=QwenVoice.__new__(QwenVoice)
    for text, expected in [('Hello friend', 'en'), ('Привет, друг', 'ru')]:
        language, options=engine.request_options(text,'anime-female')
        assert language == expected and options['x_vector_only_mode'] is False
        assert 'instruct' not in options
        assert 'pitch' not in options and 'speed' not in options


def test_qwen_worker_routes_voice_without_fallback_and_closes_owned_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_settings, 'PREFERENCES', tmp_path/'voice.json')
    voice_settings.save_voice(VOICE_ID)
    engine=SimpleNamespace(gpu='Vulkan test GPU', cancelled=False, play=Mock(), close=Mock(), cancel=Mock())
    monkeypatch.setattr('local_mode.qwen_voice.QwenVoice', Mock(return_value=engine))
    speaker=LocalSpeaker()
    try:
        speaker.say('Hello!').result(timeout=3)
        engine.play.assert_called_once()
        assert engine.play.call_args.args[2] == 'anime-female'
        assert not engine.play.call_args.kwargs
        speaker.cancel()
        engine.cancel.assert_called_once()
    finally:
        speaker.close()
    engine.close.assert_called_once()


def test_qwen_gpu_failure_surfaces_without_cpu_substitution(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_settings, 'PREFERENCES', tmp_path/'voice.json')
    voice_settings.save_voice(VOICE_ID)
    monkeypatch.setattr('local_mode.qwen_voice.QwenVoice', Mock(side_effect=RuntimeError('GPU unavailable')))
    cpu=Mock()
    monkeypatch.setattr('local_mode.voice._cpu_audio', cpu)
    speaker=LocalSpeaker()
    try:
        with pytest.raises(RuntimeError,match='GPU unavailable'):
            speaker.say('Hello!').result(timeout=3)
        cpu.assert_not_called()
    finally:
        speaker.close()


@pytest.mark.parametrize('action', ['cancel', 'close'])
def test_stop_or_close_during_qwen_startup_reaps_server(tmp_path, monkeypatch, action):
    import threading
    from local_mode import qwen_voice, supertonic_voice
    monkeypatch.setattr(voice_settings, 'PREFERENCES', tmp_path/'voice.json')
    voice_settings.save_voice(VOICE_ID)
    monkeypatch.setattr(qwen_voice, 'ensure_base_model', lambda cancel_event: None)
    monkeypatch.setattr(qwen_voice, 'ensure_reference', lambda cancel_event: qwen_voice.REFERENCE_DIRECTORY / 'reference.wav')
    monkeypatch.setattr(supertonic_voice, 'ensure_assets', lambda **kwargs: None)
    monkeypatch.setattr(supertonic_voice, 'inspect_devices',
                        lambda *args: SimpleNamespace(returncode=0, stdout='Vulkan:0 "test" [GPU]', stderr=''))
    process = Mock()
    process.poll.side_effect = lambda: 0 if process.terminate.called else None
    monkeypatch.setattr(supertonic_voice.subprocess, 'Popen', Mock(return_value=process))
    ready = threading.Event()
    client = Mock()
    def readiness(*args, **kwargs):
        ready.set()
        assert speaker._cancel.wait(2)
        return SimpleNamespace(status_code=503)
    client.get.side_effect = readiness
    monkeypatch.setattr(supertonic_voice.httpx, 'Client', Mock(return_value=client))
    speaker = LocalSpeaker()
    try:
        future = speaker.say('Hello')
        assert ready.wait(2)
        getattr(speaker, action)()
        if action == 'cancel':
            speaker.wait()
        assert future.cancelled()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=3)
        client.close.assert_called_once()
        assert speaker._vulkan_engine is None
    finally:
        speaker.close()


def test_cancel_during_device_probe_reaps_probe(monkeypatch):
    import subprocess
    import threading
    from local_mode import supertonic_voice
    cancel = threading.Event()
    process = Mock(args=['server', '--list-devices'])
    process.poll.side_effect = lambda: 0 if process.terminate.called else None
    def communicate(**kwargs):
        cancel.set()
        raise subprocess.TimeoutExpired(process.args, .1)
    process.communicate.side_effect = communicate
    monkeypatch.setattr(supertonic_voice.subprocess, 'Popen', Mock(return_value=process))
    with pytest.raises(RuntimeError, match='cancelled'):
        supertonic_voice.inspect_devices(supertonic_voice.RUNTIME/'server.exe', cancel)
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=3)
    process.stdout.close.assert_called_once()
    process.stderr.close.assert_called_once()


def test_cancel_during_runtime_install_prevents_server_launch(monkeypatch):
    import threading
    from local_mode import audio_cpp_install, qwen_voice, supertonic_voice
    from local_mode.downloads import check_cancelled
    cancel = threading.Event()
    monkeypatch.setattr(qwen_voice, 'ensure_base_model', lambda cancel_event: None)
    monkeypatch.setattr(qwen_voice, 'ensure_reference', lambda cancel_event: qwen_voice.REFERENCE_DIRECTORY / 'reference.wav')
    monkeypatch.setattr(audio_cpp_install, 'verify_runtime', lambda cancel_event: False)
    def download(*args, **kwargs):
        assert kwargs['cancel_event'] is cancel
        cancel.set()
        check_cancelled(cancel)
    monkeypatch.setattr(audio_cpp_install, 'download_file', download)
    launch = Mock()
    monkeypatch.setattr(supertonic_voice.subprocess, 'Popen', launch)
    client = Mock()
    monkeypatch.setattr(supertonic_voice.httpx, 'Client', Mock(return_value=client))
    with pytest.raises(RuntimeError, match='cancelled'):
        QwenVoice(cancel_event=cancel)
    launch.assert_not_called()
    client.close.assert_called_once()


def test_cancelled_qwen_engine_is_closed_before_worker_waits(tmp_path, monkeypatch):
    import threading
    monkeypatch.setattr(voice_settings, 'PREFERENCES', tmp_path/'voice.json')
    voice_settings.save_voice(VOICE_ID)
    started = threading.Event()
    def play(text, cancel, selected):
        started.set()
        assert cancel.wait(2)
    engine = SimpleNamespace(gpu='test', cancelled=False, play=play, close=Mock(), cancel=Mock())
    monkeypatch.setattr('local_mode.qwen_voice.QwenVoice', Mock(return_value=engine))
    speaker = LocalSpeaker()
    try:
        future = speaker.say('Hello')
        assert started.wait(2)
        speaker.cancel()
        speaker.wait()
        assert future.cancelled()
        engine.close.assert_called_once()
        assert speaker._vulkan_engine is None
    finally:
        speaker.close()
