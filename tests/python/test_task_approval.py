"""Queued approval suspends one step without replaying completed work or changing priority."""
import json
import threading
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from agent import executor as execution
from agent import task_queue
from agent.task_approval import QueueApprovalRequired, ToolCancelled
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent


def setup_queue(monkeypatch):
    queue = task_queue.TaskQueue()
    monkeypatch.setattr(task_queue, "get_queue", lambda: queue)
    shared = SharedAgent(lambda *_: None)
    shared.attach("fixture", "chat", "owner")
    events = []
    runtime = SimpleNamespace(shared_agent=shared, event=lambda *event: events.append(event))
    core = SimpleNamespace(validate=lambda *_: None, risky=lambda name, args: name == "write_step")
    registry = ServiceRegistry(runtime, lambda: core)
    steps = [{"step": 1, "tool": "read_step", "parameters": {}},
             {"step": 2, "tool": "write_step", "parameters": {"text": "exact approved text"}},
             {"step": 3, "tool": "final_step", "parameters": {}}]
    monkeypatch.setattr(execution, "create_plan", lambda goal, **_: {"steps": steps})
    monkeypatch.setattr(execution.AgentExecutor, "_summarize", lambda *_: "finished")
    calls = []

    def dispatch(name, args, speak, cancel_flag=None, *, registry=None, approved=False):
        if registry.risky(name, args) and not approved:
            raise QueueApprovalRequired(name, args)
        calls.append((name, args, approved))
        return json.dumps({"ok": True, "result": name})

    monkeypatch.setattr(execution, "_call_tool_with_timeout", dispatch)
    return queue, registry, shared, calls, events


def run_next(queue):
    with queue._lock:
        task = queue._next_task()
        assert task is not None
        queue._queue.remove(task)
        task.status = task_queue.TaskStatus.RUNNING
        queue._active_count += 1
    task.execution_context.copy().run(queue._run_task, task)


def test_shared_receipt_resumes_same_task_once_with_completed_work_and_priority(monkeypatch):
    queue, registry, shared, calls, events = setup_queue(monkeypatch)
    started = registry.execute("agent_task", {"goal": "fixture", "priority": "high"}, approved=True)
    task_id = started["task_id"]
    run_next(queue)
    paused = queue.get_status(task_id)
    assert paused["status"] == "awaiting_approval"
    assert paused["priority"] == 1
    assert len(paused["completed_steps"]) == 1
    assert [call[0] for call in calls] == ["read_step"]
    assert events[-1][0] == "tasks.approval"
    assert events[-1][1]["request"] == {"name": "tasks.resume", "args": paused["pending_action"]}
    request = paused["pending_action"]
    queue.submit("lower priority", priority=task_queue.TaskPriority.LOW, registry=registry)
    changed = {**request, "args": {"text": "changed"}}
    with pytest.raises(ValueError, match="Expired or changed"):
        shared.prepare(registry, "tasks.resume", changed)
    receipt = shared.prepare(registry, "tasks.resume", request)
    assert receipt["requires_approval"] is True
    assert shared.execute(registry, receipt["token"])["task_id"] == task_id
    assert queue._tasks[task_id].priority == 1
    assert queue._next_task().task_id == task_id
    assert len(calls) == 1  # Approval enqueues; it never bypasses queue priority.
    with pytest.raises(ValueError, match="consumed"):
        shared.execute(registry, receipt["token"])
    run_next(queue)
    assert queue.get_status(task_id)["status"] == "completed"
    assert [call[0] for call in calls] == ["read_step", "write_step", "final_step"]
    assert calls[1][2] is True
    assert len(queue.get_status(task_id)["completed_steps"]) == 3


def test_pending_task_cancel_and_chat_change_revoke_approval(monkeypatch):
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    request = queue.get_status(task_id)["pending_action"]
    shared.attach("fixture", "other-chat", "owner")
    with pytest.raises(ValueError, match="another chat"):
        shared.prepare(registry, "tasks.resume", request)
    shared.attach("fixture", "chat", "owner")
    receipt = shared.prepare(registry, "tasks.resume", request)
    assert queue.cancel(task_id)
    with pytest.raises(ValueError, match="Expired or changed"):
        shared.execute(registry, receipt["token"])
    assert queue.get_status(task_id)["status"] == "cancelled"
    assert [call[0] for call in calls] == ["read_step"]


def test_queued_provider_context_is_preserved_across_approval(monkeypatch):
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    provider = ContextVar("queued_test_provider", default="unbound")
    provider.set("original")
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    provider.set("changed")
    seen = []
    dispatch = execution._call_tool_with_timeout

    def contextual(*args, **kwargs):
        seen.append(provider.get())
        return dispatch(*args, **kwargs)

    monkeypatch.setattr(execution, "_call_tool_with_timeout", contextual)
    run_next(queue)
    receipt = shared.prepare(registry, "tasks.resume", queue.get_status(task_id)["pending_action"])
    shared.execute(registry, receipt["token"])
    run_next(queue)
    assert seen and set(seen) == {"original"}


