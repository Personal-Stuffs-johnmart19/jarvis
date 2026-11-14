"""Фразы до конца Gemini, повторы, код, отмена и ограниченное опережение."""

from concurrent.futures import Future
import threading
import pytest
from local_mode.streaming_speech import StreamingSpeech


class Speaker:
    def __init__(self):
        self.cancel_revision = 0
        self.texts = []
        self.futures = []

    def say_if_current(self, text, revision):
        if revision != self.cancel_revision:
            return
        self.texts.append(text)
        f = Future()
        f.set_result(True)
        self.futures.append(f)
        return f

    def cancel_if_current(self, revision):
        if revision == self.cancel_revision:
            self.cancel_revision += 1
            return self.cancel_revision
        return revision


def test_first_sentence_is_spoken_before_final_and_not_duplicated():
    s = Speaker()
    stream = StreamingSpeech(s, lambda: True, threading.Event())
    text = "Это первая законченная фраза. Вторая фраза продолжается"
    stream.feed(text)
    assert s.texts == ["Это первая законченная фраза."]
    stream.finish({"ok": True, "action": "reply", "reply": text})
    assert s.texts == ["Это первая законченная фраза.", "Вторая фраза продолжается"]


def test_stop_while_waiting_prevents_future_audio_and_reset():
    s = Speaker()
    stream = StreamingSpeech(s, lambda: True, threading.Event())
    s.cancel_revision += 1
    stream.feed("Эта законченная фраза не должна звучать. Продолжение")
    stream.feed("")
    stream.finish({"ok": True, "reply": "Нельзя оживить отменённый ответ."})
    assert s.texts == []


def test_retry_discards_draft_and_confirmation_never_reads_parameters():
    s = Speaker()
    stream = StreamingSpeech(s, lambda: True, threading.Event())
    stream.feed("Это первый завершённый черновик. Продолжение")
    stream.feed("")
    stream.feed("Новый незавершённый черновик")
    stream.finish(
        {
            "ok": False,
            "requires_confirmation": True,
            "action": "cmd_control",
            "reply": "powershell PRIVATE_PARAMETERS",
        }
    )
    assert "Confirmation required" in s.texts[-1]
    assert "PRIVATE_PARAMETERS" not in "".join(s.texts)


def test_code_is_held_for_sanitized_final():
    s = Speaker()
    stream = StreamingSpeech(s, lambda: True, threading.Event())
    text = "```powershell\nRemove-Item C:/private\n```"
    stream.feed(text)
    assert s.texts == []
    stream.finish({"ok": True, "action": "reply", "reply": text})
    assert "Remove-Item" not in "".join(s.texts)


def test_backpressure_can_be_interrupted():
    s = Speaker()
    stop = threading.Event()
    stream = StreamingSpeech(s, lambda: True, stop)
    a = Future()
    b = Future()
    stream.pending.extend([a, b])
    thread = threading.Thread(target=stream._say, args=("Очередь ограничена.",))
    thread.start()
    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive() and not s.texts


def test_mailbox_does_not_block_gemini_on_slow_audio():
    from local_mode.streaming_speech import SpeechStream

    speaker = Speaker()
    enqueued = threading.Event()

    def slow(text, revision):
        future = Future()
        speaker.futures.append(future)
        enqueued.set()
        return future

    speaker.say_if_current = slow
    stop = threading.Event()
    stream = SpeechStream(speaker, lambda: True, stop)
    try:
        text = "Это достаточно длинная законченная фраза. Следующая фраза"
        stream.feed(text)
        assert enqueued.wait(1)
        for index in range(100):
            stream.feed(text + str(index))
        # Незавершённый audio Future не мешает завершить чтение текста.
        speaker.cancel_revision += 1
        stream.finish({"ok": True, "action": "reply", "reply": text + "99"})
        assert not stream.thread.is_alive()
        assert not any(future.done() for future in speaker.futures)
    finally:
        stop.set()
        stream.abort()


def test_structured_model_text_is_not_read_as_parameters():
    from local_mode.results import spoken_message
    assert spoken_message({'ok':True,'reply':'{"command":"private"}'}, locale='ru')=='Структурированный результат показан в чате.'


@pytest.mark.parametrize('controller_name', ['StreamingSpeech', 'SpeechStream'])
@pytest.mark.parametrize('voice', ['supertonic:F1', 'gemini:flash'])
def test_final_owned_speech_uses_selected_locale_in_both_controllers(controller_name, voice):
    from local_mode import streaming_speech
    from local_mode.messages import OwnedText
    speaker = Speaker()
    speaker.voice = voice
    stream = getattr(streaming_speech, controller_name)(speaker, lambda: True, threading.Event())
    stream.finish({'ok': True, 'reply': OwnedText('Queue cleared.')}, locale='ru')
    assert speaker.texts == ['Очередь очищена.']
