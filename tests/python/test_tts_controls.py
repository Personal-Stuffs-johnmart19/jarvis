from unittest.mock import Mock
import pytest
from jsonschema import ValidationError
from local_mode import tts_controls as controls
from local_mode.services import ServiceRegistry

@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(controls,"settings_path",lambda:tmp_path/"tts-controls.json")
    return tmp_path

def test_legacy_defaults_preserve_punctuation_speed_and_ru(isolated):
    assert controls.request_options("Ready!")==("ru",{"num_inference_steps":8,"text_chunk_size":160,"speed":1.03})
    assert controls.request_options("Ready?")[1]["speed"]==0.98
    assert not (isolated/"tts-controls.json").exists()

def test_manual_controls_reach_runtime_options_without_restart(isolated):
    values=dict(controls.DEFAULTS,language="en",automatic_speed=False,speed=1.25,inference_steps=12)
    controls.save(values)
    assert controls.request_options("Ready!")==("en",{"num_inference_steps":12,"text_chunk_size":160,"speed":1.25})
    controls.save(dict(values,language="uk",speed=0.8))
    assert controls.request_options("Ready.")[0]=="uk"

@pytest.mark.parametrize("change",[{"language":"xx"},{"speed":0},{"speed":3},{"speed":float("nan")},{"inference_steps":0},{"inference_steps":True},{"inference_steps":33},{"style":"fictional"}])
def test_unsupported_or_unbounded_controls_are_rejected_before_persistence(isolated,change):
    with pytest.raises((ValueError,ValidationError)):controls.save(dict(controls.DEFAULTS,**change))
    assert not (isolated/"tts-controls.json").exists()

def test_shared_voice_save_requires_approval_and_preserves_other_engines(isolated):
    runtime=Mock();registry=ServiceRegistry(runtime,lambda:Mock())
    args={"voice":"supertonic:M2","synthesis":dict(controls.DEFAULTS,language="en")}
    with pytest.raises(ValueError,match="approval"):registry.execute("voice.select",args,approved=False)
    runtime.speaker.configure.assert_not_called()
    registry.ui_call("settings.voice",args)
    runtime.speaker.configure.assert_called_once_with("supertonic:M2")
    assert controls.read()["language"]=="en"
    read=registry.execute("tts.controls",{},approved=False)
    assert read["values"]==args["synthesis"] and "uk" in read["languages"]
    runtime.speaker.configure.reset_mock()
    with pytest.raises(ValueError,match="only by Supertonic"):
        registry.ui_call("settings.voice",dict(args,voice="silero:xenia"))
    runtime.speaker.configure.assert_not_called()


def test_actual_speech_request_uses_controls_and_does_not_russianize_english_numbers(isolated,monkeypatch):
    import io, wave
    from threading import Event
    from local_mode import supertonic_voice
    stream=io.BytesIO()
    with wave.open(stream,"wb") as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(44100);wav.writeframes(b"\x00\x00"*100)
    response=Mock(status_code=200,iter_bytes=lambda:iter([stream.getvalue()]))
    context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
    engine=supertonic_voice.RussianVoice.__new__(supertonic_voice.RussianVoice)
    engine.process=Mock(poll=Mock(return_value=None));engine.generating=Event()
    engine.client=Mock(stream=Mock(return_value=context));engine.model_id="test";engine.base="http://127.0.0.1:1"
    converter=Mock(return_value="twenty seven")
    monkeypatch.setattr(supertonic_voice,"numbers_to_words",converter)
    controls.save(dict(controls.DEFAULTS,language="en",automatic_speed=False,speed=1.2,inference_steps=11))
    engine.wav("27!","F2")
    request=engine.client.stream.call_args.kwargs["json"]
    assert request["input"]=="27!" and request["language"]=="en" and request["voice"]=="F2"
    assert request["options"]["speed"]==1.2 and request["options"]["num_inference_steps"]==11
    converter.assert_not_called()
    assert not engine.generating.is_set()


def test_approved_preview_uses_user_text_instead_of_fixed_russian_phrase(isolated):
    runtime=Mock();registry=ServiceRegistry(runtime,lambda:Mock())
    registry.ui_call("settings.voice",{"voice":"supertonic:F1","synthesis":dict(controls.DEFAULTS,language="en"),"preview":True,"preview_text":"Hello 27!"})
    runtime.speaker.say.assert_called_once_with("Hello 27!")


def test_invalid_saved_controls_are_recoverable_in_settings_but_not_used_for_inference(isolated):
    controls.settings_path().write_text('{"speed":0}',encoding="utf8")
    with pytest.raises(ValidationError):controls.request_options("Hello")
    description=controls.describe()
    assert description["warning"]=="invalid_saved_controls" and description["values"]==controls.DEFAULTS
    controls.save(description["values"])
    assert "warning" not in controls.describe()


def test_preview_without_text_uses_language_default_or_requests_user_text_before_changes(isolated):
    runtime=Mock();registry=ServiceRegistry(runtime,lambda:Mock())
    args={"voice":"supertonic:F1","synthesis":dict(controls.DEFAULTS,language="en"),"preview":True}
    registry.ui_call("settings.voice",args)
    runtime.speaker.say.assert_called_once_with("Hello! This is a voice preview.")
    runtime.speaker.configure.reset_mock()
    with pytest.raises(ValueError,match="Provide preview text"):
        registry.ui_call("settings.voice",dict(args,synthesis=dict(controls.DEFAULTS,language="uk")))
    runtime.speaker.configure.assert_not_called()


@pytest.mark.parametrize('locale,expected', [('en', 'Hello! This is a voice preview.'),
                                            ('ru', 'Привет! Проверяем выбранный голос.')])
