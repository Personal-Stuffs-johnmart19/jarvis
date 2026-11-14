"""Облачная озвучка: выбор модели, реальные формы SDK, отмена; без звука/сети."""

import base64
from contextlib import nullcontext
import io
from types import SimpleNamespace as NS
from threading import Event
from unittest.mock import Mock, patch
import wave

import pytest
from google.genai import types
from google.genai.errors import APIError
from local_mode.gemini_voice import (
    GeminiVoice,
    FLASH,
    LITE,
    PREVIEW,
    chunks,
    model_chain,
    interaction_wav,
    preview_wav,
)
from local_mode.streaming_speech import StreamingSpeech
from local_mode.voice_settings import validate_voice
from tests.python.local.test_streaming_speech import Speaker


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(24000)
        f.writeframes(b"\0\0" * 100)
    return output.getvalue()


def test_auto_routes_full_turn_and_honors_explicit_selection():
    assert model_chain("Привет")[0] == LITE
    assert model_chain("я" * 601) == (FLASH, LITE, PREVIEW)
    assert model_chain("длинный " * 200, "lite")[0] == LITE
    assert model_chain("текст", "preview") == (PREVIEW,)
    assert validate_voice("gemini:auto") == "gemini:auto"
    with pytest.raises(ValueError):
        validate_voice("gemini:paid-other")


@pytest.mark.parametrize(
    "text", ["слово " * 2000, "я" * 4000], ids=["long-words", "long-token"]
)
def test_bounded_chunks_keep_every_character(text):
    parts = chunks(text)
    assert all(0 < len(p) <= 1800 for p in parts)
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")
    with pytest.raises(ValueError):
        chunks("x" * 16001)


def test_wav_and_legacy_pcm_decode():
    assert (
        interaction_wav(NS(output_audio=NS(data=base64.b64encode(wav()).decode())))
        == wav()
    )
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(
                                data=b"\0\0" * 100,
                                mime_type="audio/l16;codec=pcm;rate=24000",
                            )
                        )
                    ]
                )
            )
        ]
    )
    assert preview_wav(response) == wav()
    with pytest.raises(ValueError):
        interaction_wav(NS(output_audio=None))


def test_interactions_uses_shared_client_no_storage_or_duplicate_sdk_retries():
    client = Mock()
    client.interactions.create.return_value = NS(
        output_audio=NS(data=base64.b64encode(wav()).decode())
    )
    with patch(
        "local_mode.gemini_voice.gemini.create_client", return_value=nullcontext(client)
    ) as create:
        assert GeminiVoice().request(LITE, "Привет", Event()) == wav()
    create.assert_called_once_with(retry_attempts=0)
    kwargs = client.interactions.create.call_args.kwargs
    assert kwargs["model"] == LITE and kwargs["store"] is False
    assert kwargs["input"][0]["content"][0]["text"] == "Привет"


def test_preview_uses_generate_content_audio_config():
    client = Mock()
    client.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    parts=[
                        types.Part(
                            inline_data=types.Blob(data=wav(), mime_type="audio/wav")
                        )
                    ]
                )
            )
        ]
    )
    with patch(
        "local_mode.gemini_voice.gemini.create_client", return_value=nullcontext(client)
    ):
        assert GeminiVoice().request(PREVIEW, "Привет", Event()) == wav()
    assert (
        client.models.generate_content.call_args.kwargs[
            "config"
        ].http_options.retry_options.attempts
        == 1
    )


def test_quota_falls_through_then_reuses_working_model_without_repeating_audio():
    engine = GeminiVoice()
    engine.request = Mock(
        side_effect=[APIError(429, {"error": {"message": "quota"}}), wav(), wav()]
    )
    with patch("local_mode.gemini_voice.play_wav") as play:
        engine.play(
            "Первое предложение. " * 150, Event(), fallback=Mock(), on_status=Mock()
        )
    assert engine.request.call_args_list[0].args[0] == FLASH
    assert all(call.args[0] == LITE for call in engine.request.call_args_list[1:])
    assert play.call_count == 2


