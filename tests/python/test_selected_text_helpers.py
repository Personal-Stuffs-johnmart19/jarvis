"""Selected helper route contracts: fake callbacks only, no provider/SDK calls."""
from concurrent.futures import Future, ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import types

from local_mode import model, text_helpers
from local_mode.messages import OwnedRuntimeError, OwnedValueError
from local_mode.services import ServiceRegistry, obj


@pytest.fixture(autouse=True)
def no_sdk(monkeypatch):
    cloud = Mock(side_effect=AssertionError("Offline test must not open a Gemini client"))
    monkeypatch.setattr(model, "create_client", cloud)
    return cloud


def broker_with_reply(text="selected fixture"):
    events = []
    broker = None
    def emit(name, data):
        events.append((name, data))
        if name == "helper.text.request":
            broker.complete({key: data[key] for key in ("id", "owner", "session", "route")} | {"text": text})
    broker = text_helpers.SharedTextProvider(emit)
    broker.attach("owner", "session", "local-fixture")
    return broker, events


def test_content_model_routes_current_provider_and_restores_nested_context(no_sdk):
    calls = []
    provider = lambda request, **kwargs: calls.append((request, kwargs)) or {"text": "local answer"}
    assert model.current_text_provider() is None
    with model.using_text_provider(provider):
        response = model.ContentModel("legacy-model-name", system_instruction="Read-only fixture").generate_content("fixture prompt")
        assert response.text == "local answer"
        assert model.current_text_provider() is provider
        with model.using_text_provider(lambda *args, **kwargs: {"text": "inner"}):
            assert model.generate_content(contents="inner fixture").text == "inner"
        assert model.current_text_provider() is provider
    assert model.current_text_provider() is None
    assert calls[0][0]["prompt"] == "fixture prompt"
    assert calls[0][0]["system"].startswith("Read-only fixture")
    no_sdk.assert_not_called()


def test_json_config_and_plaintext_parts_keep_original_contents(no_sdk):
    calls = []
    original = 'C:\\fixture\\name "quoted"\nПривет'
    with model.using_text_provider(lambda request, **kwargs: calls.append(request) or {"text": '{"ok":true}'}):
        result = model.generate_content(contents=[types.Content(role="user", parts=[types.Part(text=original)]), "tail"],
            config={"response_mime_type": "application/json", "max_output_tokens": 9000, "temperature": 0.25})
    assert json.loads(result.text) == {"ok": True}
    assert calls[0]["prompt"] == original + "\n\ntail"
    assert calls[0]["json_mode"] is True
    assert calls[0]["max_output_tokens"] == 4096
    assert calls[0]["temperature"] == 0.25
    no_sdk.assert_not_called()


@pytest.mark.parametrize("contents,config", [
    ([types.Part.from_bytes(data=b"synthetic-image", mime_type="image/png")], {}),
    ([types.Part(function_call=types.FunctionCall(name="not_a_text_tool", args={}))], {}),
    ("fixture", {"tools": [{"google_search": {}}]}),
    ("fixture", {"response_mime_type": "application/octet-stream"}),
    ("fixture", {"temperature": 3}),
    ("fixture", {"max_output_tokens": -1}),
])
def test_selected_model_rejects_non_text_or_unsupported_config_before_callback(contents, config, no_sdk):
    provider = Mock(return_value={"text": "must not run"})
    with model.using_text_provider(provider), pytest.raises((OwnedValueError, ValueError)):
        model.generate_content(contents=contents, config=config)
    provider.assert_not_called()
    no_sdk.assert_not_called()


def test_explicit_zero_output_limit_is_not_silently_replaced(no_sdk):
    provider = Mock(return_value={"text": "must not run"})
    with model.using_text_provider(provider), pytest.raises(OwnedValueError, match="limits"):
        model.generate_content(contents="fixture", config={"max_output_tokens": 0})
    provider.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"prompt": " "}, {"prompt": "я" * 24001}, {"prompt": "\\" * 30000},
    {"prompt": "\x00" * 10000}, {"max_output_tokens": True}, {"max_output_tokens": 4097},
    {"timeout_ms": 999}, {"timeout_ms": 120001}, {"temperature": float("nan")},
    {"temperature": float("inf")}, {"temperature": True}, {"json_mode": 1}, {"api_key": "not-accepted"},
])
def test_text_request_limits_include_json_escape_ipc_overhead(changes):
    with pytest.raises(OwnedValueError):
        text_helpers.validate_text_request({"prompt": "fixture", **changes})


