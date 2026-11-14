"""Bounded cloud QA continuation accounting, with mocked SDK and no credentials."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from google.genai import types
from local_mode import api_usage, gemini
from local_mode.companion_broker import parse_request
from local_mode.routing import ModelRouter


@pytest.fixture
def qa_budget(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", "1")
    monkeypatch.setenv("JARVIS_QA_ALLOW_CLOUD", "1")
    monkeypatch.delenv("JARVIS_QA_MAX_CLOUD_CALLS", raising=False)
    monkeypatch.setattr(api_usage, "_qa_cloud_calls", 0)
    ledger = Mock()
    monkeypatch.setattr(api_usage, "_default", ledger)
    return ledger


def test_broker_tool_continuation_uses_second_sdk_attempt_and_third_is_denied(qa_budget, monkeypatch):
    monkeypatch.setenv("JARVIS_QA_MAX_CLOUD_CALLS", "2")
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="42")]))])
    client = Mock()
    client.models.generate_content.return_value = response
    router = ModelRouter("gemini-3.5-flash-lite", False)
    initial, config = parse_request({"contents": [{"role": "user", "parts": [{"text": "Run the approved fixture, then report stdout."}]}]})
    assert router.generate(client, contents=initial, config=config) is response
    # Approved tool result is fixed synthetic data; no code, file or tool runs here.
    continuation, config = parse_request({"contents": [
        {"role": "user", "parts": [{"text": "Run the approved fixture, then report stdout."}]},
        {"role": "model", "parts": [{"functionCall": {"id": "qa-call", "name": "jarvis_action", "args": {"name": "run_code", "args": {"language": "python", "code": "print(42)"}}}, "thoughtSignature": "c2ln"}]},
        {"role": "user", "parts": [{"functionResponse": {"id": "qa-call", "name": "jarvis_action", "response": {"output": {"stdout": "42", "exit_code": 0}}}}]},
    ]})
    assert router.generate(client, contents=continuation, config=config) is response
    with pytest.raises(RuntimeError, match="2 SDK attempt"):
        router.generate(client, contents=continuation, config=config)
    assert client.models.generate_content.call_count == 2
    assert qa_budget.add.call_count == 2
    assert api_usage._qa_cloud_calls == 2


def test_default_remains_one_attempt_even_for_a_failed_sdk_call(qa_budget):
    client = Mock()
    client.models.generate_content.side_effect = ValueError("synthetic failure")
    with pytest.raises(ValueError, match="synthetic failure"):
        gemini.generate(client, "gemini-3.5-flash-lite", "first")
    with pytest.raises(RuntimeError, match="1 SDK attempt"):
        gemini.generate(client, "gemini-3.5-flash-lite", "second")
    assert client.models.generate_content.call_count == 1
    assert qa_budget.add.call_count == 1


@pytest.mark.parametrize("value", ["0", "9", "-1", "1.5", "", "two", "2 "])
def test_invalid_explicit_budget_rejects_before_sdk_and_ledger(qa_budget, monkeypatch, value):
    monkeypatch.setenv("JARVIS_QA_MAX_CLOUD_CALLS", value)
    client = Mock()
    with pytest.raises(ValueError, match="integer from 1 to 8"):
        gemini.generate(client, "gemini-3.5-flash-lite", "request")
    client.models.generate_content.assert_not_called()
    qa_budget.add.assert_not_called()
    assert api_usage._qa_cloud_calls == 0


def test_budget_admission_is_thread_safe(qa_budget, monkeypatch):
    monkeypatch.setenv("JARVIS_QA_MAX_CLOUD_CALLS", "3")
    def attempt(_):
        try:
            with api_usage.RequestUsage("gemini-3.5-flash-lite", "generate"):
                return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(attempt, range(20)))
    assert sum(admitted) == 3
    assert api_usage._qa_cloud_calls == 3
    assert qa_budget.add.call_count == 3


@pytest.mark.parametrize("desktop,cloud", [("0", "1"), ("1", "0")])
def test_budget_override_is_only_evaluated_in_explicit_cloud_qa(qa_budget, monkeypatch, desktop, cloud):
    monkeypatch.setenv("JARVIS_DESKTOP_TEST", desktop)
    monkeypatch.setenv("JARVIS_QA_ALLOW_CLOUD", cloud)
    monkeypatch.setenv("JARVIS_QA_MAX_CLOUD_CALLS", "invalid")
    with api_usage.RequestUsage("gemini-3.5-flash-lite", "generate"):
        pass
    assert api_usage._qa_cloud_calls == 0
