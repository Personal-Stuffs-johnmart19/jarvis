"""Settings success feedback preserves provenance across the JSON boundary."""
import io
import json
import queue
import threading
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_mode.messages import OwnedText
from local_mode.results import present_result


def test_settings_message_payload_preserves_raw_data_and_chat_rendering():
    raw = "provider detail: C:\\voices\\Failed {0}"
    owned = OwnedText("Speech synthesis failed: {0}", raw)
    result = {"ok": True, "message": owned, "reply": owned, "error": owned, "result": owned, "data": raw}
    payload = json.loads(json.dumps(present_result(result, locale="ru")))
    assert payload["message"] == {"source": "jarvis", "message": owned.template, "values": [raw]}
    assert payload["reply"] == payload["error"] == payload["result"] == "Ошибка озвучки: " + raw
    assert payload["data"] == raw
    assert result["message"] is owned
    for raw_message in ("Microphone selected.", "Failed", raw):
        result = {"ok": True, "message": raw_message}
        assert present_result(result, locale="ru") == result


def test_settings_success_messages_survive_actual_rpc_serialization(monkeypatch):
    from local_mode import desktop_host, camera_devices
    messages = [
        "Speech settings saved. The next response will use the selected voice.",
        "Microphone selected.",
        "The key is protected by Windows DPAPI.",
    ]
    output = io.StringIO()
    runtime = SimpleNamespace(
        interface_locale="ru", start=Mock(), close=Mock(),
        dispatch=lambda method, params: {"ok": True, "message": OwnedText(messages[params["index"]])},
    )
    requests = "".join(json.dumps({"id": index, "method": "control", "params": {"index": index}}) + "\n" for index in range(len(messages)))
    monkeypatch.setattr(desktop_host, "DesktopRuntime", lambda emit: runtime)
    monkeypatch.setattr(camera_devices, "select_camera", Mock())
    monkeypatch.setattr(desktop_host.sys, "stdout", output)
    monkeypatch.setattr(desktop_host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(requests.encode())))
    assert desktop_host._serve() == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [response["result"]["message"] for response in responses] == [
        {"source": "jarvis", "message": message, "values": []} for message in messages
    ]
    runtime.close.assert_called_once()


@pytest.mark.parametrize("owned", [True, False], ids=["owned-runtime", "raw-provider"])
def test_worker_future_retains_owned_error_provenance_and_raw_failure(monkeypatch, owned):
    from local_mode import assistant, desktop_host, remote, speech, windows
    from local_mode.messages import OwnedError, OwnedRuntimeError

    secret = "synthetic-worker-feedback-secret"
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    monkeypatch.setenv("GEMINI_API_KEY", secret)
    raw = "Speech synthesis failed: {0} raw provider diagnostic"
    error = OwnedRuntimeError("Speech synthesis failed: {0}", f"engine detail {secret} {{1}}") if owned else RuntimeError(raw)
    bot = SimpleNamespace(
        tools=SimpleNamespace(special={}), model_connected=None,
        configure_network=Mock(side_effect=error), close=Mock(),
    )
    remote_service = SimpleNamespace(start=Mock(), close=Mock())
    monkeypatch.setattr(assistant, "LocalAgent", Mock(return_value=bot))
    monkeypatch.setattr(remote, "RemoteServices", Mock(return_value=remote_service))
    foreground = Mock(return_value=None)
    monkeypatch.setattr(windows, "WindowController", Mock(return_value=SimpleNamespace(foreground=foreground)))
    recognizer = Mock(side_effect=AssertionError("No microphone access expected"))
    monkeypatch.setattr(speech, "RussianRecognizer", recognizer)

    # Run the real worker around one queued settings action, without starting its
    # surrounding audio/network services. Completion ends the loop deterministically.
    runtime = desktop_host.DesktopRuntime.__new__(desktop_host.DesktopRuntime)
    runtime.settings = SimpleNamespace()
    runtime.stop, runtime.failed = threading.Event(), threading.Event()
    runtime.speaker, runtime.cycle = Mock(), Mock()
    runtime.work = queue.Queue()
    runtime.shared_agent = SimpleNamespace(attached=False)
    runtime.wake_enabled = runtime.connected = False
    runtime.streaming_voice = runtime.remote = runtime.bot = None
    runtime.interface_locale = "ru"
    runtime.write_log, runtime.event, runtime._recover_network = Mock(), Mock(), Mock()
    future = Future()
    future.add_done_callback(lambda _: runtime.stop.set())
    runtime.work.put(desktop_host.Work("settings.network", {"mode": "system"}, future, "synthetic-feedback"))

    runtime._worker()

    assert future.done(), "Worker did not complete the queued action"
    failure = future.exception()
    if owned:
        assert isinstance(failure, OwnedRuntimeError)
        assert failure is not error
        assert failure.payload() == {
            "source": "jarvis", "message": "Speech synthesis failed: {0}",
            "values": ["engine detail [redacted] {1}"],
        }
        assert runtime.write_log.call_args_list[0].args == ("ERR: Ошибка озвучки: engine detail [redacted] {1}",)
        assert secret not in json.dumps(failure.payload())
    else:
        assert isinstance(failure, ValueError) and not isinstance(failure, OwnedError)
        assert str(failure) == raw
        assert runtime.write_log.call_args_list[0].args == ("ERR: " + raw,)
    bot.configure_network.assert_called_once_with("system", "")
    runtime._recover_network.assert_called_once_with(error)
    assert runtime.current_request == "" and runtime.work.unfinished_tasks == 0
    foreground.assert_called_once()
    recognizer.assert_not_called()
    remote_service.start.assert_not_called()
    remote_service.close.assert_called_once()
    bot.close.assert_called_once()
    runtime.speaker.close.assert_called_once()
