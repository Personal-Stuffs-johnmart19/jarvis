"""Utility queue handoff uses one-use UI receipts; no inference or processes."""
from copy import deepcopy
from types import SimpleNamespace
import json
import time

import pytest

from agent import executor as execution
from agent.planner import _descriptors
from agent.task_queue import TaskQueue, TaskStatus
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent


def descriptor(name="utility_run", **extra):
    return {"name": name, "description": "Run owned utility", "approval": "required",
            "queue_dispatch": "utility", "queue_path_fields": ["cwd"],
            "parameters": {"type": "object", "properties": {
                "cwd": {"type": "string"}, "tool": {"type": "string"},
                "args": {"type": "array", "items": {"type": "string"}}},
                "required": ["tool", "args"], "additionalProperties": False}, **extra}


@pytest.fixture
def dispatch(monkeypatch, request):
    shared = SharedAgent(lambda *_: None)
    shared.attach("fixture", "session", "owner", "workspace")
    cancellations = []
    runtime = SimpleNamespace(shared_agent=shared,
        text_provider=SimpleNamespace(identity=("owner", "session", "route")),
        event=lambda name, value: cancellations.append((name, value)))
    registry = ServiceRegistry(runtime, lambda: None)
    mcp = getattr(request, "param", None) == "mcp"
    mcp_descriptor = descriptor("mcp_call", approval="dynamic", queue_dispatch="mcp", queue_path_fields=[],
        parameters={"type": "object", "properties": {"server_id": {"type": "string"},
            "tool_name": {"type": "string"}, "arguments": {"type": "object"}},
            "required": ["server_id", "tool_name", "arguments"], "additionalProperties": False})
    registry.register_workspace([mcp_descriptor if mcp else descriptor()])
    plans = []
    plan = {"steps": [{"step": 1, "tool": "utility_run", "parameters": {
        "tool": "python", "args": ["literal space", "$value", "a&b", "Привет"], "cwd": "D:/owned"}}]}
    if mcp:
        plan["steps"][0].update(tool="mcp_call", parameters={"server_id": "fake", "tool_name": "write",
            "arguments": {"path": "opaque/non-filesystem", "content": "literal"}})
    def create(*_args, **_kwargs):
        plans.append(True)
        return deepcopy(plan)
    monkeypatch.setattr(execution, "create_plan", create)
    monkeypatch.setattr(execution.AgentExecutor, "_summarize", lambda *_: "done")
    queue, events = TaskQueue(), []
    task_id = queue.submit("run utility", registry=registry,
                           approval_owner=("owner", "session"), on_pending=events.append)
    task = queue._tasks[task_id]
    def run():
        queue._queue.remove(task)
        task.status = TaskStatus.RUNNING
        queue._active_count += 1
        queue._run_task_body(task)
    run()
    request = deepcopy(events[0]["ui_request"])
    def completion(token, **extra):
        return {key: request[key] for key in ("task_id", "nonce", "owner", "session", "route", "workspace")} | {"token": token, **extra}
    return SimpleNamespace(queue=queue, task=task, registry=registry, runtime=runtime,
        events=events, request=request, run=run, plans=plans, completion=completion,
        cancellations=cancellations, plan=plan)


def success(**extra):
    return {"stdout": "42", "stderr": "", "exit_code": 0,
            "timed_out": False, "truncated": False, "decoding_loss": False, "output_complete": True, **extra}


def test_utility_opt_in_is_distinct_from_read_only_and_has_absolute_cwd(dispatch):
    f = dispatch
    item = _descriptors(f.registry)["utility_run"]
    assert item["queue_dispatch"] == "utility" and not item.get("queue_read_only")
    assert "cwd" in item["parameters"]["required"]
    assert "cwd" not in f.registry.queued_ui_dispatch("utility_run")["parameters"]["required"]
    assert f.request["dispatch"] == "utility"


def test_utility_result_resumes_same_checkpoint_once_without_plan_or_execution_replay(dispatch):
    f = dispatch
    claim = f.queue.claim_ui_read(f.request, f.registry)
    assert f.task.status == TaskStatus.AWAITING_APPROVAL
    assert f.queue.check_ui_read(f.completion(claim["token"]), f.registry) == {"ok": True}
    assert f.queue.start_ui_dispatch(f.completion(claim["token"]), f.registry) == {"ok": True}
    with pytest.raises(ValueError):
        f.queue.start_ui_dispatch(f.completion(claim["token"]), f.registry)
    f.plan["steps"][0]["parameters"]["args"] = ["mutated"]
    assert f.task.ui_read["request"]["args"]["args"][0] == "literal space"
    result = success()
    done = f.completion(claim["token"], result=result)
    assert f.queue.complete_ui_read(done, f.registry)["status"] == "pending"
    assert f.queue._native_active_count() == 1
    f.queue.settled_ui_dispatch(f.completion(claim["token"]), f.registry)
    with pytest.raises(ValueError):
        f.queue.complete_ui_read(done, f.registry)
    f.run()
    assert f.task.status == TaskStatus.COMPLETED and len(f.plans) == 1
    assert f.task.checkpoint["step_results"] == {1: json.dumps(result)}
    assert f.queue._active_count == 0


