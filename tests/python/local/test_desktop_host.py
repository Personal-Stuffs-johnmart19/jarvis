"""Проверки границы Tauri/Python без сети, микрофона и экрана."""

from concurrent.futures import Future
import json
import queue
import threading
from unittest.mock import Mock, patch
import pytest
from pydantic import ValidationError
from local_mode.desktop_host import DesktopRuntime, Envelope, Work
from local_mode.desktop_launch import desktop_binary


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 1, "method": "chat", "version": 2},
        {"id": -1, "method": "chat"},
        {"id": "1", "method": "chat"},
        {"id": 1, "method": "chat", "params": []},
        {"id": 1, "method": "chat", "secret": True},
    ],
)
def test_protocol_rejects_wrong_schema(payload):
    with pytest.raises(ValidationError):
        Envelope.model_validate(payload)


def runtime():
    value = DesktopRuntime.__new__(DesktopRuntime)
    value.stop = threading.Event()
    value.failed = threading.Event()
    value.speaker = Mock()
    value.cycle = Mock()
    value.work = queue.Queue(maxsize=1)
    return value


def test_stop_does_not_wait_for_agent_queue():
    value = runtime()
    value.work.put(Work("chat", {}, Future(), "busy"))
    assert value.control("stop")["ok"]
    value.speaker.cancel.assert_called_once()
    assert value.work.qsize() == 1


def test_shutdown_completes_pending_requests():
    value = runtime()
    future = Future()
    value.work.put(Work("tool", {}, future, "waiting"))
    value._drain_pending()
    with pytest.raises(RuntimeError, match="backend has stopped"):
        future.result(timeout=0.1)
    assert value.work.unfinished_tasks == 0


def test_unknown_rpc_and_invalid_chat_fail_closed():
    value = runtime()
    with pytest.raises(ValueError):
        value.dispatch("shell", {"command": "calc"})
    for text in ("", None, 123, "x" * 12001):
        with pytest.raises(ValueError):
            value.submit("chat", {"text": text})
    value.failed.set()
    with pytest.raises(RuntimeError):
        value.submit("chat", {"text": "привет"})


def test_executable_checksum_is_required(tmp_path):
    folder = tmp_path / "desktop-bin"
    folder.mkdir()
    (folder / "jarvis-desktop.exe").write_bytes(b"modified")
    (folder / "manifest.json").write_text(json.dumps({"sha256": "wrong"}))
    with pytest.raises(ValueError, match="сумма"):
        desktop_binary(tmp_path)


