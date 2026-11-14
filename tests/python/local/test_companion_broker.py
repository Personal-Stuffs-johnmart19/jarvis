"""Граница общего ключа: HTTP/SSE, tools, fallback и запрет чужих origins."""

from contextlib import nullcontext
import json
from unittest.mock import patch
import httpx
import pytest
from google.genai import types
from local_mode.companion_broker import GeminiBroker, parse_request
from local_mode.models import MODEL_ORDER


@pytest.fixture
def bridge():
    broker = GeminiBroker()
    try:
        with httpx.Client(base_url=broker.url, trust_env=False, timeout=3) as client:
            yield broker, client
    finally:
        broker.close()


def test_bridge_auth_and_model_allowlist(bridge):
    broker, client = bridge
    assert client.get("/_jarvis/info").status_code == 403
    headers = {"x-goog-api-key": broker.token}
    assert (
        client.get(
            "/_jarvis/info", headers={**headers, "Origin": "https://attacker.test"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/_jarvis/info", headers={**headers, "Host": "attacker.test"}
        ).status_code
        == 403
    )
    result = client.get("/v1beta/models", headers=headers)
    assert result.status_code == 200
    assert [m["name"] for m in result.json()["models"]] == [
        "models/" + m for m in MODEL_ORDER
    ]
    assert (
        client.post(
            "/v1beta/models/gemini-pro:generateContent", headers=headers, json={}
        ).status_code
        == 400
    )
    assert client.options("/v1beta/models", headers=headers).status_code == 403


def test_request_keeps_function_arguments_and_signatures():
    contents, config = parse_request(
        {
            "contents": [
                {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "read",
                                "args": {"filePath": "a.py"},
                            },
                            "thoughtSignature": "c2ln",
                        }
                    ],
                }
            ],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "read",
                            "parameters": {
                                "type": "OBJECT",
                                "properties": {"filePath": {"type": "STRING"}},
                            },
                        }
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": 32000},
        }
    )
    assert contents[0].parts[0].function_call.args == {"filePath": "a.py"}
    assert contents[0].parts[0].thought_signature == b"sig"
    assert config.tools[0].function_declarations[0].name == "read"
    assert config.max_output_tokens == 16384


@pytest.mark.parametrize(
    "extra",
    [
        {"cachedContent": "foreign/cache"},
        {"tools": [{"googleSearch": {}}]},
        {"tools": [{"urlContext": {}}]},
        {"tools": [{"codeExecution": {}}]},
    ],
)
def test_request_rejects_billable_server_tools(extra):
    with pytest.raises(ValueError):
        parse_request({"contents": [{"parts": [{"text": "test"}]}], **extra})


def test_bridge_stream_preserves_calls_and_does_not_repeat_text(bridge):
    broker, client = bridge
    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                index=0,
                finish_reason="STOP",
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Привет"),
                        types.Part(
                            function_call=types.FunctionCall(
                                name="read", args={"filePath": "a.py"}
                            ),
                            thought_signature=b"sig",
                        ),
                    ],
                ),
            )
        ]
    )

    def stream(*args, on_text, **kwargs):
        on_text("")
        on_text("При")
        on_text("Привет")
        return response

    with patch(
        "local_mode.companion_broker.gemini.create_client",
        return_value=nullcontext(object()),
    ), patch("local_mode.companion_broker.gemini.generate_stream", side_effect=stream):
        result = client.post(
            "/v1beta/models/" + MODEL_ORDER[-1] + ":streamGenerateContent",
            headers={"Authorization": "Bearer " + broker.token},
            json={"contents": [{"parts": [{"text": "test"}]}]},
        )
    assert result.status_code == 200
    values = [
        json.loads(line[6:])
        for line in result.text.splitlines()
        if line.startswith("data: ")
    ]
    assert (
        "".join(
            p.get("text", "")
            for v in values
            for c in v.get("candidates", [])
            for p in c["content"]["parts"]
        )
        == "Привет"
    )
    last = values[-1]["candidates"][0]["content"]["parts"][0]
    assert last["functionCall"]["args"] == {"filePath": "a.py"}
    assert last["thoughtSignature"] == "c2ln"
