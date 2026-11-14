import pytest
from unittest.mock import Mock
from local_mode import gemini,api_usage

def test_qa_cannot_create_a_cloud_client_by_default(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_TEST","1")
    monkeypatch.delenv("JARVIS_QA_ALLOW_CLOUD",raising=False)
    client=Mock();monkeypatch.setattr(gemini.genai,"Client",client)
    with pytest.raises(RuntimeError,match="Cloud API is disabled"):
        gemini.create_client()
    client.assert_not_called()

def test_explicit_cloud_smoke_has_one_attempt_no_retry_or_fallback(monkeypatch):
    monkeypatch.setenv("JARVIS_DESKTOP_TEST","1")
    monkeypatch.setenv("JARVIS_QA_ALLOW_CLOUD","1")
    monkeypatch.setattr(api_usage,"_qa_cloud_calls",0)
    monkeypatch.setattr(api_usage,"_default",Mock())
    with api_usage.RequestUsage("fixture","stream"):pass
    with pytest.raises(RuntimeError,match="budget exhausted"):
        with api_usage.RequestUsage("fallback","generate"):pass
