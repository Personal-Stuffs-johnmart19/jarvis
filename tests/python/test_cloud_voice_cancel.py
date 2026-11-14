"""Cancelled remote synthesis cannot pin the audio worker or play late audio."""

import threading
import time
from unittest.mock import patch, Mock
from concurrent.futures import CancelledError, ThreadPoolExecutor
import pytest
from local_mode.gemini_voice import GeminiVoice


def test_cancel_returns_while_remote_transport_is_blocked():
    voice = GeminiVoice()
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()

    def blocked(*_):
        entered.set()
        release.wait(3)
        return b"late audio"

    try:
        with patch.object(
            voice, "_request", side_effect=blocked
        ), ThreadPoolExecutor() as pool:
            task = pool.submit(voice.request, "fixture", "hello", stop)
            assert entered.wait(1)
            stop.set()
            with pytest.raises(CancelledError):
                task.result(timeout=0.5)
    finally:
        release.set()


def test_transport_close_does_not_block_cancel_or_hold_voice_lock():
    voice = GeminiVoice()
    release, entered = threading.Event(), threading.Event()

    def close():
        entered.set()
        release.wait(3)

    client = Mock()
    client.close.side_effect = close
    voice._client = client
    try:
        start = time.monotonic()
        voice.cancel()
        assert time.monotonic() - start < 0.5
        assert entered.wait(1)
        assert voice._client is None
        voice.cancel()
        assert client.close.call_count == 1
    finally:
        release.set()


def test_speaker_leaves_stopping_and_unblocks_microphone_without_late_audio():
    from local_mode.voice import LocalSpeaker

    entered, release = threading.Event(), threading.Event()

    def blocked(*_):
        entered.set()
        release.wait(3)
        return b"late audio"

    with patch.object(GeminiVoice, "_request", side_effect=blocked), patch(
        "local_mode.gemini_voice.play_wav"
    ) as play:
        speaker = LocalSpeaker()
        speaker.voice = "gemini:auto"
        try:
            task = speaker.say("Test")
            assert entered.wait(1)
            assert speaker.microphone_blocked
            speaker.pause()
            speaker.cancel()
            deadline = time.monotonic() + 1
            while speaker.busy and time.monotonic() < deadline:
                time.sleep(0.01)
            assert speaker.state == "ONLINE"
            assert not speaker.microphone_blocked
            assert task.cancelled()
            release.set()
            play.assert_not_called()
        finally:
            release.set()
            speaker.close()
