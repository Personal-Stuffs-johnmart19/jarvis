"""Qwen request failures remain localized without swallowing diagnostics or cancellation."""
from contextlib import nullcontext
from threading import Event
from unittest.mock import Mock
import httpx
import pytest
from local_mode.qwen_voice import QwenVoice
from local_mode.messages import OwnedRuntimeError, message


@pytest.mark.parametrize('status',[401,500])
def test_failed_http_response_has_owned_prefix_and_keeps_raw_status(status):
    voice=QwenVoice.__new__(QwenVoice)
    voice.process=Mock(); voice.process.poll.return_value=None
    voice.generating=Event();voice.base='http://127.0.0.1:1';voice.model_id='test'
    response=httpx.Response(status,request=httpx.Request('POST',voice.base+'/v1/audio/speech'))
    voice.client=Mock()
    voice.client.stream.return_value=nullcontext(response)
    with pytest.raises(OwnedRuntimeError) as caught:
        voice.wav('Hello!')
    error=caught.value
    assert error.template=='Qwen3 speech request failed: {0}'
    assert str(status) in error.values[0]
    assert message(error.template,*error.values,locale='ru').startswith('Не удалось выполнить запрос озвучки Qwen3:')
    assert not voice.generating.is_set()


def test_invalid_voice_cannot_start_request():
    voice=QwenVoice.__new__(QwenVoice)
    voice.client=Mock()
    with pytest.raises(ValueError):voice.wav('Hello!','untrusted')
    voice.client.stream.assert_not_called()

def test_transport_failure_preserves_redacted_detail(monkeypatch):
    voice=QwenVoice.__new__(QwenVoice)
    voice.process=Mock();voice.process.poll.return_value=None
    voice.generating=Event();voice.base='http://127.0.0.1:1';voice.model_id='test'
    voice.client=Mock()
    monkeypatch.setenv('GEMINI_API_KEY','synthetic-secret')
    voice.client.stream.side_effect=httpx.ConnectError('connection refused synthetic-secret')
    with pytest.raises(OwnedRuntimeError) as caught:
        voice.wav('Hello!')
    assert caught.value.values==('connection refused [redacted]',)
    assert not voice.generating.is_set()


def test_output_open_failure_retains_owned_message_and_original_driver_detail():
    from types import SimpleNamespace
    from local_mode.speech_audio import _open_output
    class DriverError(Exception):
        pass
    failure = DriverError('Raw driver detail / устройство {0}')
    sd = SimpleNamespace(PortAudioError=DriverError,
        OutputStream=Mock(side_effect=failure),
        query_devices=Mock(side_effect=failure), query_hostapis=Mock(return_value=[]))
    with pytest.raises(OwnedRuntimeError) as caught:
        _open_output(sd, 24000)
    assert caught.value.__cause__ is failure
    assert caught.value.values == (str(failure),)
    assert message(caught.value.template, *caught.value.values, locale='ru').startswith('Не удалось открыть аудиовыход Windows.')
    assert message(caught.value.template, *caught.value.values, locale='en').endswith(str(failure))