def test_new_risk_after_safe_resume_requires_fresh_approval_without_execution(monkeypatch):
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    request = queue.get_status(task_id)["pending_action"]
    core = registry.core()
    core.risky = lambda *_: False
    receipt = shared.prepare(registry, "tasks.resume", request)
    assert receipt["requires_approval"] is False
    shared.execute(registry, receipt["token"])
    core.risky = lambda *_: True
    run_next(queue)
    pending = queue.get_status(task_id)
    assert pending["status"] == "awaiting_approval"
    assert pending["pending_action"]["approval_id"] != request["approval_id"]
    assert [call[0] for call in calls] == ["read_step"]


def test_cancel_after_approval_revokes_queued_grant_before_dispatch(monkeypatch):
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    receipt = shared.prepare(registry, "tasks.resume", queue.get_status(task_id)["pending_action"])
    shared.execute(registry, receipt["token"])
    assert queue.cancel(task_id)
    assert queue._next_task() is None
    assert queue._tasks[task_id].approved_action is None
    assert [call[0] for call in calls] == ["read_step"]


@pytest.mark.parametrize("change", ["detach", "session"])
def test_queued_grant_is_revoked_if_its_chat_leaves_before_dispatch(monkeypatch, change):
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    receipt = shared.prepare(registry, "tasks.resume", queue.get_status(task_id)["pending_action"])
    shared.execute(registry, receipt["token"])
    if change == "detach":
        shared.detach()
    else:
        shared.attach("fixture", "other-chat", "owner")
    run_next(queue)
    assert queue.get_status(task_id)["status"] == "cancelled"
    assert [call[0] for call in calls] == ["read_step"]


def test_cancelled_executor_never_plans(monkeypatch):
    stop = threading.Event()
    stop.set()
    monkeypatch.setattr(execution, "create_plan", lambda *_: pytest.fail("Cancelled task planned"))
    with pytest.raises(ToolCancelled):
        execution.AgentExecutor().execute("fixture", cancel_flag=stop)


def test_failed_action_never_manufactures_an_alternative_approval(monkeypatch):
    from local_mode import model
    from unittest.mock import Mock
    queue, registry, shared, calls, events = setup_queue(monkeypatch)
    provider = Mock(side_effect=AssertionError("Unexpected model recovery"))
    monkeypatch.setattr(model, "ContentModel", provider)
    monkeypatch.setattr(execution, "create_plan", lambda *_, **__: {
        "steps": [{"step": 1, "tool": "broken_step", "parameters": {}}]})

    def failed(*_args, **_kwargs):
        raise RuntimeError("Fixture failure")

    monkeypatch.setattr(execution, "_call_tool_with_timeout", failed)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    result = queue.get_status(task_id)
    assert result["status"] == "failed" and result["pending_action"] is None
    assert result["result"]["failed_step"]["tool"] == "broken_step"
    assert calls == [] and shared.receipts == {}
    assert not any(name == "tasks.approval" for name, _payload in events)
    provider.assert_not_called()


@pytest.mark.parametrize("timed_out", [False, True])
def test_approved_failure_preserves_result_and_effect_without_retry(monkeypatch, tmp_path, timed_out):
    from unittest.mock import Mock
    from local_mode import model
    from local_mode.messages import OwnedText
    from agent.task_approval import ToolReportedFailure

    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    target = tmp_path / "effect.txt"
    outcome = {"ok": False, "exit_code": None if timed_out else 1,
               "stdout": "effect written", "stderr": "owned Привіт", "timed_out": timed_out}
    provider = Mock(side_effect=AssertionError("Unexpected model recovery"))
    summary = Mock(side_effect=AssertionError("Failed task generated a success summary"))
    monkeypatch.setattr(model, "ContentModel", provider)
    monkeypatch.setattr(execution.AgentExecutor, "_summarize", summary)
    dispatch = execution._call_tool_with_timeout

    def approved_failure(name, args, *positional, **kwargs):
        if name == "write_step" and kwargs.get("approved"):
            calls.append((name, args, True))
            with target.open("a", encoding="utf-8") as stream:
                stream.write("once\n")
            raise ToolReportedFailure(OwnedText("Queued task action failed"), outcome)
        return dispatch(name, args, *positional, **kwargs)

    monkeypatch.setattr(execution, "_call_tool_with_timeout", approved_failure)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    request = queue.get_status(task_id)["pending_action"]
    receipt = shared.prepare(registry, "tasks.resume", request)
    shared.execute(registry, receipt["token"])
    run_next(queue)
    failed = queue.get_status(task_id)
    assert failed["status"] == "failed" and failed["pending_action"] is None
    assert failed["result"]["failed_step_result"] == outcome
    assert failed["result"]["failure_stage"] == ("timeout" if timed_out else "execution")
    assert len(failed["result"]["step_results"]) == 1
    assert failed["result"]["failed_step"]["parameters"] == request["args"]
    assert [item[0] for item in calls] == ["read_step", "write_step"]
    assert target.read_text(encoding="utf-8") == "once\n" and queue._next_task() is None
    with pytest.raises(ValueError):
        shared.prepare(registry, "tasks.resume", request)
    provider.assert_not_called()
    summary.assert_not_called()

