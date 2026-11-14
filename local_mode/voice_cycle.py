"""Единое состояние диалога и защита захвата от собственной озвучки."""
from __future__ import annotations
import threading
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from local_mode.voice import LocalSpeaker


class VoiceCycle:
    def __init__(self, speaker: LocalSpeaker) -> None:
        self.speaker = speaker
        self._lock = threading.RLock()
        self._phase = "ONLINE"
        self._input_revision = 0

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def invalidate_capture(self) -> None:
        """Выключение и повторное включение микрофона не оживляет старую запись."""
        with self._lock:
            self._input_revision += 1

    def capture_token(self) -> tuple[int, int]:
        with self._lock:
            return self._input_revision, self.speaker.activity_revision

    def can_listen(self, muted: bool = False) -> bool:
        return not muted and not self.speaker.microphone_blocked

    def capture_interrupted(self, token: tuple[int, int], muted: bool = False) -> bool:
        return not self.can_listen(muted) or token != self.capture_token()

    def response_token(self) -> int:
        return self.speaker.cancel_revision

    def should_speak(self, token: int) -> bool:
        # Стоп во время ожидания Gemini отменяет будущую озвучку этого ответа,
        # но сохраняет сам запрос и его текстовый результат.
        return token == self.speaker.cancel_revision

    def display_state(self, muted: bool = False, confirmation: bool = False) -> str:
        with self._lock:
            phase = self._phase
        if phase == "OFFLINE":
            return phase
        voice = self.speaker.state
        if voice != "ONLINE":
            return voice
        if phase != "ONLINE":
            return phase
        if confirmation:
            return "CONFIRMATION"
        return "MUTED" if muted else "ONLINE"
