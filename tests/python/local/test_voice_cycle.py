"""Состояния и эхо-защита всего диалога без микрофона и Computer Use."""
import json
import threading
from unittest.mock import Mock, patch
from concurrent.futures import CancelledError
import pytest
from local_mode.voice import LocalSpeaker
from local_mode.voice_cycle import VoiceCycle
from local_mode.speech import RussianRecognizer
from local_mode.speech_audio import play_wav
from tests.python.local.test_voice_controls import wav_data


def new_speaker():
    with patch("local_mode.voice.read_voice",return_value="silero:xenia"):
        return LocalSpeaker()




@pytest.mark.parametrize("cancel", [False, True])
def test_pause_resume_stop_states_and_echo_guard_use_real_player(cancel):
    data,samples=wav_data();speaker=new_speaker();cycle=VoiceCycle(speaker)
    before=cycle.capture_token()
    written=[];first=threading.Event();advance=threading.Event();paused=threading.Event()
    output=Mock()
    def write(chunk):
        written.append(chunk.copy())
        if len(written)==1:
            first.set()
            assert advance.wait(2)
    output.write.side_effect=write
    output.stop.side_effect=paused.set
    backend=Mock()
    backend.play.side_effect=lambda text,stop,voice:play_wav(data,stop)
    errors=[];speaker.on_error=errors.append
    with patch("local_mode.silero_voice.RussianVoice",return_value=backend),patch("sounddevice.OutputStream",return_value=output):
        try:
            job=speaker.say("Проверка ответа")
            assert first.wait(2)
            assert speaker.state=="SPEAKING" and cycle.display_state()=="SPEAKING"
            assert not cycle.can_listen() and cycle.capture_interrupted(before)
            assert speaker.pause()
            assert cycle.display_state()=="PAUSED" and not cycle.can_listen()
            advance.set();assert paused.wait(2)
            assert len(written)==1
            if cancel:
                speaker.cancel()
                with pytest.raises(CancelledError):job.result(timeout=2)
                output.abort.assert_called_once()
                assert len(written)==1
            else:
                speaker.resume()
                job.result(timeout=2)
                import numpy as np
                np.testing.assert_array_equal(np.concatenate(written),samples)
            speaker.wait()
            assert cycle.display_state()=="ONLINE"
            assert speaker.microphone_blocked  # Остаток звука/эхо ещё блокирует запись.
            assert not errors
            with patch("local_mode.voice.time.monotonic",return_value=speaker._mic_ready_at+1):
                assert cycle.can_listen()
                assert cycle.capture_interrupted(before)  # Старая запись не оживает после TTS.
                assert not cycle.capture_interrupted(cycle.capture_token())
        finally:
            advance.set();speaker.close()


def test_cancelled_synthesis_is_not_reported_as_tts_failure_and_next_job_works():
    speaker=new_speaker();entered=threading.Event();release=threading.Event();errors=[]
    backend=Mock()
    def play(text,stop,voice):
        if text=="Первый":
            entered.set();assert release.wait(2)
            raise RuntimeError("HTTP connection aborted by cancellation")
    backend.play.side_effect=play;speaker.on_error=errors.append
    with patch("local_mode.silero_voice.RussianVoice",return_value=backend):
        try:
            job=speaker.say("Первый");assert entered.wait(2)
            speaker.cancel()
            assert speaker.state=="STOPPING"
            release.set()
            with pytest.raises(CancelledError):job.result(timeout=2)
            speaker.say("Следующий").result(timeout=2);speaker.wait()
            assert not errors and not speaker.error and speaker.state=="ONLINE"
        finally:release.set();speaker.close()


def test_recording_is_invalidated_even_if_tts_or_mute_finished_during_whisper():
    speaker=new_speaker();cycle=VoiceCycle(speaker)
    recognizer=RussianRecognizer.__new__(RussianRecognizer)
    recognizer.microphone=None;recognizer.engine="whisper"
    decoder=Mock();decoder.AcceptWaveform.return_value=True
    decoder.Result.return_value=json.dumps({"text":"фраза"})
    recognizer._decoder=Mock(return_value=decoder)
    states=[];token=cycle.capture_token()
    def infer(pcm,rate):
        cycle.invalidate_capture()  # Микрофон выключили и снова включили во время STT.
        return "Эту старую фразу нельзя отправлять модели"
    recognizer._accurate=Mock(side_effect=infer)
    def stream(**options):
        context=Mock()
        context.__enter__=Mock(side_effect=lambda:options["callback"](bytes(6400),3200,None,None))
        context.__exit__=Mock(return_value=False)
        return context
    with patch("sounddevice.query_devices",return_value={"default_samplerate":16000}),patch("sounddevice.RawInputStream",side_effect=stream):
        assert recognizer.listen(interrupted=lambda:cycle.capture_interrupted(token),on_state=states.append)==""
    assert states==["LISTENING","RECOGNIZING"]
    speaker.close()


