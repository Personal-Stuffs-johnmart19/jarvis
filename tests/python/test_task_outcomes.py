"""Incomplete queued goals must never be reported as completed; no model/API calls."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import executor as execution
from agent.task_approval import ToolCancelled
from agent.task_queue import TaskQueue, TaskStatus


@pytest.fixture
def queued(monkeypatch):
    calls = []
    steps = [{"step": 1, "tool": "read", "parameters": {}}]
    monkeypatch.setattr(execution, "create_plan", lambda *_, **__: {"steps": steps})
    original_summary = execution.AgentExecutor._summarize
    summary = Mock(return_value="Successful summary")
    monkeypatch.setattr(execution.AgentExecutor, "_summarize", summary)
    from local_mode import model
    provider_calls = Mock(side_effect=AssertionError("Unexpected model recovery"))
    monkeypatch.setattr(model, "ContentModel", provider_calls)

    def dispatch(name, args, speak, cancel_flag=None, **kwargs):
        calls.append(name)
        if name == "missing":
            raise execution.UnknownToolError("Fixture tool is absent")
        if name == "broken":
            raise RuntimeError("Fixture step failed")
        if name == "write_then_stop":
            cancel_flag.set()  # Represents a physical effect preceding cancellation feedback.
            raise ToolCancelled("Stopped after physical effect")
        return "saved " + name

    monkeypatch.setattr(execution, "_call_tool_with_timeout", dispatch)
    queue = TaskQueue()
    task_id = queue.submit("fixture", registry=SimpleNamespace(risky=lambda *_: False))
    task = queue._tasks[task_id]

    def run():
        queue._queue.remove(task)
        task.status = TaskStatus.RUNNING
        queue._active_count += 1
        task.execution_context.copy().run(queue._run_task, task)
        return queue.get_status(task_id)

    return SimpleNamespace(steps=steps, calls=calls, summary=summary, provider_calls=provider_calls,
                           task=task, queue=queue, run=run,
                           original_summary=original_summary)


def test_empty_plan_is_failed_not_completed(queued):
    queued.steps.clear()
    result = queued.run()
    assert result["status"] == "failed"
    assert "no valid plan" in result["error"]
    assert queued.calls == []
    queued.summary.assert_not_called()


@pytest.mark.parametrize("critical", [None, True])
def test_unknown_required_tool_fails_without_model_recovery_and_keeps_prior_results(queued, critical):
    step = {"step": 2, "tool": "missing", "parameters": {}}
    if critical is not None:
        step["critical"] = critical
    queued.steps.append(step)
    result = queued.run()
    assert result["status"] == "failed"
    assert "unavailable tool missing" in result["error"]
    assert len(result["completed_steps"]) == 1
    assert result["result"]["step_results"] == {1: "saved read"}
    assert queued.calls == ["read", "missing"]
    queued.provider_calls.assert_not_called()
    queued.summary.assert_not_called()


def test_explicit_optional_unknown_is_skipped_without_fake_completed_step(queued):
    queued.steps.append({"step": 2, "tool": "missing", "parameters": {}, "critical": False})
    result = queued.run()
    assert result["status"] == "completed"
    assert "1 optional steps were skipped" in result["result"]
    assert len(result["completed_steps"]) == 1
    assert len(result["skipped_steps"]) == 1
    queued.provider_calls.assert_not_called()
    queued.summary.assert_not_called()


def test_skipping_every_optional_step_is_incomplete_when_no_action_completed(queued):
    queued.steps[:] = [{"step":1,"tool":"missing","parameters":{},"critical":False}]
    result = queued.run()
    assert result["status"] == "failed"
    assert "No queued steps completed" in result["error"]
    assert result["completed_steps"] == []
    assert len(result["skipped_steps"]) == 1
    queued.summary.assert_not_called()


@pytest.mark.parametrize("critical", [True, False])
def test_runtime_failure_never_retries_skips_or_spends_model_calls(queued, critical):
    queued.steps.extend([{"step": 2, "tool": "broken", "parameters": {}, "critical": critical},
                         {"step": 3, "tool": "must_not_run", "parameters": {}}])
    result = queued.run()
    assert result["status"] == "failed" and "may have taken effect" in result["error"]
    assert result["result"]["step_results"] == {1: "saved read"}
    assert result["result"]["failed_step"]["tool"] == "broken"
    assert result["skipped_steps"] == [] and queued.calls == ["read", "broken"]
    queued.provider_calls.assert_not_called()
    queued.summary.assert_not_called()


def test_write_then_exception_occurs_once_and_failed_checkpoint_cannot_reenter(queued, monkeypatch, tmp_path):
    from agent.task_approval import TaskExecutionFailed
    target = tmp_path / "effect.txt"
    dispatch = execution._call_tool_with_timeout

    def write_then_fail(name, *args, **kwargs):
        if name == "write_then_fail":
            with target.open("a", encoding="utf-8") as output:
                output.write("one effect\n")
            raise RuntimeError("private failure after write")
        return dispatch(name, *args, **kwargs)

    monkeypatch.setattr(execution, "_call_tool_with_timeout", write_then_fail)
    queued.steps.append({"step": 2, "tool": "write_then_fail", "parameters": {"path": str(target)}})
    result = queued.run()
    assert result["status"] == "failed" and "private failure" not in result["error"]
    assert target.read_text(encoding="utf-8") == "one effect\n"
    with pytest.raises(TaskExecutionFailed):
        execution.AgentExecutor().execute("fixture", checkpoint=queued.task.checkpoint)
    assert target.read_text(encoding="utf-8") == "one effect\n"
    assert result["result"]["step_results"] == {1: "saved read"}
    queued.provider_calls.assert_not_called()
    queued.summary.assert_not_called()


def test_timeout_remains_distinct_and_has_no_recovery_or_success_summary(queued, monkeypatch):
    from agent.task_approval import ToolTimedOut
    dispatch = execution._call_tool_with_timeout

    def timed_out(name, *args, **kwargs):
        if name == "timeout":
            raise ToolTimedOut("private timeout")
        return dispatch(name, *args, **kwargs)

    monkeypatch.setattr(execution, "_call_tool_with_timeout", timed_out)
    queued.steps.append({"step": 2, "tool": "timeout", "parameters": {}})
    result = queued.run()
    assert result["status"] == "failed" and "timed out" in result["error"]
    assert result["result"]["failure_stage"] == "timeout"
    assert result["result"]["step_results"] == {1: "saved read"}
    queued.provider_calls.assert_not_called()
    queued.summary.assert_not_called()

def test_stop_after_physical_effect_keeps_partial_work_and_reports_unknown_outcome(queued):
    queued.steps.extend([{"step":2,"tool":"write_then_stop","parameters":{}},
                         {"step":3,"tool":"must_not_run","parameters":{}}])
    result = queued.run()
    assert result["status"] == "cancelled"
    assert "may have taken effect" in result["error"]
    assert result["result"]["step_results"] == {1:"saved read"}
    assert queued.calls == ["read","write_then_stop"]
    assert queued.queue._next_task() is None
    queued.summary.assert_not_called()
    queued.provider_calls.assert_not_called()


def test_cancelled_summary_does_not_speak_success_fallback(queued, monkeypatch):
    from local_mode import model
    # Use the real summary implementation with a synthetic stopped provider.

    def stopped(*_args, **_kwargs):
        queued.task.cancel_flag.set()
        raise ToolCancelled("Stopped during summary")

    monkeypatch.setattr(execution.AgentExecutor, "_summarize", queued.original_summary)
    monkeypatch.setattr(model, "ContentModel", lambda **_kwargs: SimpleNamespace(generate_content=stopped))
    queued.task.speak = Mock()
    result = queued.run()
    assert result["status"] == "cancelled"
    assert result["result"]["step_results"] == {1:"saved read"}
    queued.task.speak.assert_not_called()
