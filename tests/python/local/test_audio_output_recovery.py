"""Восстановление аудиовыхода без воспроизведения звука."""
from unittest.mock import Mock,patch
from threading import Event
import sounddevice as sd
import pytest
from local_mode.speech_audio import play_wav
from tests.python.local.test_voice_controls import wav_data


def test_mme_start_failure_falls_back_to_wasapi_native_format():
    data,_=wav_data(); broken=[]; output=Mock(); written=[]
    output.write.side_effect=lambda audio:written.append(audio.copy())
    def create(**options):
        if options.get("device")==8:return output
        stream=Mock();stream.start.side_effect=sd.PortAudioError("There is no driver installed")
        broken.append(stream);return stream
    def device(index=None,kind=None):
        return {"default_samplerate":48000 if index==8 else 44100,"max_output_channels":2}
    with patch("sounddevice.OutputStream",side_effect=create),patch("sounddevice.query_devices",side_effect=device),patch("sounddevice.query_hostapis",return_value=[{"name":"Windows WASAPI","default_output_device":8}]):
        play_wav(data,Event())
    assert len(broken)==2
    assert all(stream.close.called for stream in broken)
    assert sum(len(chunk) for chunk in written)==24000
    assert all(chunk.shape[1]==2 for chunk in written)
    output.stop.assert_called_once();output.close.assert_called_once()


def test_no_driver_reports_actionable_russian_error():
    data,_=wav_data()
    with patch("sounddevice.OutputStream",side_effect=sd.PortAudioError("No driver")),patch("sounddevice.query_devices",side_effect=sd.PortAudioError("No driver")),patch("sounddevice.query_hostapis",return_value=[]):
        with pytest.raises(RuntimeError,match="Проверьте устройство вывода"):
            play_wav(data,Event())


def test_write_failure_does_not_replay_already_spoken_audio():
    data,_=wav_data();output=Mock();output.write.side_effect=sd.PortAudioError("Disconnected")
    with patch("sounddevice.OutputStream",return_value=output) as create,patch("sounddevice.query_devices") as devices:
        with pytest.raises(RuntimeError,match="отключилось во время озвучки"):play_wav(data,Event())
    create.assert_called_once();devices.assert_not_called();output.close.assert_called_once()