def test_stop_during_request_never_plays_or_falls_back():
    stop = Event()
    engine = GeminiVoice()

    def request(*args):
        stop.set()
        return wav()

    engine.request = Mock(side_effect=request)
    fallback = Mock()
    with patch("local_mode.gemini_voice.play_wav") as play:
        engine.play("Привет", stop, fallback=fallback, on_status=Mock())
    play.assert_not_called()
    fallback.assert_not_called()


def test_all_models_unavailable_uses_local_backend():
    engine = GeminiVoice()
    engine.request = Mock(
        side_effect=APIError(404, {"error": {"message": "not found"}})
    )
    fallback = Mock(return_value=wav())
    with patch("local_mode.gemini_voice.play_wav") as play:
        engine.play("Привет", Event(), fallback=fallback, on_status=Mock())
    assert engine.request.call_count == 3
    fallback.assert_called_once_with("Привет")
    play.assert_called_once()


def test_cloud_waits_for_full_turn_and_preserves_long_safe_reply():
    speaker = Speaker()
    speaker.voice = "gemini:auto"
    stream = StreamingSpeech(speaker, lambda: True, Event())
    text = "Это длинный ответ с цифрами 2026 и English. " * 60
    stream.feed(text)
    assert speaker.texts == []
    stream.finish({"ok": True, "action": "reply", "reply": text})
    assert len(speaker.texts) == 1 and len(speaker.texts[0]) > 1200
    assert "2026" in speaker.texts[0] and "English" in speaker.texts[0]


def test_retry_is_cancelled_in_backoff_and_cannot_send_another_request():
    stop = Event()
    client = Mock()

    def fail(**kwargs):
        stop.set()
        raise APIError(503, {"error": {"message": "busy"}})

    client.interactions.create.side_effect = fail
    with patch(
        "local_mode.gemini_voice.gemini.create_client", return_value=nullcontext(client)
    ):
        with pytest.raises(APIError):
            GeminiVoice().request(LITE, "Привет", stop)
    assert client.interactions.create.call_count == 1


def test_cloud_speech_errors_and_fallback_status_keep_owned_resources():
    from local_mode.messages import OwnedText, OwnedValueError, message
    with pytest.raises(OwnedValueError) as invalid:
        model_chain("hello", "unknown")
    payload = invalid.value.payload()
    assert payload["message"] == "Unknown Gemini TTS mode."
    assert message(payload["message"], locale="ru") == "Неизвестный режим Gemini TTS."
    with pytest.raises(OwnedValueError):
        chunks("x" * 16001)
    engine = GeminiVoice()
    engine.request = Mock(side_effect=RuntimeError("raw diagnostic"))
    statuses = []
    with patch("local_mode.gemini_voice.play_wav"):
        engine.play("hello", Event(), fallback=lambda _: wav(), on_status=statuses.append)
    assert all(isinstance(status, OwnedText) for status in statuses)
    assert any(status.values == ("raw diagnostic",) for status in statuses)
    assert statuses[-1].payload()["message"] == "Gemini TTS is unavailable. Using local CPU speech."
    assert "локальная озвучка CPU" in message(statuses[-1].template, locale="ru")


@pytest.mark.parametrize("failure,template", [
    (ValueError("raw diagnostic"), "Gemini TTS: {0}"),
    (APIError(403, {"error":{"message":"denied"}}), "Gemini rejected the API key or project access."),
])
def test_mapped_api_status_is_owned_and_raw_diagnostics_stay_values(failure, template):
    from local_mode.messages import OwnedText
    engine = GeminiVoice()
    engine.request = Mock(side_effect=failure)
    statuses = []
    with patch("local_mode.gemini_voice.play_wav"):
        engine.play("hello", Event(), fallback=lambda _: wav(), on_status=statuses.append)
    assert any(isinstance(s, OwnedText) and s.template == template for s in statuses)


def test_validation_status_keeps_its_template_instead_of_flattening():
    from local_mode.messages import OwnedValueError, message
    engine = GeminiVoice()
    engine.request = Mock(side_effect=OwnedValueError("Gemini TTS returned no audio."))
    statuses = []
    with patch("local_mode.gemini_voice.play_wav"):
        engine.play("hello", Event(), fallback=lambda _: wav(), on_status=statuses.append)
    failure = next(s for s in statuses if s.template == "Gemini TTS returned no audio.")
    assert failure.values == ()
    assert message(failure.template, locale="ru") == "Gemini TTS не вернул звук."