def test_blocked_capture_never_opens_device_or_submits_self_speech():
    speaker=new_speaker();cycle=VoiceCycle(speaker)
    recognizer=RussianRecognizer.__new__(RussianRecognizer)
    speaker._paused.set()
    from concurrent.futures import Future
    speaker._queue.put(("Собственный ответ", "silero:xenia",0,Future()))
    with patch("sounddevice.RawInputStream") as microphone,patch("sounddevice.query_devices") as devices:
        assert recognizer.listen(interrupted=lambda:not cycle.can_listen())==""
        microphone.assert_not_called();devices.assert_not_called()
    speaker.cancel();speaker.close()


def test_finished_tts_during_whisper_invalidates_capture_even_without_busy_flag():
    speaker=new_speaker();cycle=VoiceCycle(speaker);token=cycle.capture_token()
    backend=Mock()
    with patch("local_mode.silero_voice.RussianVoice",return_value=backend):
        speaker.say("Короткое уведомление").result(timeout=2)
        speaker.wait()
    assert cycle.can_listen()  # Имитация без PCM уже завершилась.
    assert cycle.capture_interrupted(token)
    speaker.close()


def test_stop_during_gemini_keeps_thinking_but_suppresses_only_current_speech():
    speaker=new_speaker();cycle=VoiceCycle(speaker)
    cycle.set_phase("THINKING");token=cycle.response_token()
    assert cycle.should_speak(token)
    speaker.cancel()
    assert cycle.display_state()=="THINKING"
    assert not cycle.should_speak(token)
    assert cycle.should_speak(cycle.response_token())
    speaker.close()


def test_new_pending_job_after_stop_does_not_show_stopping_old_answer():
    speaker=new_speaker();speaker.cancel()
    from concurrent.futures import Future
    speaker._queue.put(("Новый ответ","silero:xenia",speaker.cancel_revision,Future()))
    assert speaker.state=="SYNTHESIZING"
    speaker.cancel();speaker.close()


def test_agent_emits_thinking_for_requests_and_processing_for_tools():
    from google.genai import types
    from local_mode.assistant import LocalAgent
    from local_mode.config import Settings
    def response(part):
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model",parts=[part]))])
    replies=[response(types.Part(function_call=types.FunctionCall(name="system_status",args={}))),response(types.Part(text="Проверка завершена."))]
    phases=[]
    with patch("local_mode.assistant.WindowController"),patch("local_mode.assistant.YouTubePlayer"),patch("local_mode.assistant.gemini.create_client"):
        bot=LocalAgent(Settings());bot.on_state=phases.append
        with patch.object(bot.router,"generate",side_effect=replies),patch.object(bot.tools,"execute",return_value={"ok":True,"result":"Данные получены"}):
            assert bot.run("Проверь загрузку процессора и объясни результат")["ok"]
    assert phases==["THINKING","PROCESSING","THINKING"]


def test_tts_failure_releases_microphone_and_does_not_stick_in_speaking():
    speaker=new_speaker();cycle=VoiceCycle(speaker);errors=[];speaker.on_error=errors.append
    backend=Mock();backend.play.side_effect=RuntimeError("Устройство вывода недоступно")
    with patch("local_mode.silero_voice.RussianVoice",return_value=backend):
        try:
            with pytest.raises(RuntimeError,match="недоступно"):
                speaker.say("Ответ").result(timeout=2)
            speaker.wait()
            assert cycle.display_state()=="ONLINE" and cycle.can_listen()
            assert len(errors)==1
        finally:speaker.close()


@pytest.mark.parametrize("prefix",["Джарвис", "Jarvis", "jarvis", "Джарвиз"])
def test_jarvis_wake_and_free_mode_strip_name(prefix):
    from local_mode.speech import voice_command
    assert voice_command(prefix+", поставь видео на паузу",True)=="поставь видео на паузу"
    assert voice_command(prefix+", поставь видео на паузу",False)=="поставь видео на паузу"
    assert voice_command("поставь видео на паузу",True)==""
