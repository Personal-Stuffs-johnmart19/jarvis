"""Keep the opt-in native failure fixture's strict planner guards in offline CI."""
from pathlib import Path


def test_owned_failure_plan_cannot_admit_other_paths_or_extra_inference(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tests/desktop"))
    from qa_queued_failure import pure_guards

    report = pure_guards()
    assert report == {"ok": True, "pure_cases": 6, "model_calls": 0, "executions": 0}
