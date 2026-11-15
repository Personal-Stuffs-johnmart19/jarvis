"""Configured Telegram input without the retired phone transport."""
from concurrent.futures import Future
import threading
from unittest.mock import Mock, patch

from local_mode.remote import RemoteServices


def test_unconfigured_remote_input_starts_no_worker(monkeypatch):
    monkeypatch.delenv("JARVIS_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("JARVIS_TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("JARVIS_BLUETOOTH", "1")  # Old environment settings cannot revive it.
    service = RemoteServices(Mock(), threading.Event(), Mock())
    with patch("telegram_bot.load_config", return_value={}), patch("local_mode.remote.threading.Thread") as thread:
        service.start()
    thread.assert_not_called()
    service.close()


def test_telegram_uses_shared_queue_and_skips_restart_backlog():
    stop = threading.Event()
    future = Future()
    future.set_result({"ok": True, "reply": "Owned reply"})
    submit = Mock(return_value=future)
    service = RemoteServices(submit, stop, Mock())
    update = {"update_id": 11, "message": {
        "chat": {"id": 123}, "from": {"id": 123}, "text": "Owned command",
    }}
    with patch("telegram_bot.get_updates", side_effect=[[{"update_id": 10}], [update]]) as poll, patch("telegram_bot.send_message", side_effect=lambda *a, **kw: stop.set()) as reply:
        service._telegram("owned-token", "123")
    assert poll.call_args_list[0].args == ("owned-token", -1)
    assert poll.call_args_list[1].args == ("owned-token", 11)
    submit.assert_called_once_with("Owned command", source="telegram:123")
    reply.assert_called_once_with("owned-token", "123", "Owned reply", parse_mode="")
    service.close()
