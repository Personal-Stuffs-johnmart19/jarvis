"""Built-in speech metadata must be localizable without changing persisted IDs."""
import json
import re
import pytest
from pathlib import Path

from local_mode.voice_settings import neural_voices, validate_voice


def test_builtin_voice_metadata_has_english_source_and_separate_translations():
    root = Path(__file__).resolve().parents[2]
    en = json.loads((root / "apps/desktop/src/locales/en.json").read_text(encoding="utf-8"))
    ru = json.loads((root / "apps/desktop/src/locales/ru.json").read_text(encoding="utf-8"))
    voices = neural_voices()
    assert len({voice["id"] for voice in voices}) == len(voices)
    assert {voice["id"] for voice in voices} >= {"supertonic:F1", "gemini:auto", "piper:denis", "silero:xenia"}
    for voice in voices:
        assert validate_voice(voice["id"]) == voice["id"]
        assert not re.search(r"[А-Яа-яЁё]", voice["name"])
        assert en[voice["name"]] == voice["name"]
        assert ru[voice["name"]]


def test_owned_speech_status_survives_event_boundary_without_translating_diagnostics():
    from unittest.mock import Mock
    from local_mode.messages import OwnedText, text_payload, message
    from local_mode.desktop_host import DesktopRuntime
    raw = "raw provider detail"
    status = OwnedText("Vulkan check failed: {0} Using Silero CPU.", raw)
    assert isinstance(status, str) and raw in status
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    import threading
    runtime.lock = threading.RLock()
    runtime.speaker = Mock(backend_status=status)
    runtime.event, runtime.write_log = Mock(), Mock()
    runtime.publish_speech_status(status)
    payload = runtime.event.call_args.args[1]["text"]
    assert payload == status.payload() and payload["values"] == [raw]
    assert "Проверка Vulkan не пройдена" in message(payload["message"],*payload["values"],locale="ru")
    assert text_payload("Failed") == "Failed"
    runtime.speaker.backend_status = ""
    runtime.publish_speech_status(status)
    assert runtime.event.call_count == 1
    runtime.publish_speech_status("")
    assert runtime.event.call_args.args[1] == {"text":""}
    assert runtime.write_log.call_count == 1


def test_cancelled_or_reconfigured_voice_cannot_replace_current_status(monkeypatch):
    from unittest.mock import Mock
    from local_mode.voice import LocalSpeaker
    from local_mode.messages import OwnedText
    monkeypatch.setattr("local_mode.voice.read_voice",lambda:"supertonic:F1")
    monkeypatch.setattr("local_mode.voice.save_voice",Mock())
    speaker = LocalSpeaker()
    speaker.on_status = Mock()
    generation = speaker._generation
    status = OwnedText("Supertonic 3: {0}", "synthetic GPU")
    speaker._report_status(status,generation,speaker.voice)
    assert speaker.backend_status is status
    speaker.cancel()
    speaker._report_status("stale",generation,speaker.voice)
    assert speaker.backend_status is status
    speaker.configure("supertonic:F2",preserve_playback=True)
    assert speaker.backend_status == "" and speaker.on_status.call_args.args == ("",)
    speaker._report_status("old voice",speaker._generation,"supertonic:F1")
    assert speaker.backend_status == ""
    speaker.close()


def test_status_callbacks_do_not_hold_speaker_lock(monkeypatch):
    import threading
    from unittest.mock import Mock
    from local_mode.voice import LocalSpeaker
    monkeypatch.setattr("local_mode.voice.read_voice", lambda: "supertonic:F1")
    monkeypatch.setattr("local_mode.voice.save_voice", Mock())
    speaker = LocalSpeaker()
    runtime_lock = threading.RLock()
    callback_started = threading.Event()
    snapshot_finished = threading.Event()
    failures = []
    snapshot_locked = threading.Event()

    def callback(text):
        callback_started.set()
        with runtime_lock:
            pass

    def snapshot():
        with runtime_lock:
            snapshot_locked.set()
            if not callback_started.wait(2):
                failures.append("status callback did not start")
                return
            speaker.state
        snapshot_finished.set()

    speaker.on_status = callback
    for publish in (
        lambda: speaker._report_status("ready", speaker._generation, speaker.voice),
        lambda: speaker.configure("supertonic:F2", preserve_playback=True),
    ):
        callback_started.clear()
        snapshot_finished.clear()
        # Acquire the runtime lock before status publication, matching snapshots.
        snapshot_locked.clear()
        reader = threading.Thread(target=snapshot, daemon=True)
        reader.start()
        assert snapshot_locked.wait(2)
        writer = threading.Thread(target=publish, daemon=True)
        writer.start()
        writer.join(3)
        reader.join(3)
        assert not writer.is_alive() and not reader.is_alive()
        assert snapshot_finished.is_set() and not failures
    speaker.close()


