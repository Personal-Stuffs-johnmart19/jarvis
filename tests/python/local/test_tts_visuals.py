"""Реакция визуализации на PCM и состояние плеера без аудиоустройств."""
import io
import threading
import wave
from unittest.mock import Mock, patch
import numpy as np
from local_mode.speech_audio import PlaybackStop, play_wav
from local_mode.voice import LocalSpeaker


def test_levels_follow_actual_pcm_and_reset_when_playback_ends():
    samples=np.concatenate([np.full(1000,value,dtype=np.int16) for value in (500,6000,0)])
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as writer:
        writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(10000)
        writer.writeframes(samples.tobytes())
    levels=[];written=[]
    stop=PlaybackStop(threading.Event(),on_level=levels.append)
    output=Mock()
    output.write.side_effect=lambda chunk: written.append((levels[-1],chunk.copy()))
    with patch('local_mode.speech_audio._open_output',return_value=(output,10000,1)):
        play_wav(buffer.getvalue(),stop)
    quiet,loud,silence=[value for value,_ in written]
    assert 0<quiet<loud<=1
    assert silence==0
    assert levels[-1]==0
    np.testing.assert_array_equal(np.concatenate([chunk for _,chunk in written]),samples)


def test_speaker_meter_ignores_pause_cancel_and_stale_generation():
    with patch('local_mode.voice.read_voice',return_value='silero:xenia'):
        speaker=LocalSpeaker()
    speaker._queue.put(None)  # Помечаем активную работу, не создавая поток озвучки.
    speaker._active.set()
    speaker._phase='SPEAKING'
    speaker._playback_level(.7,speaker.cancel_revision)
    assert speaker.audio_level==.7
    speaker._playback_level(.9,speaker.cancel_revision-1)
    assert speaker.audio_level==.7
    assert speaker.pause()
    assert speaker.audio_level==0
    speaker.resume()
    speaker._playback_state('SYNTHESIZING')
    assert speaker.audio_level==0
    speaker._playback_state('SPEAKING')
    speaker._level_at-=1
    assert speaker.audio_level==0
    speaker.cancel()
    assert speaker.audio_level==0
