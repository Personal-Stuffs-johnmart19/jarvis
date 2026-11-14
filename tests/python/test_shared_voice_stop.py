"""Full Stop releases voice requests without closing their shared chat."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_mode.desktop_host import DesktopRuntime
from local_mode.shared_agent import SharedAgent, SharedAgentCancelled


def make_runtime(monkeypatch, emit):
    monkeypatch.setattr("local_mode.embedding_jobs.cancel_all", Mock())
    monkeypatch.setattr("agent.task_queue.cancel_existing", Mock())
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    runtime.stop = threading.Event()
    runtime.shared_agent = SharedAgent(emit)
    runtime.shared_agent.attach("owned-model", "owned-session", "owned-owner")
    runtime.text_provider = Mock()
    runtime.speaker = Mock()
    runtime.event = Mock()
    runtime.emit = Mock()
    return runtime


def registry():
    return SimpleNamespace(validate=Mock(), risky=Mock(return_value=True),
                           execute=Mock(return_value={"ok": True}))


def test_full_stop_releases_voice_request_and_preserves_new_receipts(monkeypatch):
    started, release = threading.Event(), threading.Event()
    events, errors = [], []

    def emit(name, data):
        events.append((name, data))
        if name == "agent.request":
            started.set()
            assert release.wait(1)

    runtime = make_runtime(monkeypatch, emit)
    shared, tools = runtime.shared_agent, registry()
    old = shared.prepare(tools, "owned.action", {"value": "old"})["token"]

    def run():
        try:
            shared.run("owned voice request", runtime.stop)
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert started.wait(1)
        assert runtime.control("stop") == {"ok": True}
        new = shared.prepare(tools, "owned.action", {"value": "new"})["token"]
    finally:
        release.set()
        worker.join(timeout=0.75)

    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], SharedAgentCancelled)
    assert not runtime.stop.is_set()
    assert shared.attached
    assert (shared.model, shared.session, shared.owner) == ("owned-model", "owned-session", "owned-owner")
    assert shared.pending is None
    with pytest.raises(ValueError, match="Expired or consumed"):
        shared.execute(tools, old)
    assert shared.execute(tools, new) == {"ok": True}
    tools.execute.assert_called_once_with("owned.action", {"value": "new"}, approved=True)
    runtime.text_provider.cancel.assert_called_once()
    runtime.speaker.cancel.assert_called_once()
    runtime.emit.assert_called_once_with("agent.stop", {})
    assert events[-1][0] == "agent.cancel"

    shared.emit = lambda name, data: shared.complete(data["id"], "fresh reply") if name == "agent.request" else None
    assert shared.run("next voice request", runtime.stop) == {"ok": True, "reply": "fresh reply"}


def test_full_stop_wins_already_completed_reply_race(monkeypatch):
    runtime = make_runtime(monkeypatch, lambda *_: None)

    def emit(name, data):
        if name == "agent.request":
            runtime.shared_agent.complete(data["id"], "stale reply")
            runtime.control("stop")

    runtime.shared_agent.emit = emit
    with pytest.raises(SharedAgentCancelled):
        runtime.shared_agent.run("owned request", runtime.stop)
    assert runtime.shared_agent.pending is None
    assert runtime.shared_agent.attached and not runtime.stop.is_set()


def test_speech_stop_preserves_active_shared_request_and_receipts(monkeypatch):
    runtime = make_runtime(monkeypatch, lambda *_: None)
    tools = registry()
    token = runtime.shared_agent.prepare(tools, "owned.action", {})["token"]
    revision = runtime.shared_agent.cancel_revision
    runtime.control("speech_stop")
    assert runtime.shared_agent.cancel_revision == revision
    assert token in runtime.shared_agent.receipts
    runtime.text_provider.cancel.assert_not_called()
    runtime.emit.assert_not_called()
    runtime.speaker.cancel.assert_called_once()