def test_qwen_preview_does_not_read_corrupt_supertonic_settings(isolated, locale, expected):
    controls.settings_path().write_text('{invalid Supertonic settings', encoding='utf8')
    runtime = Mock(interface_locale=locale)
    registry = ServiceRegistry(runtime, lambda: Mock())
    registry.ui_call('settings.voice', {'voice': 'qwen3:anime-female', 'preview': True})
    runtime.speaker.configure.assert_called_once_with('qwen3:anime-female')
    runtime.speaker.say.assert_called_once_with(expected)
    assert controls.settings_path().read_text(encoding='utf8') == '{invalid Supertonic settings'


def test_play_prefetch_preserves_english_digits_in_actual_requests(isolated, monkeypatch):
    import io
    import wave
    from threading import Event
    from local_mode import supertonic_voice

    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b"\x00\x00" * 100)
    response = Mock(status_code=200, iter_bytes=lambda: iter([audio.getvalue()]))
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    engine = supertonic_voice.RussianVoice.__new__(supertonic_voice.RussianVoice)
    engine.process = Mock(poll=Mock(return_value=None))
    engine.generating = Event()
    engine.unavailable = False
    engine.client = Mock(stream=Mock(return_value=context))
    engine.model_id = "test"
    engine.base = "http://127.0.0.1:1"
    converter = Mock(return_value="Russian number substitution")
    monkeypatch.setattr(supertonic_voice, "numbers_to_words", converter)
    monkeypatch.setattr(supertonic_voice, "trim_wav_edges", lambda data: data)
    playback = Mock()
    monkeypatch.setattr(supertonic_voice, "play_wav", playback)
    controls.save(dict(controls.DEFAULTS, language="en"))

    engine.play("First 27!\n\nThen 42!", Event(), "F2")

    requests = [call.kwargs["json"] for call in engine.client.stream.call_args_list]
    assert [request["input"] for request in requests] == ["First 27!", "Then 42!"]
    assert all(request["language"] == "en" and request["voice"] == "F2" for request in requests)
    assert playback.call_count == 2
    converter.assert_not_called()


@pytest.mark.parametrize("language", ["ru", "en"])
def test_play_segments_after_language_specific_number_expansion(isolated, monkeypatch, language):
    from threading import Event
    from local_mode import supertonic_voice
    from local_mode.speech_numbers import numbers_to_words

    engine = supertonic_voice.RussianVoice.__new__(supertonic_voice.RussianVoice)
    engine.unavailable = False
    chunks = []

    def synthesis(text, speaker):
        assert len(text) <= 160
        # The real wav() applies the Russian converter again before its 240-char bound.
        assert len(numbers_to_words(text) if language == "ru" else text) <= 240
        chunks.append(text)
        return b"mock audio"

    engine.wav = synthesis
    monkeypatch.setattr(supertonic_voice, "trim_wav_edges", lambda data: data)
    monkeypatch.setattr(supertonic_voice, "play_wav", Mock())
    controls.save(dict(controls.DEFAULTS, language=language))
    text = " ".join(["27"] * 20)
    engine.play(text, Event())
    if language == "ru":
        assert len(chunks) > 1
        assert " ".join(chunks) == numbers_to_words(text)
    else:
        assert chunks == [text]

@pytest.mark.parametrize("saved", ["{broken-json", '{"language":"invalid"}'])
def test_invalid_saved_controls_reach_playback_cpu_fallback(isolated, monkeypatch, saved):
    from threading import Event
    from unittest.mock import Mock
    from local_mode import supertonic_voice
    controls.settings_path().write_text(saved, encoding="utf8")
    engine = supertonic_voice.RussianVoice.__new__(supertonic_voice.RussianVoice)
    engine.unavailable = False
    engine.close = Mock()
    engine.client = Mock()
    fallback, warning, playback = Mock(return_value=b"cpu audio"), Mock(), Mock()
    monkeypatch.setattr(supertonic_voice, "trim_wav_edges", lambda data: data)
    monkeypatch.setattr(supertonic_voice, "play_wav", playback)
    engine.play("Hello 27!", Event(), fallback=fallback, on_fallback=warning)
    fallback.assert_called_once_with("Hello 27!")
    warning.assert_called_once()
    engine.close.assert_called_once()
    assert engine.unavailable and engine.client.mock_calls == []
    assert playback.call_args.args[0] == b"cpu audio"
    assert controls.settings_path().read_text(encoding="utf8") == saved


def test_piper_preview_uses_model_language_not_supertonic_controls(isolated,monkeypatch):
    from pathlib import Path
    from local_mode import piper_models
    runtime=Mock();registry=ServiceRegistry(runtime,lambda:Mock())
    monkeypatch.setattr(piper_models,'resolve',lambda voice:(Path('fixture.onnx'),0,'en-us','identity'))
    controls.save(dict(controls.DEFAULTS,language='ru'))
    args={'voice':'piper:managed:'+'a'*24+':0','preview':True}
    registry.ui_call('settings.voice',args)
    runtime.speaker.say.assert_called_once_with('Hello! This is a voice preview.')
    runtime.speaker.configure.reset_mock()
    monkeypatch.setattr(piper_models,'resolve',lambda voice:(Path('fixture.onnx'),0,'de-de','identity'))
    with pytest.raises(ValueError,match='Provide preview text'):
        registry.ui_call('settings.voice',args)
    runtime.speaker.configure.assert_not_called()
    runtime.speaker.say.reset_mock()
    registry.ui_call('settings.voice',dict(args,preview_text='User-authored preview'))
    runtime.speaker.say.assert_called_once_with('User-authored preview')