def test_dependent_utility_step_uses_saved_result_and_never_repeats_first_dispatch(dispatch):
    f = dispatch
    # The plan is fixed before handoff; append only to the owned fixture checkpoint.
    f.task.checkpoint["plan"]["steps"].append({"step": 2, "tool": "utility_run", "parameters": {
        "tool": "python", "cwd": "D:/owned", "args": [{"$result": {"step": 1, "path": ["stdout"]}}]}})
    first = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(first), f.registry)
    f.queue.complete_ui_read(f.completion(first, result=success()), f.registry)
    f.queue.settled_ui_dispatch(f.completion(first), f.registry)
    f.run()
    assert len(f.events) == 2 and len(f.plans) == 1
    second = deepcopy(f.events[1]["ui_request"])
    assert second["nonce"] != f.request["nonce"] and second["args"]["args"] == ["42"]
    assert f.task.checkpoint["next_step"] == 1
    token = f.queue.claim_ui_read(second, f.registry)["token"]
    second_origin = {key: second[key] for key in ("task_id", "nonce", "owner", "session", "route", "workspace")} | {"token": token}
    f.queue.start_ui_dispatch(second_origin, f.registry)
    f.queue.complete_ui_read(second_origin | {"result": success(stdout="final")}, f.registry)
    f.queue.settled_ui_dispatch(second_origin, f.registry)
    f.run()
    assert f.task.status == TaskStatus.COMPLETED and len(f.plans) == 1
    assert len(f.task.checkpoint["completed_steps"]) == 2
    assert json.loads(f.task.checkpoint["step_results"][1])["stdout"] == "42"
    assert json.loads(f.task.checkpoint["step_results"][2])["stdout"] == "final"


@pytest.mark.parametrize("output", [success(exit_code=1), success(timed_out=True, exit_code=None),
    success(truncated=True), success(exit_code=True), {"ok": True}, {"error": "refused"},
    success(decoding_loss=True, output_complete=False), success(output_complete=False),
    success(stdout="x" * 48001), None])
def test_non_success_native_results_never_complete_or_retry(dispatch, output):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    f.queue.complete_ui_read(f.completion(token, result=output), f.registry)
    assert f.task.status == TaskStatus.FAILED and f.task not in f.queue._queue
    assert len(f.plans) == 1 and f.task.checkpoint["completed_steps"] == []
    assert f.task.checkpoint["failure_stage"] == ("timeout" if isinstance(output, dict) and output.get("timed_out") else "execution")
    if isinstance(output, dict) and len(json.dumps(output).encode()) <= 48000:
        assert f.task.checkpoint["failed_step_result"] == output


def test_denial_is_terminal_without_dispatch_or_checkpoint_replay(dispatch):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.complete_ui_read(f.completion(token, cancelled=True, error=True), f.registry)
    assert f.task.status == TaskStatus.CANCELLED and f.task not in f.queue._queue
    assert f.task.checkpoint["completed_steps"] == []


def test_success_result_without_started_dispatch_cannot_complete_task(dispatch):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.complete_ui_read(f.completion(token, result=success()), f.registry)
    assert f.task.status == TaskStatus.FAILED and f.task.checkpoint["completed_steps"] == []


def test_native_execution_obeys_existing_queue_concurrency_limit(dispatch):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue._active_count = 1
    assert f.queue.start_ui_dispatch(f.completion(token), f.registry) == {"ok": False, "waiting": True}
    assert f.task.status == TaskStatus.AWAITING_APPROVAL and not f.task.ui_read.get("started")
    f.queue._active_count = 0
    assert f.queue.start_ui_dispatch(f.completion(token), f.registry) == {"ok": True}
    f.queue.submit("another task", registry=f.registry, approval_owner=("owner", "session"))
    assert f.queue._next_task() is None