def test_text_request_boundary_preserves_unicode_and_wire_bound():
    request = text_helpers.validate_text_request({"prompt": "я" * 20000, "json_mode": True, "max_output_tokens": 1, "timeout_ms": 1000})
    assert request["prompt"] == "я" * 20000
    assert len(json.dumps(request, ensure_ascii=False).encode()) <= text_helpers.MAX_WIRE_BYTES


def test_parent_relay_passes_cancellation_and_deadline_without_cloud_fallback(no_sdk):
    stop = threading.Event()
    provider = Mock(return_value={"text": "fixture"})
    with model.using_text_provider(provider):
        result = model.relay_text_request({"prompt": "fixture"}, cancel_flag=stop, deadline=1234.0)
    assert result == {"text": "fixture"}
    assert provider.call_args.kwargs == {"cancel_flag": stop, "deadline": 1234.0}
    with model.using_text_provider(Mock(side_effect=OwnedRuntimeError("selected route failed"))):
        with pytest.raises(OwnedRuntimeError, match="selected route failed"):
            model.ContentModel().generate_content("fixture")
    no_sdk.assert_not_called()


def test_unbound_parent_relay_refuses_without_sdk(no_sdk):
    with pytest.raises(OwnedValueError, match="no longer available"):
        model.relay_text_request({"prompt": "fixture"})
    no_sdk.assert_not_called()


def test_shared_broker_success_preserves_identity_and_rejects_duplicate():
    broker, events = broker_with_reply()
    assert broker.binding()({"prompt": "fixture"}) == {"text": "selected fixture"}
    request = events[0][1]
    assert {key: request[key] for key in ("owner", "session", "route")} == {"owner": "owner", "session": "session", "route": "local-fixture"}
    with pytest.raises(OwnedValueError, match="Expired"):
        broker.complete({key: request[key] for key in ("id", "owner", "session", "route")} | {"text": "duplicate"})
    assert broker.pending is None


@pytest.mark.parametrize("field", ["owner", "session", "route", "id"])
def test_broker_rejects_wrong_completion_identity_without_consuming_request(field):
    requested = threading.Event()
    events = []
    def emit(name, data):
        events.append((name, data))
        if name == "helper.text.request":
            requested.set()
    broker = text_helpers.SharedTextProvider(emit)
    broker.attach("owner", "session", "local-fixture")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(broker.binding(), {"prompt": "fixture"})
        assert requested.wait(1)
        request = events[0][1]
        reply = {key: request[key] for key in ("id", "owner", "session", "route")} | {"text": "accepted"}
        try:
            with pytest.raises(OwnedValueError, match="Expired"):
                broker.complete(reply | {field: "wrong"})
            broker.complete(reply)
            assert future.result(timeout=1) == {"text": "accepted"}
        finally:
            broker.cancel()


@pytest.mark.parametrize("action", ["cancel", "route_change", "detach"])
def test_pending_broker_request_is_revoked_and_late_completion_rejected(action):
    requested = threading.Event()
    events = []
    broker = text_helpers.SharedTextProvider(lambda name, data: (events.append((name, data)), requested.set() if name == "helper.text.request" else None))
    broker.attach("owner", "session", "local-fixture")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(broker.binding(), {"prompt": "fixture"})
        assert requested.wait(1)
        request = events[0][1]
        if action == "cancel":
            broker.cancel()
        elif action == "route_change":
            broker.attach("owner", "session", "different-route")
        else:
            broker.detach()
        with pytest.raises(OwnedRuntimeError):
            future.result(timeout=1)
    with pytest.raises(OwnedValueError, match="Expired"):
        broker.complete({key: request[key] for key in ("id", "owner", "session", "route")} | {"text": "late"})
    assert broker.pending is None


def test_already_cancelled_request_never_emits_model_work():
    events = []
    broker = text_helpers.SharedTextProvider(lambda *args: events.append(args))
    broker.attach("owner", "session", "local-fixture")
    stop = threading.Event()
    stop.set()
    with pytest.raises(OwnedRuntimeError, match="stopped"):
        broker.binding()({"prompt": "fixture"}, cancel_flag=stop)
    assert events == []


