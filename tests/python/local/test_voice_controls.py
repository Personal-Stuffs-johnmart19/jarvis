"""Проверка PCM и микрофона без доступа к реальным аудиоустройствам."""
import io
import json
import threading
import wave
from unittest.mock import Mock, patch
import numpy as np
import pytest
from local_mode.speech import RussianRecognizer, voice_command
from local_mode.speech_audio import PlaybackStop, play_wav
from local_mode.voice import LocalSpeaker


def wav_data():
    samples = np.arange(5000, dtype=np.int16)
    data = io.BytesIO()
    with wave.open(data, "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(10000)
        out.writeframes(samples.tobytes())
    return data.getvalue(), samples


@pytest.mark.parametrize("cancel", [False, True])
def test_pause_retains_pcm_and_stop_interrupts_pause(cancel):
    data, samples = wav_data()
    paused = threading.Event(); stop = PlaybackStop(paused)
    reached = threading.Event(); chunks = []
    output = Mock()
    output.__enter__ = Mock(return_value=output)
    output.__exit__ = Mock(return_value=False)
    def write(chunk):
        chunks.append(chunk.copy())
        if len(chunks) == 1: paused.set()
    output.write.side_effect = write
    output.stop.side_effect = reached.set
    with patch("sounddevice.OutputStream", return_value=output):
        thread = threading.Thread(target=play_wav, args=(data, stop))
        thread.start()
        try:
            assert reached.wait(2)
            assert len(chunks) == 1
            if cancel: stop.set()
            else: paused.clear()
        finally:
            thread.join(2)
            stop.set(); paused.clear()
        assert not thread.is_alive()
    if cancel:
        output.abort.assert_called_once()
        assert len(chunks) == 1
    else:
        assert output.start.call_count == 2
        np.testing.assert_array_equal(np.concatenate(chunks), samples)


def test_microphone_pcm_reaches_whisper_and_full_command():
    recognizer = RussianRecognizer.__new__(RussianRecognizer)
    recognizer.microphone = None; recognizer.engine = "whisper"
    decoder = Mock()
    decoder.AcceptWaveform.return_value = True
    decoder.Result.return_value = json.dumps({"text": "включи музыку"})
    recognizer._decoder = Mock(return_value=decoder)
    recognizer._accurate = Mock(return_value="Включи музыку на YouTube")
    pcm = bytes(6400)
    def stream(**kwargs):
        context = Mock()
        context.__enter__ = Mock(side_effect=lambda: kwargs["callback"](pcm, 3200, None, None))
        context.__exit__ = Mock(return_value=False)
        return context
    with patch("sounddevice.query_devices", return_value={"default_samplerate": 16000}), patch("sounddevice.RawInputStream", side_effect=stream):
        result = recognizer.listen(timeout=3)
    recognizer._accurate.assert_called_once_with(pcm, 16000)
    assert voice_command(result, False) == "Включи музыку на YouTube"
    assert voice_command(result, True) == ""
    assert voice_command("Джарвис, Включи музыку", True) == "Включи музыку"
    assert voice_command("джарвистест", True) == ""


def test_muting_discards_recording_before_whisper():
    recognizer = RussianRecognizer.__new__(RussianRecognizer)
    recognizer.microphone = None; recognizer.engine = "whisper"
    recognizer._decoder = Mock()
    recognizer._accurate = Mock()
    context = Mock(); context.__enter__ = Mock(); context.__exit__ = Mock(return_value=False)
    with patch("sounddevice.query_devices", return_value={"default_samplerate": 16000}), patch("sounddevice.RawInputStream", return_value=context):
        assert recognizer.listen(timeout=3, interrupted=lambda: True) == ""
    recognizer._accurate.assert_not_called()


def test_cancel_resets_pause_and_clears_pending_speech():
    with patch("local_mode.voice.read_voice", return_value="silero:xenia"):
        speaker = LocalSpeaker()
    speaker._paused.set()
    from concurrent.futures import Future
    pending = Future()
    speaker._queue.put(("Речь", "silero:xenia", 0, pending))
    assert speaker.busy
    speaker.cancel()
    assert not speaker.paused and not speaker.busy
    assert pending.cancelled()
    speaker.close()
