"""Actual queue/executor checkpoint handoff; no model, UI, filesystem or handler call."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent import executor as execution
from agent.task_queue import TaskQueue, TaskStatus, TaskPriority
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent


@pytest.fixture
def handoff(monkeypatch):
    shared = SharedAgent(lambda *_: None)
    shared.attach("fixture", "session", "owner", "workspace")
    runtime = SimpleNamespace(shared_agent=shared, text_provider=SimpleNamespace(identity=("owner", "session", "route")))
    registry = ServiceRegistry(runtime, lambda: None)
    registry.register_workspace([{"name": "read_file", "description": "Read", "approval": "none",
        "queue_read_only": True, "queue_path_fields": ["path"],
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}])
    planned = []
    def plan(*args, **kwargs):
        planned.append(args)
        return {"steps": [{"step": 1, "tool": "read_file", "parameters": {"path": "D:/owned/42.txt"}}]}
    monkeypatch.setattr(execution, "create_plan", plan)
    monkeypatch.setattr(execution.AgentExecutor, "_summarize", lambda *_: "done")
    queue, events = TaskQueue(), []
    task_id = queue.submit("read owned file", priority=TaskPriority.HIGH, registry=registry,
                           approval_owner=("owner", "session"), on_pending=events.append)
    task = queue._tasks[task_id]
    def run():
        queue._queue.remove(task)
        task.status = TaskStatus.RUNNING
        queue._active_count += 1
        queue._run_task_body(task)
    run()
    return SimpleNamespace(queue=queue, task=task, registry=registry, runtime=runtime, events=events,
                           request=deepcopy(events[0]["ui_request"]), run=run, planned=planned)


def completion(fixture, token, **extra):
    return dict(task_id=fixture.task.task_id, owner="owner", session="session", route="route", workspace="workspace", token=token, **extra)


def test_exact_read_resumes_same_task_once_without_second_plan(handoff):
    f = handoff
    assert f.task.priority == 1 and f.queue._active_count == 0
    assert f.queue.ui_reads(f.registry, "owner", "session", "route", "workspace") == [f.request]
    assert f.request["nonce"] not in json.dumps(f.queue.get_status(f.task.task_id))
    receipt = f.queue.claim_ui_read(f.request, f.registry)
    with pytest.raises(ValueError):
        f.queue.claim_ui_read(f.request, f.registry)
    output = {"path": "D:/owned/42.txt", "content": "42", "size": 2}
    done = completion(f, receipt["token"], result=output)
    assert f.queue.complete_ui_read(done, f.registry)["status"] == "pending"
    with pytest.raises(ValueError):
        f.queue.complete_ui_read(done, f.registry)
    f.run()
    assert f.task.status == TaskStatus.COMPLETED and len(f.planned) == 1
    assert f.task.checkpoint["step_results"] == {1: json.dumps(output)}
    assert len(f.task.checkpoint["completed_steps"]) == 1


def test_actual_runtime_event_metadata_preserves_exact_single_use_claim(handoff):
    from local_mode.desktop_host import DesktopRuntime

    f = handoff
    emitted = []
    runtime = SimpleNamespace(current_request="owned-read-fixture",
                              emit=lambda _name, data: emitted.append(data))
    DesktopRuntime.event(runtime, "tasks.ui.read", f.request)
    event = emitted[0]
    assert event["request_id"] == "owned-read-fixture" and isinstance(event["monotonic"], float)
    with pytest.raises(ValueError):
        f.queue.claim_ui_read(dict(event, unknown_dispatch_field=True), f.registry)
    token = f.queue.claim_ui_read(event, f.registry)["token"]
    assert f.task.ui_read["request"] == f.request
    assert event["request_id"] == "owned-read-fixture"
    with pytest.raises(ValueError):
        f.queue.claim_ui_read(event, f.registry)
    assert f.queue.complete_ui_read(completion(f, token, result={"content": "42"}), f.registry)["status"] == "pending"


def test_native_read_failure_retains_exact_bounded_result_and_resolved_target(handoff):
    f = handoff
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    output = {"error": "owned read failed", "path": f.request["args"]["path"]}
    reply = f.queue.complete_ui_read(completion(f, token, result=output), f.registry)
    failed = f.queue.get_status(f.task.task_id)
    assert reply["status"] == "failed" and f.task not in f.queue._queue
    assert failed["result"]["failed_step_result"] == output
    assert failed["result"]["failed_step"]["parameters"] == f.request["args"]
    assert failed["result"]["step_results"] == {} and len(f.planned) == 1
    with pytest.raises(ValueError):
        f.queue.complete_ui_read(completion(f, token, result=output), f.registry)


@pytest.mark.parametrize("field", ["owner", "session", "route", "workspace"])
def test_pending_claim_and_completion_require_exact_origin(handoff, field):
    f = handoff
    origin = dict(owner="owner", session="session", route="route", workspace="workspace")
    origin[field] = "other"
    assert f.queue.ui_reads(f.registry, **origin) == []
    with pytest.raises(ValueError):
        f.queue.claim_ui_read(dict(f.request, **{field: "other"}), f.registry)
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    with pytest.raises(ValueError):
        f.queue.complete_ui_read(dict(completion(f, token, result={}), **{field: "other"}), f.registry)


@pytest.mark.parametrize("phase", ["before_claim", "after_claim"])
def test_stop_or_provider_switch_invalidates_late_read(handoff, phase):
    f = handoff
    token = f.queue.claim_ui_read(f.request, f.registry)["token"] if phase == "after_claim" else None
    assert f.queue.cancel(f.task.task_id)
    with pytest.raises(ValueError):
        if token:
            f.queue.complete_ui_read(completion(f, token, result={"content": "late"}), f.registry)
        else:
            f.queue.claim_ui_read(f.request, f.registry)
    assert f.task.status == TaskStatus.CANCELLED and f.task not in f.queue._queue


def test_actual_provider_route_change_cancels_pending_claim(handoff):
    f = handoff
    f.runtime.text_provider.identity = ("owner", "session", "new-route")
    assert f.queue.ui_reads(f.registry, "owner", "session", "route", "workspace") == []
    with pytest.raises(ValueError):
        f.queue.claim_ui_read(f.request, f.registry)
    assert f.task.status == TaskStatus.CANCELLED


@pytest.mark.parametrize("change", ["owner", "session", "route", "detach"])
def test_attachment_invalidation_cancels_even_when_no_frontend_callback_arrives(handoff, change):
    f = handoff
    if change == "detach":
        f.runtime.shared_agent.detach()
    elif change == "route":
        f.runtime.text_provider.identity = ("owner", "session", "new")
    else:
        f.runtime.shared_agent.attach("fixture", "new" if change == "session" else "session", "new" if change == "owner" else "owner")
    f.queue.invalidate_ui_reads(f.registry)
    assert f.task.status == TaskStatus.CANCELLED and f.task.ui_read is None


@pytest.mark.parametrize("output", [None, {"error": "guard refused"}, {"content": "x" * 48001}])
def test_failed_or_oversized_native_result_is_terminal_without_recovery(handoff, output):
    f = handoff
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.complete_ui_read(completion(f, token, result=output), f.registry)
    assert f.task.status == TaskStatus.FAILED and f.task not in f.queue._queue
    assert f.task.checkpoint["completed_steps"] == [] and len(f.planned) == 1


@pytest.mark.parametrize("args", [{"path": "relative.txt"}, {}, {"path": "D:/owned/.env"}])
def test_invalid_or_protected_read_fails_locally_without_handoff_recovery(handoff, monkeypatch, args):
    f = handoff
    with pytest.raises(execution.TaskExecutionFailed):
        execution._call_tool("read_file", args, None, registry=f.registry)


def test_only_explicit_read_metadata_is_discoverable(handoff):
    from agent.planner import _descriptors
    f = handoff
    f.registry.workspace += ({"name": "write_file", "parameters": {}, "executor": "native-host", "available": True},)
    catalog = _descriptors(f.registry)
    assert "read_file" in catalog and "write_file" not in catalog
    assert "utility_run" not in catalog and "mcp_call" not in catalog


def test_queued_native_discovery_requires_absolute_root_without_mutating_canonical_schema(handoff):
    from agent.planner import _descriptors
    f = handoff
    f.registry.register_workspace([{"name":"grep","description":"Search","approval":"none",
        "queue_read_only":True,"queue_path_fields":["root"],
        "parameters":{"type":"object","properties":{"root":{"type":"string"},"pattern":{"type":"string"}},"required":["pattern"]}}])
    canonical = deepcopy(f.registry.queued_ui_read("grep")["parameters"])
    queued = _descriptors(f.registry)["grep"]["parameters"]
    assert set(queued["required"]) == {"pattern", "root"}
    assert "explicit absolute path" in queued["properties"]["root"]["description"]
    from jsonschema import Draft202012Validator
    Draft202012Validator(queued).validate({"root":"D:/owned","pattern":"42"})
    with pytest.raises(Exception):
        Draft202012Validator(queued).validate({"root":"relative","pattern":"42"})
    assert f.registry.queued_ui_read("grep")["parameters"] == canonical


@pytest.mark.parametrize("phase", ["before_claim", "after_claim"])
def test_workspace_change_before_pending_callback_or_completion_rejects_original_scope(handoff, phase):
    f = handoff
    token = f.queue.claim_ui_read(f.request, f.registry)["token"] if phase == "after_claim" else None
    f.runtime.shared_agent.workspace = "different-distro-same-paths"
    assert f.queue.ui_reads(f.registry, "owner", "session", "route", "different-distro-same-paths") == []
    with pytest.raises(ValueError):
        if token:
            f.queue.complete_ui_read(completion(f, token, result={"content": "late"}), f.registry)
        else:
            f.queue.claim_ui_read(f.request, f.registry)
    assert f.task.status == TaskStatus.CANCELLED


def test_workspace_is_captured_at_submit_not_at_ui_callback(handoff):
    f = handoff
    events=[]
    task_id=f.queue.submit("owned",registry=f.registry,approval_owner=("owner","session"),on_pending=events.append)
    task=f.queue._tasks[task_id]
    assert task.ui_workspace == "workspace"
    f.runtime.shared_agent.workspace="new-workspace"
    f.queue._queue.remove(task);task.status=TaskStatus.RUNNING;f.queue._active_count+=1
    f.queue._run_task_body(task)
    assert task.status == TaskStatus.CANCELLED and task.ui_read is None and events == []
