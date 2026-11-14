"""Озвучка завершённых фраз потока с ограниченным опережением и отменой."""

from __future__ import annotations
from collections import deque
from concurrent.futures import Future
import re
from typing import Callable
from local_mode.results import spoken_message
from local_mode.messages import message


class StreamingSpeech:
    """Один ответ: максимум две фразы в очереди, F9 отменяет и будущие фразы.

    Работает в потоке модели, не в audio callback. Backpressure не накапливает
    неограниченные очереди и не мешает независимым кнопкам управления.
    """

    def __init__(self, speaker, enabled: Callable[[], bool], stop):
        self.speaker = speaker
        self.enabled = enabled
        self.stop = stop
        self.revision = speaker.cancel_revision
        self.text = ""
        self.offset = 0
        self.spoken = 0
        self.pending: deque[Future] = deque()
        self.started = False
        self.blocked = False
        self.cancelled = False

    def active(self) -> bool:
        if (
            self.revision != self.speaker.cancel_revision
            or self.stop.is_set()
            or not self.enabled()
        ):
            self.cancelled = True
        return not self.cancelled

    def reset(self) -> None:
        # Новый шаг, retry или резервная модель не должны склеивать черновики.
        if not self.active():
            return
        if self.started:
            self.revision = self.speaker.cancel_if_current(self.revision)
        self.text = ""
        self.offset = self.spoken = 0
        self.pending.clear()
        self.started = self.blocked = False

    def _say(self, text: str) -> None:
        if not self.active() or not text.strip():
            return
        while self.pending and self.pending[0].done():
            self.pending.popleft()
        while len(self.pending) >= 2:
            if not self.active():
                return
            if self.pending[0].done():
                self.pending.popleft()
            else:
                self.stop.wait(0.025)
        future = self.speaker.say_if_current(text, self.revision)
        if future is not None:
            self.pending.append(future)
            self.started = True

    def feed(self, text: str) -> None:
        if not text:
            self.reset()
            return
        if not self.active():
            return
        if self.text and not text.startswith(self.text):
            self.reset()
        self.text = text
        voice = getattr(self.speaker, "voice", "")
        if isinstance(voice, str) and voice.startswith("gemini:"):
            return  # Выбор Flash/Lite зависит от длины полного ответа, не первой фразы.
        # Код/JSON/таблицы озвучиваются только после итоговой фильтрации.
        if self.blocked or re.search(r"```|(?m:^\s*[|{])", text):
            self.blocked = True
            return
        remaining = text[self.offset :]
        # Требуем следующий пробел/текст: точка в дроби и недописанный токен
        # не образуют завершённое предложение. Аббревиатуры ждут продолжения.
        for match in re.finditer(r'[.!?…][»”")]*(?=\s+\S)', remaining):
            end = self.offset + match.end()
            clause = text[self.offset : end]
            if len(clause.strip()) < 24 or re.search(
                r"\b(?:т|д|п|г|ул|им|стр|руб|мин|сек)\.$", clause
            ):
                continue
            if self.spoken + len(clause) > 1100:
                break
            self._say(spoken_message({"ok": True, "reply": clause}))
            self.spoken += len(clause)
            self.offset = end
            # Индексы match относятся к исходному remaining, не к новому offset.
            break
        if self.offset and text[self.offset :].strip() and self.offset < len(text):
            # Следующая feed() обработает очередную фразу; finish() — остаток.
            return

    def finish(self, result: dict, *, locale: str = "en") -> None:
        if not self.active():
            return
        voice = getattr(self.speaker, "voice", "")
        if isinstance(voice, str) and voice.startswith("gemini:"):
            self._say(spoken_message(result, max_chars=16000, locale=locale))
            return
        final = spoken_message(result, locale=locale)
        if (
            result.get("ok")
            and result.get("action") == "reply"
            and result.get("reply") == self.text
            and not self.blocked
        ):
            tail = self.text[self.offset :]
            if self.spoken + len(tail) > 1200:
                tail = (
                    tail[: max(0, 1100 - self.spoken)].rsplit(" ", 1)[0]
                    + " " + message("Full response is in chat.", locale=locale)
                )
            self._say(spoken_message({"ok": True, "reply": tail}, locale=locale))
        else:
            self.reset()
            self._say(final)

    def abort(self) -> None:
        if self.started and self.active():
            self.revision = self.speaker.cancel_if_current(self.revision)
        self.cancelled = True


class SpeechStream:
    """Однослотовый mailbox: медленный TTS не тормозит чтение текста Gemini.

    Синхронный контроллер работает в своём потоке. Промежуточный накопленный
    текст заменяется последним; конечный результат не теряется. finish ждёт
    постановки хвоста в LocalSpeaker, удерживая STT до эхо-защиты плеера.
    """

    def __init__(self, speaker, enabled, stop):
        import threading

        self.speaker, self.enabled, self.stop = speaker, enabled, stop
        self.expected = speaker.cancel_revision
        self.condition = threading.Condition()
        self.latest = None
        self.final = None
        self.final_locale = "en"
        self.inner = None
        self.closed = False
        self.epoch = 0
        self.thread = threading.Thread(
            target=self._run, name="Jarvis-stream-TTS", daemon=True
        )
        self.thread.start()

    def feed(self, text: str) -> None:
        with self.condition:
            if self.closed or self.speaker.cancel_revision != self.expected:
                return
            if not text:
                self.epoch += 1
                if self.inner:
                    self.inner.abort()
                    self.expected = self.inner.revision
                    self.inner = None
                self.latest = None
            else:
                self.latest = (self.epoch, text[:64000])
            self.condition.notify_all()

    def reset(self) -> None:
        self.feed("")

    def finish(self, result: dict, *, locale: str = "en") -> None:
        with self.condition:
            self.final = result
            self.final_locale = locale
            self.condition.notify_all()
        while self.thread.is_alive():
            self.thread.join(0.05)
            if self.stop.is_set():
                self.abort()

    def abort(self) -> None:
        with self.condition:
            self.closed = True
            if self.inner:
                self.inner.abort()
            self.condition.notify_all()

    def _run(self) -> None:
        try:
            while True:
                with self.condition:
                    while (
                        self.latest is None and self.final is None and not self.closed
                    ):
                        self.condition.wait(0.1)
                        if self.stop.is_set():
                            return
                    if self.closed:
                        return
                    latest, self.latest = self.latest, None
                    result = self.final if latest is None else None
                    final_locale = self.final_locale
                    if (
                        self.inner is None
                        and self.speaker.cancel_revision == self.expected
                    ):
                        self.inner = StreamingSpeech(
                            self.speaker, self.enabled, self.stop
                        )
                    inner = self.inner
                if latest and inner:
                    inner.feed(latest[1])
                if result is not None:
                    if inner:
                        inner.finish(result, locale=final_locale)
                    return
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()
