"""Google-compatible broker failures, with loopback HTTP and mocked SDK only."""
import http.client
import json
from unittest.mock import Mock

import pytest

from local_mode import companion_broker as broker_module, gemini


@pytest.mark.parametrize("code,status", [(400, "INVALID_ARGUMENT"), (401, "UNAUTHENTICATED"),
    (403, "PERMISSION_DENIED"), (404, "NOT_FOUND"), (429, "RESOURCE_EXHAUSTED"),
    (500, "INTERNAL"), (502, "UNAVAILABLE"), (503, "UNAVAILABLE"), (504, "DEADLINE_EXCEEDED")])
def test_error_payload_has_required_google_status_string(code, status):
    assert broker_module.error_payload(code, "fixture") == {
        "error": {"code": code, "message": "fixture", "status": status}}


@pytest.mark.parametrize("operation", ["generateContent", "streamGenerateContent"])
def test_actual_timeout_response_keeps_deadline_message_and_google_status(monkeypatch, operation):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(gemini, "create_client", Mock(return_value=client))
    monkeypatch.setattr(gemini, "generate", Mock(side_effect=TimeoutError("fixture first-response deadline")))
    monkeypatch.setattr(gemini, "generate_stream", Mock(side_effect=TimeoutError("fixture first-response deadline")))
    broker = broker_module.GeminiBroker()
    connection = http.client.HTTPConnection("127.0.0.1", broker._server.server_port, timeout=2)
    try:
        connection.request("POST", "/v1beta/models/gemini-3.8-flash:" + operation,
            json.dumps({"contents": [{"role": "user", "parts": [{"text": "fixture"}]}]}),
            {"Authorization": "Bearer " + broker.token, "Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read().decode()
        if operation == "streamGenerateContent":
            assert response.status == 200
            payload = json.loads(next(line[6:] for line in raw.splitlines() if line.startswith("data: ")))
        else:
            assert response.status == 504
            payload = json.loads(raw)
        assert payload["error"] == {"code": 504, "status": "DEADLINE_EXCEEDED",
                                    "message": "fixture first-response deadline"}
    finally:
        connection.close()
        broker.close()


@pytest.mark.parametrize("method,code,status", [("GET", 403, "PERMISSION_DENIED"), ("PUT", 501, "UNIMPLEMENTED")])
def test_ingress_errors_also_have_google_status(method, code, status):
    broker = broker_module.GeminiBroker()
    connection = http.client.HTTPConnection("127.0.0.1", broker._server.server_port, timeout=2)
    try:
        connection.request(method, "/v1beta/models")
        response = connection.getresponse()
        assert response.status == code
        assert json.loads(response.read())["error"]["status"] == status
    finally:
        connection.close()
        broker.close()
