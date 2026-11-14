"""Явная реальная проверка общего Gemini-моста, без UI/микрофона/браузера."""

import json
import httpx
from local_mode.companion_broker import GeminiBroker
from local_mode.config import Settings

broker = GeminiBroker()
try:
    with httpx.Client(trust_env=False, timeout=120) as client:
        response = client.post(
            broker.url
            + "/v1beta/models/"
            + Settings.from_env().model
            + ":streamGenerateContent",
            headers={"x-goog-api-key": broker.token},
            json={
                "contents": [
                    {"role": "user", "parts": [{"text": "Ответь одним словом: Готово"}]}
                ],
                "generationConfig": {"maxOutputTokens": 128},
            },
        )
        response.raise_for_status()
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events and not any(
            "error" in event for event in events
        ), "Broker SSE returned an error"
        text = "".join(
            part.get("text", "")
            for event in events
            for candidate in event.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
        )
        assert "готово" in text.lower(), "Unexpected empty/nonmatching model response"
        print(
            json.dumps(
                {"ok": True, "streaming": True, "text": text, "events": len(events)},
                ensure_ascii=False,
            )
        )
finally:
    broker.close()
