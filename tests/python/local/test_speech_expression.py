"""Паузы, XML-границы и ошибки образцов без звука и Computer Use."""
from threading import Event
from unittest.mock import Mock,patch
import xml.etree.ElementTree as ET
import io,wave
import pytest
from local_mode.speech_text import speech_parts,silero_ssml,sapi_xml,speech_speed
from local_mode.voice import LocalSpeaker


def wave_data():
    data=io.BytesIO()
    with wave.open(data,"wb") as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
        output.writeframes(bytes(2400*2))
    return data.getvalue()


def test_sentence_boundaries_paragraphs_and_punctuation_survive():
    parts=speech_parts("Привет! Вы готовы?\n\nТогда начнём… Ещё одна фраза.")
    assert [part.text for part in parts] == ["Привет!","Вы готовы?","Тогда начнём…","Ещё одна фраза."]
    assert [part.pause_ms for part in parts] == [240,500,420,0]
    assert speech_speed(parts[0].text)>speech_speed(parts[1].text)>speech_speed(parts[2].text)


def test_decimal_and_abbreviation_are_not_sentence_boundaries():
    parts=speech_parts("Цена 3.14 руб. за штуку. Адрес: ул. Лесная.")
    assert [p.text for p in parts] == ["Цена 3.14 руб. за штуку.","Адрес: ул. Лесная."]
    assert all(len(p.text)<=160 for p in speech_parts("слово "*100))


def test_only_generated_xml_is_executable_markup():
    text='Привет <audio src="file:///secret"/> & отлично!'
    xml=ET.fromstring(silero_ssml(text))
    assert xml.find(".//audio") is None
    assert text in ''.join(xml.itertext())
    assert xml.find("prosody").get("pitch")=="high"
    sapi=ET.fromstring(sapi_xml("Готовы?\n\nОтлично!"))
    assert sapi.find("silence").get("msec")=="500"


def test_pause_is_written_as_silence_without_blocking_sleep():
    import numpy as np
    from local_mode.speech_audio import play_wav
    chunks=[]
    output=Mock();output.__enter__=Mock(return_value=output);output.__exit__=Mock(return_value=False)
    output.write.side_effect=lambda data:chunks.append(data.copy())
    with patch("sounddevice.OutputStream",return_value=output):play_wav(wave_data(),Event(),300)
    assert sum(len(chunk) for chunk in chunks)==2400+7200
    assert np.all(np.concatenate(chunks)[-7200:]==0)


def test_double_failure_uses_piper_and_does_not_retry_broken_gpu():
    cpu=Mock(wav=Mock(side_effect=RuntimeError("Ошибка синтеза речи")))
    piper=Mock(wav=Mock(return_value=wave_data()))
    with patch("local_mode.voice.read_voice",return_value="supertonic:F1"),patch("local_mode.supertonic_voice.RussianVoice",side_effect=RuntimeError("HTTP 500")) as gpu,patch("local_mode.silero_voice.RussianVoice",return_value=cpu),patch("local_mode.piper_voice.RussianVoice",return_value=piper),patch("local_mode.voice.play_wav") as play:
        speaker=LocalSpeaker()
        try:
            job=speaker.say("Первая фраза.");job.result(timeout=3)
            job=speaker.say("Вторая фраза.");job.result(timeout=3)
            gpu.assert_called_once();cpu.wav.assert_called_once();assert piper.wav.call_count==2
            assert play.call_count==2 and not speaker.error
            assert "Piper CPU" in speaker.backend_status
        finally:speaker.close()


def test_same_voice_save_does_not_stop_preview():
    with patch("local_mode.voice.read_voice",return_value="supertonic:F1"),patch("local_mode.voice.save_voice"):
        speaker=LocalSpeaker()
        with patch.object(speaker,"cancel") as cancel:speaker.configure("supertonic:F1");cancel.assert_not_called()
        with patch.object(speaker,"cancel") as cancel:speaker.configure("supertonic:M1",preserve_playback=True);cancel.assert_not_called()
        speaker.close()


def test_preview_future_exposes_real_synthesis_error():
    with patch("local_mode.voice.read_voice",return_value="silero:xenia"),patch("local_mode.silero_voice.RussianVoice",side_effect=RuntimeError("модель повреждена")):
        speaker=LocalSpeaker()
        try:
            job=speaker.say("Проверка")
            with pytest.raises(RuntimeError,match="модель повреждена"):job.result(timeout=3)
        finally:speaker.close()


def test_emoji_and_markdown_do_not_reach_the_model():
    assert [p.text for p in speech_parts("✅ **Готово!**\n🎵 Музыка играет.")] == ["Готово!","Музыка играет."]


def test_gpu_http_500_reports_reason_and_keeps_details(tmp_path):
    import httpx
    from local_mode import supertonic_voice as module
    engine=module.RussianVoice.__new__(module.RussianVoice)
    engine.process=Mock();engine.process.poll.return_value=None
    engine.generating=Event();engine.base="http://127.0.0.1:12345";engine.model_id="test"
    engine.client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(500,json={"error":{"message":"unsupported Vulkan shader"}})))
    (tmp_path/".cache").mkdir()
    try:
        with patch.object(module,"ROOT",tmp_path),pytest.raises(RuntimeError,match="Неподдерживаемая"):
            engine.wav("Проверка")
        assert "unsupported Vulkan shader" in (tmp_path/".cache/supertonic-last-error.txt").read_text(encoding="utf-8")
    finally:engine.client.close()




def test_output_adapts_to_windows_device_sample_rate_and_stereo():
    import sounddevice as sd
    from local_mode.speech_audio import play_wav
    output=Mock();output.__enter__=Mock(return_value=output);output.__exit__=Mock(return_value=False)
    with patch("sounddevice.OutputStream",side_effect=[sd.PortAudioError("unsupported format"),output]) as create,patch("sounddevice.query_devices",return_value={"default_samplerate":48000,"max_output_channels":2}):
        play_wav(wave_data(),Event())
    assert create.call_args.kwargs == {"samplerate":48000,"channels":2,"dtype":"int16"}
    assert output.write.call_args.args[0].shape[1] == 2


def test_silero_ssml_parser_failure_retries_plain_text_and_remembers():
    from local_mode.silero_voice import RussianVoice
    engine = RussianVoice.__new__(RussianVoice)
    engine._ssml_enabled = True
    tensor = Mock()
    engine.model = Mock()
    engine.model.apply_tts.side_effect = [
        ValueError("Failed to parse SSML: 'NoneType' object has no attribute 'keys'"),
        tensor, tensor,
    ]
    engine.audio("Здравствуйте!", "kseniya")
    engine.audio("Следующая фраза.", "kseniya")
    calls = engine.model.apply_tts.call_args_list
    assert "ssml_text" in calls[0].kwargs
    assert calls[1].kwargs["text"] == "Здравствуйте!"
    assert calls[2].kwargs["text"] == "Следующая фраза."
    assert all(call.kwargs["speaker"] == "kseniya" for call in calls)
    assert not engine._ssml_enabled


def test_silero_does_not_hide_unrelated_synthesis_failure():
    from local_mode.silero_voice import RussianVoice
    engine = RussianVoice.__new__(RussianVoice)
    engine._ssml_enabled = True
    engine.model = Mock()
    engine.model.apply_tts.side_effect = RuntimeError("out of memory")
    with pytest.raises(RuntimeError, match="out of memory"):
        engine.audio("Проверка.")
    engine.model.apply_tts.assert_called_once()