def test_expired_deadline_does_not_emit_model_work():
    events = []
    broker = text_helpers.SharedTextProvider(lambda *args: events.append(args))
    broker.attach("owner", "session", "local-fixture")
    with pytest.raises(OwnedRuntimeError, match="timed out"):
        broker.binding()({"prompt": "fixture"}, deadline=time.monotonic() - 1)
    assert not any(name == "helper.text.request" for name, _ in events)


def test_broker_short_deadline_times_out_and_revokes_request():
    events = []
    broker = text_helpers.SharedTextProvider(lambda *args: events.append(args))
    broker.attach("owner", "session", "local-fixture")
    with pytest.raises(OwnedRuntimeError, match="timed out"):
        broker.binding()({"prompt": "fixture"}, deadline=time.monotonic() + 0.02)
    assert broker.pending is None
    assert events[-1][0] == "helper.text.cancel"


@pytest.mark.parametrize("race", ["cancel", "deadline", "route_change"])
def test_completed_response_is_rechecked_before_becoming_success(monkeypatch, race):
    stop = threading.Event()
    real_clock = time.monotonic
    offset = [0]
    broker, events = broker_with_reply()
    class BoundaryFuture(Future):
        def result(self, *args, **kwargs):
            value = super().result(*args, **kwargs)
            if race == "cancel":
                stop.set()
            elif race == "deadline":
                offset[0] = 1000
            else:
                broker.attach("owner", "session", "new-route")
            return value
    monkeypatch.setattr(text_helpers, "Future", BoundaryFuture)
    monkeypatch.setattr(time, "monotonic", lambda: real_clock() + offset[0])
    try:
        with pytest.raises(OwnedRuntimeError):
            broker.binding()({"prompt": "fixture"}, cancel_flag=stop, deadline=real_clock() + 1)
    finally:
        offset[0] = 0
    assert broker.pending is None


def test_service_registry_binds_selected_runtime_and_restores_context(no_sdk):
    broker, events = broker_with_reply("service answer")
    runtime = SimpleNamespace(text_provider=broker, event=Mock())
    registry = ServiceRegistry(runtime, lambda: Mock())
    registry.register("qa.text", "Owned fixture", obj(), lambda _: model.ContentModel().generate_content("service fixture").text)
    assert registry.execute("qa.text", {}) == "service answer"
    assert events[0][1]["prompt"] == "service fixture"
    assert model.current_text_provider() is None
    no_sdk.assert_not_called()


def test_service_registry_preserves_existing_captured_provider(no_sdk):
    runtime_provider = SimpleNamespace(binding=Mock(side_effect=AssertionError("must preserve original route")))
    runtime = SimpleNamespace(text_provider=runtime_provider, event=Mock())
    registry = ServiceRegistry(runtime, lambda: Mock())
    registry.register("qa.text", "Owned fixture", obj(), lambda _: model.ContentModel().generate_content("fixture").text)
    with model.using_text_provider(lambda request, **kwargs: {"text": "captured answer"}):
        assert registry.execute("qa.text", {}) == "captured answer"
    runtime_provider.binding.assert_not_called()
    no_sdk.assert_not_called()


@pytest.mark.parametrize("fail", [False, True])
def test_actual_owned_helper_worker_relays_selected_model_without_cloud_fallback(monkeypatch, no_sdk, fail):
    from agent.executor import _call_tool_with_timeout
    # A child process cannot inherit pytest's mocks; its own hard cloud guard
    # prevents any network/API call even if selected-provider routing regresses.
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    monkeypatch.setenv("JARVIS_QA_ALLOW_CLOUD", "0")
    requests = []
    def selected(request, **kwargs):
        requests.append((request, kwargs))
        assert "print(42)" in request["prompt"]
        if fail:
            raise OwnedRuntimeError("selected fixture route unavailable")
        return {"text": "selected worker explanation"}
    parent = SimpleNamespace(validate=Mock(), risky=Mock(return_value=False), execute=Mock())
    with model.using_text_provider(selected):
        if fail:
            with pytest.raises(OwnedRuntimeError, match="selected fixture route unavailable"):
                _call_tool_with_timeout("code_helper", {"action": "explain", "code": "print(42)"}, None, registry=parent)
        else:
            result = json.loads(_call_tool_with_timeout("code_helper", {"action": "explain", "code": "print(42)"}, None, registry=parent))
            assert result["ok"] is True
            assert result["result"] == "selected worker explanation"
    assert len(requests) == 1
    assert requests[0][1]["deadline"] > time.monotonic() - 1
    parent.execute.assert_not_called()
    no_sdk.assert_not_called()