def test_snapshot_never_returns_credentials(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    with patch("local_mode.credentials.load_key", return_value="private-api-key"):
        value = DesktopRuntime(lambda *args: None)
        state = value.snapshot()
        assert state["key_configured"]
        assert "private-api-key" not in json.dumps(state)
        assert state["muted"] and state["silent"]
        value.close()


def test_webview_install_is_verified_before_execution(tmp_path):
    from local_mode.desktop_launch import ensure_webview

    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b"bootstrap-fixture"
    with patch(
        "local_mode.desktop_launch.webview_available", side_effect=[False, True]
    ), patch("urllib.request.urlopen", return_value=response), patch(
        "local_mode.desktop_launch.subprocess.run"
    ) as run:
        ensure_webview(tmp_path)
        assert run.call_count == 2
        assert "Get-AuthenticodeSignature" in run.call_args_list[0].args[0][-1]
        assert "/silent" in run.call_args_list[1].args[0]
    with patch(
        "local_mode.desktop_launch.webview_available", return_value=False
    ), patch("urllib.request.urlopen", return_value=response), patch(
        "local_mode.desktop_launch.subprocess.run",
        side_effect=RuntimeError("Untrusted signature"),
    ) as run:
        with pytest.raises(RuntimeError):
            ensure_webview(tmp_path)
        assert run.call_count == 1


def test_import_failure_drains_queue_and_marks_backend_offline(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    value = DesktopRuntime(lambda *args: None)
    future = Future()
    value.work.put(Work("tool", {}, future, "pending"))
    value._worker = Mock(side_effect=ImportError("missing package"))
    value._safe_worker()
    assert value.failed.is_set() and future.done()
    assert value.cycle.display_state() == "OFFLINE"
    assert value.work.unfinished_tasks == 0


def test_network_failure_restarts_recovery_only_once(monkeypatch):
    import httpx

    monkeypatch.delenv("JARVIS_DESKTOP_TEST", raising=False)
    value = runtime()
    value.network_thread = None
    value.write_log = Mock()
    thread = Mock()
    thread.is_alive.return_value = True
    with patch(
        "local_mode.network.startup.start_network_check", return_value=thread
    ) as start:
        value._recover_network(httpx.ConnectError("offline"))
        value._recover_network(httpx.ConnectError("offline"))
        assert start.call_count == 1 and start.call_args.kwargs["initialize"] is False
        value._recover_network(ValueError("quota is not network"))
        assert start.call_count == 1


def test_native_failure_keeps_stderr_and_exit_code(tmp_path, capsys):
    from types import SimpleNamespace
    from local_mode.desktop_launch import launch

    with patch(
        "local_mode.desktop_launch.desktop_binary", return_value=tmp_path / "app.exe"
    ), patch("local_mode.desktop_launch.ensure_webview"), patch(
        "local_mode.desktop_launch.subprocess.run",
        return_value=SimpleNamespace(returncode=9),
    ) as run:
        assert launch(tmp_path) == 9
        assert run.call_args.kwargs["stdout"] is run.call_args.kwargs["stderr"]
        assert (tmp_path / "data/logs/desktop-native.log").exists()
    assert "0x00000009" in capsys.readouterr().out


@pytest.mark.parametrize(
    "filename",
    ["../other.exe", "C:/other.exe", "jarvis-desktop.exe:stream", "other.exe"],
)
def test_manifest_rejects_unmanaged_binary(tmp_path, filename):
    folder = tmp_path / "desktop-bin"
    folder.mkdir()
    (folder / "manifest.json").write_text(
        json.dumps({"file": filename, "sha256": "wrong"})
    )
    with pytest.raises(ValueError, match="имя"):
        desktop_binary(tmp_path)


def test_versioned_binary_can_coexist_with_running_release(tmp_path):
    import hashlib

    folder = tmp_path / "desktop-bin"
    folder.mkdir()
    (folder / "jarvis-desktop.exe").write_bytes(b"old")
    content = b"new release"
    digest = hashlib.sha256(content).hexdigest()
    binary = folder / f"jarvis-desktop-{digest}.exe"
    binary.write_bytes(content)
    (folder / "manifest.json").write_text(
        json.dumps({"file": binary.name, "sha256": digest})
    )
    assert desktop_binary(tmp_path) == binary
    assert (folder / "jarvis-desktop.exe").read_bytes() == b"old"


def test_workbench_local_speech_returns_prompt_without_running_llm(monkeypatch):
    from local_mode.config import Settings
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    speaker=Mock();speaker.microphone_blocked=False
    cycle=Mock();cycle.can_listen.return_value=False
    cycle.capture_interrupted.return_value=False
    bot=Mock();bot.tools.special={}
    windows=Mock();windows.foreground.return_value=None
    recognizer=Mock();recognizer.listen.return_value="включи музыку на YouTube"
    with patch("local_mode.desktop_host.LocalSpeaker",return_value=speaker), patch("local_mode.desktop_host.VoiceCycle",return_value=cycle), patch("local_mode.assistant.LocalAgent",return_value=bot), patch("local_mode.windows.WindowController",return_value=windows), patch("local_mode.speech.RussianRecognizer",return_value=recognizer), patch("local_mode.remote.RemoteServices"):
        value=DesktopRuntime(lambda *_:None,settings=Settings())
        value.muted=False
        result=value.submit("speech.recognize",{})
        worker=threading.Thread(target=value._worker,daemon=True)
        worker.start()
        try:
            assert result.result(timeout=3)=={"ok":True,"text":"включи музыку на YouTube","cancelled":False}
            bot.run.assert_not_called()
            recognizer.listen.assert_called_once()
            speaker.cancel.assert_called_once()
        finally:
            value.stop.set();worker.join(timeout=3)
        assert not worker.is_alive()


def test_ui_speech_lease_is_owner_bound_once_only_and_respects_cancel():
    from types import SimpleNamespace
    value = runtime()
    value.shared_agent = SimpleNamespace(lock=threading.RLock(), attached=True, owner="owner", session="session")
    value.services = SimpleNamespace(aliases={})
    value.current_request = ""
    value.silent_mode = False
    value.speaker.cancel_revision = 7
    args = {"owner":"owner", "session":"session", "run":"run"}
    assert value.dispatch("agent.ui_speech", {**args, "owner":"other", "phase":"begin"})["stale"]
    value.speaker.cancel.assert_not_called()
    value.dispatch("agent.ui_speech", {**args,"phase":"begin"})
    value.dispatch("agent.ui_speech", {**args,"phase":"end","text":"answer"})
    value.speaker.say_if_current.assert_called_once_with("answer",7)
    assert value.dispatch("agent.ui_speech", {**args,"phase":"end","text":"replay"})["stale"]
    value.dispatch("agent.ui_speech", {**args,"phase":"begin"})
    value.silent_mode = True
    value.dispatch("agent.ui_speech", {**args,"phase":"end","text":"silent"})
    assert value.speaker.say_if_current.call_count == 1

@pytest.mark.parametrize("method", ["companions.open", "companions.status"])
def test_retired_companion_gui_rpc_is_unsupported_without_starting_a_broker(method):
    value = runtime()
    with patch("local_mode.companions.GeminiBroker") as broker:
        with pytest.raises(ValueError, match="Unknown application method"):
            value.dispatch(method, {"name": "terax"})
        broker.assert_not_called()