def test_same_task_cannot_resume_before_cleanup_when_global_capacity_is_spare(dispatch):
    f = dispatch
    f.queue._max_concurrent = 2
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    f.queue.complete_ui_read(f.completion(token, result=success()), f.registry)
    other_id = f.queue.submit("other", registry=f.registry, approval_owner=("owner", "session"))
    assert f.queue._next_task() is f.queue._tasks[other_id]
    assert f.task.native_claim == f.completion(token)
    f.queue.settled_ui_dispatch(f.completion(token), f.registry)
    assert f.queue._next_task() is f.task


def test_cancelled_native_reservation_blocks_next_start_until_original_cleanup_ack(dispatch):
    f = dispatch
    second_id = f.queue.submit("second", registry=f.registry, approval_owner=("owner", "session"), on_pending=f.events.append)
    second = f.queue._tasks[second_id]
    f.queue._queue.remove(second);second.status = TaskStatus.RUNNING;f.queue._active_count += 1
    f.queue._run_task_body(second)
    request = f.events[-1]["ui_request"]
    second_token = f.queue.claim_ui_read(request, f.registry)["token"]
    second_origin = {key: request[key] for key in ("task_id", "nonce", "owner", "session", "route", "workspace")} | {"token": second_token}
    first_token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(first_token), f.registry)
    f.queue.cancel(f.task.task_id)
    assert f.task.ui_read is None and f.task.native_run is not None
    assert f.queue.start_ui_dispatch(second_origin, f.registry) == {"ok": False, "waiting": True}
    with pytest.raises(ValueError):
        f.queue.settled_ui_dispatch(f.completion("forged"), f.registry)
    assert f.queue._native_active_count() == 1
    assert f.queue.orphaned_ui_dispatches(f.registry)["items"] == [f.completion(first_token)]
    f.queue.settled_ui_dispatch(f.completion(first_token), f.registry)
    assert f.task.status == TaskStatus.CANCELLED and f.task not in f.queue._queue
    assert f.queue.start_ui_dispatch(second_origin, f.registry) == {"ok": True}
    assert f.queue.settled_ui_dispatch(f.completion(first_token), f.registry) == {"ok": True}
    assert f.queue._native_active_count() == 1  # Old cleanup cannot free the new run.


def test_orphan_recovery_never_frees_slot_on_clock_or_heartbeat_alone(dispatch):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    f.task.ui_read["last_seen"] = time.monotonic() - 10
    assert f.queue.orphaned_ui_dispatches(f.registry)["items"] == [f.completion(token)]
    assert f.task.status == TaskStatus.CANCELLED and f.queue._native_active_count() == 1
    f.runtime.shared_agent.detach()
    f.queue.settled_ui_dispatch(f.completion(token), f.registry)
    assert f.queue._native_active_count() == 0 and f.task.status == TaskStatus.CANCELLED


def test_cleanup_without_result_is_terminal_uncertainty_not_success_or_replay(dispatch):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    f.queue.settled_ui_dispatch(f.completion(token), f.registry)
    assert f.task.status == TaskStatus.FAILED and f.task not in f.queue._queue
    assert f.task.checkpoint["completed_steps"] == [] and f.queue._native_active_count() == 0


@pytest.mark.parametrize("change", ["route", "workspace", "detach", "catalog"])
def test_identity_change_after_result_before_cleanup_cannot_resume_next_step(dispatch, change):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    f.queue.complete_ui_read(f.completion(token, result=success()), f.registry)
    assert f.task.status == TaskStatus.PENDING
    if change == "route":
        f.runtime.text_provider.identity = ("owner", "session", "new")
    elif change == "workspace":
        f.runtime.shared_agent.workspace = "new"
    elif change == "detach":
        f.runtime.shared_agent.detach()
    else:
        f.registry.revision = "new"
    f.queue.invalidate_ui_reads(f.registry)
    assert f.task.status == TaskStatus.CANCELLED and f.queue._native_active_count() == 1
    f.queue.settled_ui_dispatch(f.completion(token), f.registry)
    assert f.task.status == TaskStatus.CANCELLED and f.queue._native_active_count() == 0
    assert json.loads(f.task.result["pending_step_result"])["stdout"] == "42"


