"""Учёт только метаданных: без промптов/ключей и выдуманной квоты."""

from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from google.genai.errors import APIError
from local_mode import api_usage
from local_mode.api_usage import UsageLedger, token_usage, RequestUsage, track_call


def test_both_sdk_usage_formats_are_counted_exactly():
    assert token_usage(
        NS(
            usage_metadata=NS(
                prompt_token_count=12, candidates_token_count=5, total_token_count=20
            )
        )
    ) == {"input_tokens": 12, "output_tokens": 5, "total_tokens": 20}
    assert token_usage(
        NS(usage=NS(total_input_tokens=2, total_output_tokens=7, total_tokens=9))
    ) == {"input_tokens": 2, "output_tokens": 7, "total_tokens": 9}
    assert token_usage(Mock()) == {}


def test_attempts_errors_and_restart_are_local_and_secret_free(tmp_path):
    path = tmp_path / "usage.json"
    ledger = UsageLedger(path)
    ledger.add(
        "gemini-3.8-flash", "stream", NS(usage_metadata=NS(total_token_count=12))
    )
    ledger.add(
        "gemini-3.8-flash",
        "stream",
        error=APIError(429, {"error": {"message": "secret prompt"}}),
    )
    snapshot = UsageLedger(path).snapshot()
    row = snapshot["rows"][0]
    assert row["attempts"] == 2 and row["successes"] == 1 and row["rate_limited"] == 1
    assert row["total_tokens"] == 12 and snapshot["remaining_quota"] is None
    assert "secret" not in path.read_text("utf-8")


def test_missing_metadata_is_not_zero_usage(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.json")
    ledger.add("gemini-embedding-2", "embed")
    row = ledger.snapshot()["rows"][0]
    assert "total_tokens" not in row and "reported_usage" not in row


def test_concurrent_writes_and_corrupt_file(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("broken")
    ledger = UsageLedger(path)
    assert ledger.persistence_error
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: ledger.add("test", "tts"), range(20)))
    assert UsageLedger(path).snapshot()["rows"][0]["attempts"] == 20


def test_decorator_records_each_attempt_without_changing_response(
    tmp_path, monkeypatch
):
    ledger = UsageLedger(tmp_path / "usage.json")
    monkeypatch.setattr(api_usage, "_default", ledger)
    response = NS(usage_metadata=NS(total_token_count=4))

    @track_call("generate")
    def request(client, model):
        return response

    assert request(None, "model") is response
    assert ledger.snapshot()["rows"][0]["total_tokens"] == 4


def test_persistence_failure_never_masks_api_result(tmp_path, monkeypatch):
    ledger = UsageLedger(tmp_path / "usage.json")
    monkeypatch.setattr(api_usage, "_default", ledger)
    with patch("local_mode.api_usage.write_json", side_effect=OSError("disk full")):
        with RequestUsage("model", "tts"):
            pass
    assert ledger.persistence_error and ledger.snapshot()["rows"][0]["successes"] == 1


def test_non_object_json_cannot_break_gemini_startup(tmp_path):
    path = tmp_path / "usage.json"
    path.write_text("[]")
    ledger = UsageLedger(path)
    assert ledger.persistence_error and ledger.snapshot()["rows"] == []