def test_full_queue_error_does_not_hold_lock_and_retains_owned_payload(monkeypatch):
    import threading
    from concurrent.futures import Future
    from unittest.mock import Mock
    from local_mode.voice import LocalSpeaker
    from local_mode.messages import OwnedText
    monkeypatch.setattr("local_mode.voice.read_voice", lambda: "silero:xenia")
    speaker = LocalSpeaker()
    for _ in range(speaker._queue.maxsize):
        speaker._queue.put(("queued", speaker.voice, speaker.cancel_revision, Future()))
    observed = []
    def error_callback(text):
        state_read = threading.Event()
        reader = threading.Thread(target=lambda: (speaker.state, state_read.set()), daemon=True)
        reader.start()
        assert state_read.wait(2), "Error callback retained the speaker lock"
        reader.join(2)
        observed.append(text)
    speaker.on_error = error_callback
    assert speaker.say_if_current("hello", speaker.cancel_revision) is None
    assert isinstance(observed[0], OwnedText)
    assert observed[0].payload()["message"] == "The speech queue is full."
    old_revision = speaker.cancel_revision
    speaker.cancel()
    assert speaker.say_if_current("stale", old_revision) is None
    assert speaker._queue.empty()
    speaker.close()


def test_speech_error_event_preserves_provenance_and_rejects_old_error():
    import threading
    from unittest.mock import Mock
    from local_mode.desktop_host import DesktopRuntime
    from local_mode.messages import OwnedText
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    runtime.lock = threading.RLock()
    status = OwnedText("Speech synthesis failed: {0}", "raw diagnostic")
    runtime.speaker = Mock(error=status)
    runtime.event, runtime.write_log = Mock(), Mock()
    runtime.publish_speech_error(status)
    assert runtime.event.call_args.args == ("backend.error", {"text": status.payload()})
    runtime.speaker.error = ""
    runtime.publish_speech_error(status)
    assert runtime.event.call_count == 1


@pytest.mark.parametrize("callback_fails", [False, True])
def test_reconfigure_during_error_callback_does_not_kill_speech_worker(monkeypatch, callback_fails, caplog):
    import pytest
    from unittest.mock import Mock
    from local_mode.voice import LocalSpeaker
    from local_mode.messages import OwnedRuntimeError, OwnedValueError
    monkeypatch.setattr("local_mode.voice.read_voice", lambda: "silero:xenia")
    monkeypatch.setattr("local_mode.voice.save_voice", Mock())
    engine = Mock()
    engine.play.side_effect = [OwnedValueError("The selected Windows voice is no longer installed."), None]
    monkeypatch.setattr("local_mode.silero_voice.RussianVoice", lambda: engine)
    speaker = LocalSpeaker()
    def on_error(text):
        if callback_fails:
            raise RuntimeError("synthetic delivery failure")
        speaker.configure("silero:xenia", preserve_playback=True)
    speaker.on_error = on_error
    try:
        failed = speaker.say("first")
        with pytest.raises(OwnedRuntimeError) as error:
            failed.result(timeout=2)
        assert error.value.template == "The selected Windows voice is no longer installed."
        assert speaker.say("second").result(timeout=2) == ""
        assert engine.play.call_count == 2
        if callback_fails:
            assert "Speech error callback failed" in caplog.text
    finally:
        speaker.close()


def test_cancel_rejects_pending_speech_error_and_cancels_failed_job(monkeypatch):
    import threading
    from concurrent.futures import CancelledError
    from unittest.mock import Mock
    import pytest
    from local_mode.voice import LocalSpeaker
    from local_mode.desktop_host import DesktopRuntime
    monkeypatch.setattr("local_mode.voice.read_voice", lambda: "silero:xenia")
    engine = Mock()
    engine.play.side_effect = RuntimeError("synthetic failure")
    monkeypatch.setattr("local_mode.silero_voice.RussianVoice", lambda: engine)
    speaker = LocalSpeaker()
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    runtime.lock = threading.RLock()
    runtime.speaker = speaker
    runtime.event, runtime.write_log = Mock(), Mock()
    def cancel_before_publication(text):
        speaker.cancel()
        runtime.publish_speech_error(text)
    speaker.on_error = cancel_before_publication
    try:
        failed = speaker.say("hello")
        with pytest.raises(CancelledError):
            failed.result(timeout=2)
        assert speaker.error == ""
        runtime.event.assert_not_called()
        runtime.write_log.assert_not_called()
    finally:
        speaker.close()