@pytest.mark.parametrize("reason", ["cancel", "detach", "route", "workspace", "catalog", "deadline", "shutdown"])
def test_scoped_revocation_emits_native_abort_and_rejects_late_result(dispatch, reason):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)["token"]
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    if reason == "cancel":
        f.queue.cancel(f.task.task_id)
    elif reason == "shutdown":
        f.queue.stop()
    else:
        if reason == "detach":
            f.runtime.shared_agent.detach()
        elif reason == "route":
            f.runtime.text_provider.identity = ("owner", "session", "other")
        elif reason == "workspace":
            f.runtime.shared_agent.workspace = "other"
        elif reason == "catalog":
            f.registry.revision = "other"
        else:
            f.task.ui_read["deadline"] = time.monotonic() - 1
        with pytest.raises(ValueError):
            f.queue.check_ui_read(f.completion(token), f.registry)
    assert f.task.status == TaskStatus.CANCELLED
    assert f.task.native_run == f.completion(token) and f.queue._native_active_count() == 1
    assert f.cancellations == [("tasks.ui.cancel", {key: f.request[key] for key in
        ("task_id", "nonce", "owner", "session", "route", "workspace")})]
    with pytest.raises(ValueError):
        f.queue.complete_ui_read(f.completion(token, result=success()), f.registry)
    f.queue.settled_ui_dispatch(f.completion(token), f.registry)
    assert f.queue._native_active_count() == 0 and f.task.status == TaskStatus.CANCELLED


@pytest.mark.parametrize("change", [{"queue_dispatch": "mcp"}, {"name": "write_file"},
    {"approval": "none"}, {"queue_read_only": True}, {"queue_path_fields": ["other"]}])
def test_utility_dispatch_cannot_enable_other_native_capabilities(dispatch, change):
    with pytest.raises(ValueError):
        dispatch.registry.register_workspace([descriptor(**change)])


@pytest.mark.parametrize("cwd", [None, "relative", "D:/owned/.ssh", "D:/owned/.env"])
def test_invalid_or_protected_cwd_is_rejected_before_handoff(dispatch, cwd):
    args = {"tool": "python", "args": []}
    if cwd is not None:
        args["cwd"] = cwd
    with pytest.raises(execution.TaskExecutionFailed):
        execution._call_tool("utility_run", args, None, registry=dispatch.registry)

@pytest.mark.parametrize('dispatch', ['mcp'], indirect=True)
@pytest.mark.parametrize('native_result,successful', [
    ({'content': [{'type': 'text', 'text': 'written'}], 'structuredContent': {'id': 42}}, True),
    ({'content': [], 'isError': True}, False),
    ({'content': [], 'isError': 'false'}, False),
    ({'content': [], 'structuredContent': []}, False),
    ({'ok': True}, False),
    ({'content': [None]}, False),
    ({'content': [{'type': 'text'}]}, False),
    ({'content': [{'type': 'invented'}]}, False),
    ({'content': [{'type': {}}]}, False),
    ({'content': [{'type': 'text', 'text': 'x' * 48000}]}, False),
])
def test_mcp_kind_uses_complete_native_result_not_utility_shape(dispatch, native_result, successful):
    f = dispatch
    assert f.request['dispatch'] == 'mcp'
    assert len(f.request['cleanup_nonce']) == 32
    assert _descriptors(f.registry)['mcp_call']['queue_path_fields'] == []
    token = f.queue.claim_ui_read(f.request, f.registry)['token']
    assert f.task.native_claim['kind'] == 'mcp'
    assert f.task.native_claim['cleanup_nonce'] == f.request['cleanup_nonce']
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    envelope = {'result': native_result, 'transport_settled': True, 'output_complete': True}
    f.queue.complete_ui_read(f.completion(token, result=envelope), f.registry)
    assert f.task.status == (TaskStatus.PENDING if successful else TaskStatus.FAILED)
    assert f.queue._native_active_count() == 1
    origin = deepcopy(f.task.native_claim)
    f.queue.settled_ui_dispatch(origin, f.registry)
    assert f.queue._native_active_count() == 0
    if successful:
        f.run()
        assert f.task.status == TaskStatus.COMPLETED
        assert len(f.plans) == 1
    elif len(json.dumps(envelope).encode()) <= 48000:
        assert f.task.checkpoint['failed_step_result'] == envelope


@pytest.mark.parametrize('dispatch', ['mcp'], indirect=True)
def test_mcp_orphan_keeps_private_operation_family_and_secret(dispatch):
    f = dispatch
    token = f.queue.claim_ui_read(f.request, f.registry)['token']
    f.queue.start_ui_dispatch(f.completion(token), f.registry)
    f.queue.cancel(f.task.task_id)
    orphan = f.queue.orphaned_ui_dispatches(f.registry)['items'][0]
    assert orphan['kind'] == 'mcp' and orphan['cleanup_nonce'] == f.request['cleanup_nonce']
    wrong = dict(orphan, cleanup_nonce='0' * 32)
    with pytest.raises(ValueError):
        f.queue.settled_ui_dispatch(wrong, f.registry)
    assert f.queue._native_active_count() == 1
    f.queue.settled_ui_dispatch(orphan, f.registry)
    assert f.queue._native_active_count() == 0