def test_planning_helper_receives_queued_cancellation_flag_without_api(monkeypatch):
    from local_mode.model import relay_text_request, using_text_provider
    queue, registry, _, calls, _ = setup_queue(monkeypatch)
    seen = []

    def provider(request, *, cancel_flag=None, deadline=None):
        seen.append(cancel_flag)
        cancel_flag.set()
        raise ToolCancelled("Planning stopped")

    def plan(_goal, **_):
        relay_text_request({"prompt": "plan fixture", "system": "fixture", "max_output_tokens": 20, "json_mode": True})
        pytest.fail("Stopped planning continued")

    monkeypatch.setattr(execution, "create_plan", plan)
    with using_text_provider(provider):
        task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    assert seen == [queue._tasks[task_id].cancel_flag]
    assert queue.get_status(task_id)["status"] == "cancelled"
    assert calls == []


def test_owned_pending_backfill_and_exact_denial_only_cancel_matching_task(monkeypatch):
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal": "fixture"}, approved=True)["task_id"]
    run_next(queue)
    pending = registry.execute("tasks.pending", {"owner":"owner", "session":"chat"})
    assert pending["ready"] and pending["items"][0]["task_id"] == task_id
    assert registry.execute("tasks.pending", {"owner":"other", "session":"chat"})["items"] == []
    nonce = pending["items"][0]["request"]["args"]["approval_id"]
    with pytest.raises(ValueError, match="Expired or changed"):
        registry.execute("tasks.deny", {"task_id":task_id, "approval_id":"wrong"})
    receipt = shared.prepare(registry, "tasks.deny", {"task_id":task_id, "approval_id":nonce})
    assert receipt["requires_approval"] is False
    assert shared.execute(registry, receipt["token"])["status"] == "cancelled"
    assert queue.get_status(task_id)["pending_action"] is None
    assert [call[0] for call in calls] == ["read_step"]


def test_global_stop_cancels_existing_pending_and_queued_tasks_without_starting(monkeypatch):
    queue, registry, _, calls, _ = setup_queue(monkeypatch)
    monkeypatch.setattr(task_queue, "_queue", queue)
    monkeypatch.setattr(task_queue, "_queue_started", False)
    assert task_queue.cancel_existing() == 0
    task_id = registry.execute("agent_task", {"goal":"fixture"}, approved=True)["task_id"]
    run_next(queue)
    other = registry.execute("agent_task", {"goal":"other"}, approved=True)["task_id"]
    monkeypatch.setattr(task_queue, "_queue_started", True)
    assert task_queue.cancel_existing() == 2
    assert task_queue.cancel_existing(stop=True) == 0
    for item in [task_id,other]:
        assert queue.get_status(item)["status"] == "cancelled"
        assert queue._tasks[item].cancel_flag.is_set()
        assert queue._tasks[item].approved_action is None
        assert queue._tasks[item].pending_action is None
    assert calls[0][0] == "read_step"


def test_pending_approval_failures_keep_owned_error_payloads(monkeypatch):
    from local_mode.messages import OwnedValueError
    queue, registry, shared, _, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal":"fixture"}, approved=True)["task_id"]
    run_next(queue)
    request = queue.get_status(task_id)["pending_action"]
    with pytest.raises(OwnedValueError) as stale:
        registry.execute("tasks.deny", {"task_id":task_id,"approval_id":"stale"})
    assert stale.value.payload() == {"source":"jarvis","message":"Expired or changed queued task approval","values":[]}
    shared.attach("fixture","other-chat","owner")
    with pytest.raises(OwnedValueError) as foreign:
        shared.prepare(registry,"tasks.resume",request)
    assert foreign.value.payload()["message"] == "Queued approval belongs to another chat session"
    assert foreign.value.payload()["source"] == "jarvis"


def test_readonly_task_status_preserves_results_and_rejects_other_chat_origins(monkeypatch):
    from local_mode.messages import OwnedValueError
    queue, registry, shared, calls, _ = setup_queue(monkeypatch)
    task_id = registry.execute("agent_task", {"goal":"fixture"}, approved=True)["task_id"]
    run_next(queue)
    args = {"task_id":task_id}
    assert registry.risky("tasks.status", args) is False
    paused = registry.execute("tasks.status", args)
    assert paused["task_id"] == task_id and paused["status"] == "awaiting_approval"
    assert paused["step_results"][1] == json.dumps({"ok":True,"result":"read_step"})
    receipt = shared.prepare(registry,"tasks.resume",paused["pending_action"])
    shared.execute(registry,receipt["token"])
    run_next(queue)
    complete = registry.execute("tasks.status",args)
    assert complete["task_id"] == task_id and complete["status"] == "completed"
    assert len(complete["step_results"]) == 3
    shared.attach("fixture","other-chat","owner")
    with pytest.raises(OwnedValueError, match="another chat"):
        registry.execute("tasks.status",args)
    assert [call[0] for call in calls] == ["read_step","write_step","final_step"]
