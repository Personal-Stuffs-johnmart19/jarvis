"""Паузы Supertonic: тишина и подготовка следующей фразы без звука/GPU."""
import io
import wave
from threading import Event
from unittest.mock import Mock,patch
import numpy as np
from local_mode.speech_audio import trim_wav_edges
from local_mode.supertonic_voice import RussianVoice


def wav_bytes(audio,rate=1000):
    result=io.BytesIO()
    with wave.open(result,"wb") as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(rate)
        output.writeframes(np.asarray(audio,dtype='<i2').tobytes())
    return result.getvalue()


def test_trims_only_edge_silence_and_preserves_consonant_margin():
    audio=np.concatenate([np.zeros(1000),np.full(1000,10000),np.zeros(1000)])
    data=trim_wav_edges(wav_bytes(audio))
    with wave.open(io.BytesIO(data),'rb') as source:
        trimmed=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2')
    assert len(trimmed)==1200
    np.testing.assert_array_equal(trimmed[80:-120],audio[1000:2000])
    silent=wav_bytes(np.zeros(1000))
    assert trim_wav_edges(silent)==silent


def test_short_sentences_share_request_but_paragraph_pause_survives():
    engine=RussianVoice.__new__(RussianVoice);engine.unavailable=False
    engine.wav=Mock(return_value=wav_bytes(np.ones(1000)*10000));engine.cancel=Mock()
    with patch('local_mode.supertonic_voice.play_wav') as play:
        engine.play('Первая фраза. Вторая фраза.\n\nНовый абзац.',Event())
    assert [call.args[0] for call in engine.wav.call_args_list]==['Первая фраза. Вторая фраза.','Новый абзац.']
    assert [call.args[2] for call in play.call_args_list]==[500,0]


def test_next_generation_overlaps_current_playback():
    engine=RussianVoice.__new__(RussianVoice);engine.unavailable=False
    started=Event();release=Event();calls=[];data=wav_bytes(np.ones(1000)*10000)
    def synthesize(text,speaker):
        calls.append(text)
        if len(calls)==2:
            started.set()
            assert release.wait(2)
        return data
    engine.wav=Mock(side_effect=synthesize);engine.cancel=Mock()
    played=[]
    def play(*args):
        if not played:
            try: assert started.wait(2), 'Next generation waited for playback to finish'
            finally: release.set()
        played.append(args)
    with patch('local_mode.supertonic_voice.play_wav',side_effect=play):
        engine.play('Первый абзац.\n\nВторой абзац.',Event())
    assert len(calls)==2 and len(played)==2
