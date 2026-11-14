"""Own loopback broker and mocked SDK only: no credentials or cloud requests."""
import http.client
import json
import threading
from contextlib import contextmanager
from unittest.mock import Mock

import httpx
import pytest
from google.genai import types
from local_mode import companion_broker as broker_module, gemini


@contextmanager
def broker_fixture(monkeypatch, generate):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(gemini, "create_client", Mock(return_value=client))
    monkeypatch.setattr(gemini, "generate_stream", generate)
    monkeypatch.setattr(broker_module, "FIRST_RESPONSE_SECONDS", 0.15)
    monkeypatch.setattr(broker_module, "HEARTBEAT_SECONDS", 0.02)
    broker = broker_module.GeminiBroker()
    connection = http.client.HTTPConnection("127.0.0.1", broker._server.server_port, timeout=2)
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": "fixture"}]}]})
    connection.request("POST", "/v1beta/models/gemini-3.8-flash:streamGenerateContent", body,
                       {"Authorization": "Bearer " + broker.token, "Content-Type": "application/json"})
    try:
        yield broker, connection, client
    finally:
        connection.close()
        broker.close()


def final_response():
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="weather_report", args={"city": "Vorzel"}), thought_signature=b"signature")]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=7, candidates_token_count=2))


def test_headers_arrive_before_sdk_completion_and_preserve_tool_signature_usage(monkeypatch):
    release = threading.Event()
    calls = []
    def generate(client, model, contents, config, **kwargs):
        calls.append((model, kwargs))
        assert release.wait(2)
        return final_response()
    with broker_fixture(monkeypatch, generate) as (_, connection, _):
        response = connection.getresponse()
        assert response.status == 200
        assert not release.is_set()
        release.set()
        payload = response.read().decode()
    assert '"functionCall"' in payload
    assert '"thoughtSignature": "c2lnbmF0dXJl"' in payload
    assert '"promptTokenCount": 7' in payload
    assert len(calls) == 1
    assert calls[0][0] == "gemini-3.8-flash"
    assert calls[0][1]["single_attempt"] is True


def test_first_meaningful_deadline_signals_stop_and_holds_slot_until_worker_exits(monkeypatch):
    stopped = threading.Event()
    release = threading.Event()
    def generate(client, model, contents, config, **kwargs):
        assert kwargs["stop"].wait(2)
        stopped.set()
        assert release.wait(2)
        return final_response()
    with broker_fixture(monkeypatch, generate) as (broker, connection, client):
        response = connection.getresponse()
        payload = response.read().decode()
        assert ": keepalive" in payload
        assert '"error"' in payload
        assert stopped.wait(1)
        client.close.assert_called()
        assert gemini.create_client.call_args.kwargs["stop"].is_set()
        # Capacity two, one cancelled worker still owns its physical slot.
        assert broker.slots.acquire(blocking=False)
        assert not broker.slots.acquire(blocking=False)
        broker.slots.release()
        release.set()


def test_stream_disconnect_signals_stop_without_another_generation(monkeypatch):
    stopped = threading.Event()
    calls = []
    def generate(client, model, contents, config, **kwargs):
        calls.append(model)
        assert kwargs["stop"].wait(2)
        stopped.set()
        return final_response()
    with broker_fixture(monkeypatch, generate) as (_, connection, _):
        response = connection.getresponse()
        # Force TCP close rather than leaving a response file owning the socket.
        response.fp.raw._sock.shutdown(2)
        response.close()
        connection.close()
        assert stopped.wait(1)
    assert calls == ["gemini-3.8-flash"]


def test_upstream_error_is_structured_sse_and_not_retried(monkeypatch):
    generate = Mock(side_effect=ValueError("fixture invalid request"))
    with broker_fixture(monkeypatch, generate) as (_, connection, _):
        response = connection.getresponse()
        payload = response.read().decode()
    assert '"code": 400' in payload
    assert "fixture invalid request" in payload
    assert generate.call_count == 1


def test_single_attempt_disables_tenacity_without_changing_legacy_retry_policy(monkeypatch):
    client = Mock()
    client.models.generate_content.side_effect = httpx.ReadTimeout("fixture")
    with pytest.raises(httpx.ReadTimeout):
        gemini.generate(client, "gemini-3.8-flash", "fixture", single_attempt=True)
    assert client.models.generate_content.call_count == 1
