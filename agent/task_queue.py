import threading
import json
import time
import uuid
from copy import deepcopy
from contextvars import copy_context
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Any
from agent.task_approval import (QueueApprovalRequired, QueueUiReadRequired, QueueUiDispatchRequired, ToolCancelled,
                                ToolTimedOut, TaskExecutionFailed, ToolNotDispatched,
                                ToolReportedFailure, FAILED_STEP_MESSAGE, TIMED_OUT_STEP_MESSAGE)
from local_mode.messages import OwnedText, OwnedValueError

STOP_OUTCOME = OwnedText(
    "Task stopped. Completed work is preserved. An interrupted action may have taken effect; check its state before retrying.")


def _valid_mcp_result(result):
    if (not isinstance(result, dict) or not isinstance(result.get("content"), list)
            or ("isError" in result and type(result["isError"]) is not bool)
            or ("structuredContent" in result and not isinstance(result["structuredContent"], dict))):
        return False
    for part in result["content"]:
        if not isinstance(part, dict):
            return False
        kind = part.get("type")
        if not isinstance(kind, str):
            return False
        fields = {"text": ("text",), "image": ("data", "mimeType"),
                  "audio": ("data", "mimeType"), "resource_link": ("uri", "name")}.get(kind)
        if fields is not None:
            if not all(isinstance(part.get(key), str) for key in fields):
                return False
        elif kind == "resource":
            resource = part.get("resource")
            if (not isinstance(resource, dict) or not isinstance(resource.get("uri"), str)
                    or not (isinstance(resource.get("text"), str) or isinstance(resource.get("blob"), str))):
                return False
        else:
            return False
    return True


def _partial_outcome(task):
    result = {"completed_steps": deepcopy(task.checkpoint.get("completed_steps", [])),
              "step_results": deepcopy(task.checkpoint.get("step_results", {})),
              "skipped_steps": deepcopy(task.checkpoint.get("skipped_steps", []))}
    if "approved_step_result" in task.checkpoint:
        result["pending_step_result"] = deepcopy(task.checkpoint["approved_step_result"])
    if "failed_step" in task.checkpoint:
        result["failed_step"] = deepcopy(task.checkpoint["failed_step"])
        result["failure_stage"] = task.checkpoint.get("failure_stage", "execution")
    if "failed_step_result" in task.checkpoint:
        result["failed_step_result"] = deepcopy(task.checkpoint["failed_step_result"])
    return result


def _record_cancelled(task):
    _revoke_ui_action(task)
    task.status = TaskStatus.CANCELLED
    task.error = STOP_OUTCOME
    task.result = _partial_outcome(task)
    task.pending_action = task.approved_action = None
    task.ui_read = None


def _record_failed(task, message, stage="execution"):
    _revoke_ui_action(task)
    checkpoint = task.checkpoint
    if "failed_step" not in checkpoint:
        steps = checkpoint.get("plan", {}).get("steps", [])
        index = checkpoint.get("next_step", 0)
        step = deepcopy(steps[index]) if 0 <= index < len(steps) else {}
        if "pending_parameters" in checkpoint:
            step["parameters"] = deepcopy(checkpoint["pending_parameters"])
        checkpoint["failed_step"] = step
    checkpoint["failure_message"] = message
    checkpoint.setdefault("failure_stage", stage)
    task.status = TaskStatus.FAILED
    task.error = message
    task.result = _partial_outcome(task)
    task.pending_action = task.approved_action = task.ui_read = None


def _revoke_ui_action(task):
    state = task.ui_read
    if state:
        request = state["request"]
        event = getattr(getattr(task.registry, "runtime", None), "event", None)
        if callable(event):
            try:
                event("tasks.ui.cancel", {key: request[key] for key in
                    ("task_id", "nonce", "owner", "session", "route", "workspace")})
            except Exception:
                pass  # The UI also checks claim liveness while work is active.
        task.ui_read = None


def _pending_step_number(task):
    steps = task.checkpoint.get("plan", {}).get("steps", [])
    index = task.checkpoint.get("next_step", 0)
    return steps[index].get("step", "?") if 0 <= index < len(steps) else "?"


class TaskStatus(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class TaskPriority(Enum):
    LOW    = 3
    NORMAL = 2
    HIGH   = 1   


@dataclass(order=True)
class Task:
    priority:    int                       
    created_at:  float = field(compare=False)
    task_id:     str   = field(compare=False)
    goal:        str   = field(compare=False)
    status:      TaskStatus = field(compare=False, default=TaskStatus.PENDING)
    result:      Any        = field(compare=False, default=None)
    error:       str        = field(compare=False, default="")
    speak:       Any        = field(compare=False, default=None)   
    on_complete: Any        = field(compare=False, default=None)  
    cancel_flag: threading.Event = field(compare=False, default_factory=threading.Event)
    checkpoint: dict = field(compare=False, default_factory=dict)
    pending_action: Any = field(compare=False, default=None)
    approved_action: Any = field(compare=False, default=None)
    registry: Any = field(compare=False, default=None)
    on_pending: Any = field(compare=False, default=None)
    execution_context: Any = field(compare=False, default_factory=copy_context)
    approval_owner: Any = field(compare=False, default=None)
    ui_read: Any = field(compare=False, default=None)
    ui_origin: Any = field(compare=False, default=None)
    ui_workspace: Any = field(compare=False, default=None)
    ui_catalog_revision: Any = field(compare=False, default=None)
    native_run: Any = field(compare=False, default=None)
    native_claim: Any = field(compare=False, default=None)
    native_settled: list = field(compare=False, default_factory=list)


class TaskQueue:
    def __init__(self, max_concurrent: int = 1):
        self._queue:        list[Task]       = []
        self._lock:         threading.Lock   = threading.Lock()
        self._condition:    threading.Condition = threading.Condition(self._lock)
        self._tasks:        dict[str, Task]  = {} 
        self._running:      bool             = False
        self._worker_thread: threading.Thread | None = None
        self._max_concurrent = max_concurrent
        self._active_count   = 0
        self._executor       = None  

    def _get_executor(self):
        if self._executor is None:
            from agent.executor import AgentExecutor
            self._executor = AgentExecutor()
        return self._executor

    def start(self) -> None:
        if self._running:
            return
        self._running      = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AgentTaskQueue"
        )
        self._worker_thread.start()
        print("[TaskQueue] ✅ Started")

    def stop(self) -> None:
        self._running = False
        with self._condition:
            for task in self._tasks.values():
                if task.ui_read:
                    task.cancel_flag.set()
                    _record_cancelled(task)
            self._condition.notify_all()
        print("[TaskQueue] 🔴 Stopped")

    def submit(
        self,
        goal:        str,
        priority:    TaskPriority = TaskPriority.NORMAL,
        speak:       Callable | None = None,
        on_complete: Callable | None = None,
        registry=None,
        on_pending=None,
        approval_owner=None,
    ) -> str:

        task_id = str(uuid.uuid4())[:8]
        task    = Task(
            priority    = priority.value,
            created_at  = time.time(),
            task_id     = task_id,
            goal        = goal,
            speak       = speak,
            on_complete = on_complete,
            registry    = registry,
            on_pending  = on_pending,
            approval_owner = approval_owner,
            ui_origin = getattr(getattr(getattr(registry, "runtime", None), "text_provider", None), "identity", None),
            ui_workspace = getattr(getattr(getattr(registry, "runtime", None), "shared_agent", None), "workspace", None),
            ui_catalog_revision = getattr(registry, "revision", None),
        )

        with self._condition:
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._tasks[task_id] = task
            self._condition.notify()

        print(f"[TaskQueue] 📥 Task queued: [{task_id}] {goal[:60]}")
        return task_id

    def cancel(self, task_id: str) -> bool:

        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False

            task.cancel_flag.set()
            _record_cancelled(task)
            try:
                self._queue.remove(task)
            except ValueError:
                pass
            print(f"[TaskQueue] 🚫 Task cancelled: [{task_id}]")
            return True

    def get_status(self, task_id: str) -> dict | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            return self._status(task)

    @staticmethod
    def _status(task):
        return {
                "task_id": task.task_id,
                "goal":    task.goal,
                "status":  task.status.value,
                "result":  task.result,
                "error":   task.error,
                "priority": task.priority,
                "completed_steps": deepcopy(task.checkpoint.get("completed_steps", [])),
                "step_results": deepcopy(task.checkpoint.get("step_results", {})),
                "skipped_steps": deepcopy(task.checkpoint.get("skipped_steps", [])),
                "pending_action": deepcopy(task.pending_action),
            }

    def status_for(self, registry, task_id):
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise OwnedValueError("Queued task not found.")
            if task.registry is not registry or task.approval_owner is None or not self._approval_owner_active(task):
                raise OwnedValueError("Queued task belongs to another chat session.")
            return self._status(task)

    def get_all_statuses(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "goal":    t.goal[:50],
                    "status":  t.status.value,
                    "priority": t.priority,
                    "pending_action": deepcopy(t.pending_action),
                }
                for t in self._tasks.values()
            ]

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for t in self._queue if t.status == TaskStatus.PENDING)

    def _ui_read_task(self, request, registry, *, token=False):
        task = self._tasks.get(request.get("task_id"))
        state = task.ui_read if task else None
        if (not state or task.registry is not registry
                or task.status not in ({TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL} if state.get("dispatch") else {TaskStatus.RUNNING})
                or task.cancel_flag.is_set() or task.approval_owner != (request.get("owner"), request.get("session"))
                or not task.ui_origin or request.get("route") != task.ui_origin[2]
                or not task.ui_workspace or request.get("workspace") != task.ui_workspace
                or (token and state.get("dispatch") and request.get("nonce") != state["request"]["nonce"])
                or (state["claim"] != request.get("token") if token else state["request"] != request)):
            raise OwnedValueError("Expired or changed queued task approval")
        if (not self._approval_owner_active(task) or time.monotonic() >= state["deadline"]
                or registry.runtime.text_provider.identity != task.ui_origin
                or registry.runtime.shared_agent.workspace != task.ui_workspace
                or (state.get("dispatch") and state["revision"] != registry.revision)):
            task.cancel_flag.set()
            _record_cancelled(task)
            raise OwnedValueError("Queued approval belongs to another chat session")
        return task

    def ui_reads(self, registry, owner, session, route, workspace):
        """Private UI IPC only; opaque dispatch nonces never enter the model catalogue."""
        with self._lock:
            shared = registry.runtime.shared_agent
            if (not shared.attached or (shared.owner, shared.session) != (owner, session)
                    or registry.runtime.text_provider.identity != (owner, session, route)):
                return []
            return [deepcopy(t.ui_read["request"]) for t in self._tasks.values()
                    if t.registry is registry and t.approval_owner == (owner, session)
                    and t.status == TaskStatus.RUNNING and t.ui_read and not t.ui_read["claim"]
                    and t.ui_origin == (owner, session, route)
                    and workspace and shared.workspace == workspace == t.ui_workspace
                    and not t.cancel_flag.is_set() and time.monotonic() < t.ui_read["deadline"]][:16]

    def invalidate_ui_reads(self, registry):
        with self._condition:
            for task in self._tasks.values():
                native_history = bool(task.native_run or task.native_settled)
                if (task.registry is registry and (task.ui_read or native_history)
                        and task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL}
                        and (not self._approval_owner_active(task)
                        or registry.runtime.text_provider.identity != task.ui_origin
                        or registry.runtime.shared_agent.workspace != task.ui_workspace
                        or (task.ui_read and task.ui_read.get("dispatch") and task.ui_read["revision"] != registry.revision)
                        or (native_history and task.ui_catalog_revision != registry.revision))):
                    task.cancel_flag.set()
                    _record_cancelled(task)
            self._condition.notify_all()

    def claim_ui_read(self, request, registry):
        # Runtime events add these audit fields. They are not part of the stored
        # action; all other fields must still match its exact one-use request.
        request = {key: value for key, value in request.items()
                   if key not in {"request_id", "monotonic"}}
        with self._lock:
            task = self._ui_read_task(request, registry)
            if task.ui_read["claim"]:
                raise OwnedValueError("Expired or changed queued task approval")
            try:
                descriptor = registry.queued_ui_read(request["name"]) or registry.queued_ui_dispatch(request["name"])
                if not descriptor:
                    raise ValueError("Read capability changed")
                registry.validate(request["name"], request["args"])
            except Exception as error:
                task.ui_read = None
                _record_failed(task, OwnedText("Queued task action failed"), "validation")
                raise OwnedValueError("Queued task action failed") from error
            task.ui_read["claim"] = uuid.uuid4().hex
            if task.ui_read.get("dispatch"):
                task.status = TaskStatus.AWAITING_APPROVAL
                task.native_claim = {key: task.ui_read["request"][key] for key in
                    ("task_id", "nonce", "owner", "session", "route", "workspace")}
                task.native_claim["token"] = task.ui_read["claim"]
                if task.ui_read["request"]["dispatch"] == "mcp":
                    task.native_claim.update(kind="mcp", cleanup_nonce=task.ui_read["request"]["cleanup_nonce"])
            return {"token": task.ui_read["claim"]}

    def check_ui_read(self, request, registry):
        """A claimed action's lease; failure must physically abort its UI process."""
        with self._lock:
            task = self._ui_read_task(request, registry, token=True)
            if not request.get("token"):
                raise OwnedValueError("Expired or changed queued task approval")
            task.ui_read["last_seen"] = time.monotonic()
            return {"ok": True}

    def start_ui_dispatch(self, request, registry):
        with self._lock:
            task = self._ui_read_task(request, registry, token=True)
            if not request.get("token") or not task.ui_read.get("dispatch") or task.ui_read.get("started"):
                raise OwnedValueError("Expired or changed queued task approval")
            if self._active_count + self._native_active_count() >= self._max_concurrent:
                return {"ok": False, "waiting": True}
            task.ui_read["started"] = True
            task.ui_read["last_seen"] = time.monotonic()
            task.native_run = deepcopy(task.native_claim)
            task.status = TaskStatus.RUNNING
            return {"ok": True}

    def _native_active_count(self):
        return sum(1 for task in self._tasks.values() if task.native_run)

    def settled_ui_dispatch(self, request, registry):
        """Cleanup acknowledgement only; a revoked lease can never resume work."""
        with self._condition:
            task = self._tasks.get(request.get("task_id"))
            if not task or task.registry is not registry:
                raise OwnedValueError("Expired or changed queued task approval")
            if request in task.native_settled:
                return {"ok": True}
            if not task.native_claim or request != task.native_claim:
                raise OwnedValueError("Expired or changed queued task approval")
            if (not self._approval_owner_active(task)
                    or registry.runtime.text_provider.identity != task.ui_origin
                    or registry.runtime.shared_agent.workspace != task.ui_workspace
                    or task.ui_catalog_revision != registry.revision):
                task.cancel_flag.set()
                _record_cancelled(task)
            elif task.ui_read is not None:
                # Cleanup without the result means transport was uncertain. End
                # the step without inventing success or scheduling it again.
                _record_failed(task, OwnedText(FAILED_STEP_MESSAGE, _pending_step_number(task)))
            task.native_run = None
            task.native_claim = None
            task.native_settled.append(deepcopy(request))
            self._condition.notify_all()
            return {"ok": True}

    def orphaned_ui_dispatches(self, registry):
        with self._lock:
            active = [task for task in self._tasks.values() if task.registry is registry and task.native_run]
            for task in active:
                if task.ui_read and time.monotonic() - task.ui_read.get("last_seen", time.monotonic()) > 5:
                    task.cancel_flag.set()
                    _record_cancelled(task)
            return {"items": [deepcopy(task.native_run) for task in active if task.ui_read is None][:16],
                    "watch": bool(active)}

    def complete_ui_read(self, request, registry):
        with self._condition:
            task = self._ui_read_task(request, registry, token=True)
            if not request.get("token"):
                raise OwnedValueError("Expired or changed queued task approval")
            result = request.get("result")
            dispatch = task.ui_read.get("dispatch")
            serializable = True
            try:
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                serializable = False
                encoded = "null"
                request = dict(request, error=True)
            failed = (result is None or bool(request.get("error")) or len(encoded.encode()) > 48000
                      or (isinstance(result, dict) and ("error" in result or result.get("ok") is False)))
            if dispatch == "mcp":
                failed = failed or not task.ui_read.get("started") or not (isinstance(result, dict)
                    and result.get("output_complete") is True and result.get("transport_settled") is True
                    and _valid_mcp_result(result.get("result"))
                    and ("isError" not in result["result"] or result["result"]["isError"] is False))
            elif dispatch:
                failed = failed or not task.ui_read.get("started") or not (isinstance(result, dict)
                    and type(result.get("exit_code")) is int and result["exit_code"] == 0
                    and result.get("timed_out") is False and result.get("truncated") is False
                    and result.get("decoding_loss") is False and result.get("output_complete") is True
                    and isinstance(result.get("stdout"), str) and isinstance(result.get("stderr"), str))
            task.ui_read = None  # Consume before scheduling; results cannot replay a read.
            if request.get("cancelled") is True:
                task.cancel_flag.set()
                _record_cancelled(task)
            elif failed:
                if serializable and isinstance(result, dict) and len(encoded.encode()) <= 48000:
                    task.checkpoint["failed_step_result"] = deepcopy(result)
                if dispatch:
                    timed_out = isinstance(result, dict) and result.get("timed_out") is True
                    _record_failed(task, OwnedText(TIMED_OUT_STEP_MESSAGE if timed_out else FAILED_STEP_MESSAGE,
                        _pending_step_number(task)), "timeout" if timed_out else "execution")
                else:
                    _record_failed(task, OwnedText("Queued task action failed"))
            else:
                task.checkpoint["approved_step_result"] = encoded
                task.status = TaskStatus.PENDING
                self._queue.append(task)
                self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._condition.notify_all()
            return {"ok": True, "task_id": task.task_id, "status": task.status.value}

    def _pending_request(self, request, registry):
        task = self._tasks.get(request.get("task_id"))
        if (not task or task.status != TaskStatus.AWAITING_APPROVAL
                or task.cancel_flag.is_set() or not task.pending_action
                or task.registry is not registry or request != task.pending_action):
            raise OwnedValueError("Expired or changed queued task approval")
        if request["name"] == "tasks.resume":
            raise OwnedValueError("A queued approval cannot resume another queued approval")
        if not self._approval_owner_active(task):
            raise OwnedValueError("Queued approval belongs to another chat session")
        return task

    @staticmethod
    def _approval_owner_active(task):
        if task.approval_owner is None:
            return True
        shared = task.registry.runtime.shared_agent
        return shared.attached and (shared.owner, shared.session) == task.approval_owner

    def pending_risky(self, request, registry):
        with self._lock:
            task = self._pending_request(request, registry)
            pending = deepcopy(task.pending_action)
        registry.validate(pending["name"], pending["args"])
        return registry.risky(pending["name"], pending["args"])

    def resume(self, request, registry):
        """Called only by the canonical service after its shared receipt policy."""
        with self._condition:
            task = self._pending_request(request, registry)
            task.approved_action = {"request": task.pending_action,
                                    "was_risky": registry.risky(task.pending_action["name"], task.pending_action["args"])}
            task.pending_action = None
            task.status = TaskStatus.PENDING
            self._queue.append(task)
            self._queue.sort(key=lambda t: (t.priority, t.created_at))
            self._condition.notify()
            return {"ok": True, "task_id": task.task_id, "status": "pending",
                    "hint": "The same queued task will continue from its saved step; completed steps will not run again."}

    def deny(self, request, registry):
        with self._condition:
            task = self._tasks.get(request.get("task_id"))
            if not task or not task.pending_action or task.pending_action["approval_id"] != request.get("approval_id"):
                raise OwnedValueError("Expired or changed queued task approval")
            self._pending_request(task.pending_action, registry)
            task.cancel_flag.set()
            _record_cancelled(task)
            return {"ok": True, "task_id": task.task_id, "status": "cancelled"}

    def pending_for(self, registry, owner, session):
        with self._lock:
            shared = registry.runtime.shared_agent
            if not shared.attached or (shared.owner, shared.session) != (owner, session):
                return {"ready": False, "items": []}
            tasks = [task for task in self._tasks.values()
                     if task.registry is registry and task.approval_owner == (owner, session)
                     and task.status == TaskStatus.AWAITING_APPROVAL and task.pending_action]
            return {"ready": True, "items": [{"task_id": task.task_id, "goal": task.goal,
                    "priority": task.priority, "owner": owner, "session": session,
                    "request": {"name": "tasks.resume", "args": deepcopy(task.pending_action)}}
                    for task in tasks[:16]], "omitted": max(0, len(tasks) - 16)}

    def _worker_loop(self) -> None:
        while self._running:
            task = None

            with self._condition:
                while self._running and not self._next_task():
                    for waiting in self._tasks.values():
                        if waiting.ui_read and time.monotonic() >= waiting.ui_read["deadline"]:
                            waiting.cancel_flag.set()
                            _record_cancelled(waiting)
                    self._condition.wait(timeout=1.0)
                task = self._next_task()
                if task:
                    task.status = TaskStatus.RUNNING
                    self._active_count += 1
                    try:
                        self._queue.remove(task)
                    except ValueError:
                        pass

            if task:
                threading.Thread(
                    target=lambda task=task: task.execution_context.copy().run(self._run_task, task),
                    daemon=True,
                    name=f"AgentTask-{task.task_id}"
                ).start()

    def _next_task(self) -> Task | None:
        if self._active_count + self._native_active_count() >= self._max_concurrent:
            return None
        for task in self._queue:
            if task.status == TaskStatus.PENDING and not task.cancel_flag.is_set() and not task.native_run:
                return task
        return None

    def _run_task(self, task: Task) -> None:
        from local_mode.model import current_text_provider, using_text_provider
        provider = current_text_provider()
        if provider is None:
            self._run_task_body(task)
            return

        def task_provider(request, *, cancel_flag=None, deadline=None):
            if task.cancel_flag.is_set():
                raise ToolCancelled("Task cancelled")
            try:
                result = provider(request, cancel_flag=cancel_flag if cancel_flag is not None else task.cancel_flag,
                                  deadline=deadline)
            except Exception as error:
                if task.cancel_flag.is_set():
                    raise ToolCancelled("Task cancelled") from error
                raise
            if task.cancel_flag.is_set():
                raise ToolCancelled("Task cancelled")
            return result

        with using_text_provider(task_provider):
            self._run_task_body(task)

    def _run_task_body(self, task: Task) -> None:
        print(f"[TaskQueue] ▶️ Running: [{task.task_id}] {task.goal[:60]}")
        try:
            executor = self._get_executor()
            with self._lock:
                approved, task.approved_action = task.approved_action, None
            if approved:
                from agent.executor import _call_tool_with_timeout
                if task.cancel_flag.is_set() or not self._approval_owner_active(task):
                    raise ToolCancelled("Task cancelled")
                pending = approved["request"]
                if not approved["was_risky"] and task.registry.risky(pending["name"], pending["args"]):
                    raise QueueApprovalRequired(pending["name"], pending["args"])
                task.checkpoint["approved_step_result"] = _call_tool_with_timeout(
                    pending["name"], pending["args"], task.speak, task.cancel_flag,
                    registry=task.registry, approved=True)
            result   = executor.execute(
                goal        = task.goal,
                speak       = task.speak,
                cancel_flag = task.cancel_flag,
                checkpoint  = task.checkpoint,
                registry    = task.registry,
            )

            with self._lock:
                if task.cancel_flag.is_set():
                    _record_cancelled(task)
                else:
                    task.status = TaskStatus.COMPLETED
                    task.result = result
                self._active_count -= 1

            if task.on_complete and not task.cancel_flag.is_set():
                try:
                    task.on_complete(task.task_id, result)
                except Exception as e:
                    print(f"[TaskQueue] ⚠️ on_complete callback error: {e}")

            if task.status == TaskStatus.COMPLETED:
                print(f"[TaskQueue] ✅ Completed: [{task.task_id}]")

        except QueueUiReadRequired as pending:
            with self._lock:
                dispatch = isinstance(pending, QueueUiDispatchRequired)
                if (task.cancel_flag.is_set() or task.approval_owner is None or not self._approval_owner_active(task)
                        or not task.ui_origin or task.ui_origin[:2] != task.approval_owner
                        or task.registry.runtime.text_provider.identity != task.ui_origin
                        or not task.ui_workspace or task.registry.runtime.shared_agent.workspace != task.ui_workspace
                        or (dispatch and task.ui_catalog_revision != task.registry.revision)):
                    _record_cancelled(task)
                else:
                    request = {"task_id": task.task_id, "nonce": uuid.uuid4().hex,
                               "owner": task.approval_owner[0], "session": task.approval_owner[1],
                               "route": task.ui_origin[2],
                               "workspace": task.ui_workspace,
                               "name": pending.name, "args": deepcopy(pending.args_snapshot)}
                    if dispatch:
                        descriptor = task.registry.queued_ui_dispatch(pending.name)
                        request["dispatch"] = descriptor["queue_dispatch"]
                        if request["dispatch"] == "mcp":
                            request["cleanup_nonce"] = uuid.uuid4().hex
                    task.ui_read = {"request": request, "claim": None,
                                    "dispatch": request.get("dispatch"), "revision": task.registry.revision,
                                    "deadline": time.monotonic() + (900 if dispatch else 120)}
                self._active_count -= 1
            if task.ui_read and task.on_pending:
                try:
                    task.on_pending({"ui_request": deepcopy(request)})
                except Exception:
                    self.cancel(task.task_id)
        except QueueApprovalRequired as pending:
            with self._lock:
                if task.cancel_flag.is_set():
                    _record_cancelled(task)
                else:
                    task.status = TaskStatus.AWAITING_APPROVAL
                    task.pending_action = {"task_id": task.task_id, "approval_id": uuid.uuid4().hex,
                                           "name": pending.name, "args": deepcopy(pending.args_snapshot)}
                self._active_count -= 1
            if task.status == TaskStatus.AWAITING_APPROVAL and task.on_pending:
                try:
                    task.on_pending(self.get_status(task.task_id))
                except Exception as error:
                    print(f"[TaskQueue] Approval notification failed: {error}")
        except ToolCancelled:
            with self._lock:
                _record_cancelled(task)
                self._active_count -= 1
        except ToolTimedOut:
            with self._lock:
                if task.cancel_flag.is_set():
                    _record_cancelled(task)
                else:
                    _record_failed(task, OwnedText(TIMED_OUT_STEP_MESSAGE, _pending_step_number(task)), "timeout")
                self._active_count -= 1
        except TaskExecutionFailed as error:
            with self._lock:
                if task.cancel_flag.is_set():
                    _record_cancelled(task)
                else:
                    message = error.message
                    stage = "validation" if isinstance(error, ToolNotDispatched) else "execution"
                    if isinstance(error, ToolReportedFailure):
                        task.checkpoint["failed_step_result"] = deepcopy(error.result)
                        timed_out = error.result.get("timed_out") is True
                        stage = "timeout" if timed_out else "execution"
                        message = OwnedText(TIMED_OUT_STEP_MESSAGE if timed_out else FAILED_STEP_MESSAGE, _pending_step_number(task))
                    _record_failed(task, message, stage)
                self._active_count -= 1
        except Exception:
            with self._lock:
                if task.cancel_flag.is_set():
                    _record_cancelled(task)
                else:
                    _record_failed(task, OwnedText(FAILED_STEP_MESSAGE, _pending_step_number(task)))
                self._active_count -= 1
            print(f"[TaskQueue] Failed: [{task.task_id}]")

        with self._condition:
            self._condition.notify()

_queue        = TaskQueue()
_queue_started = False
_queue_lock    = threading.Lock()


def get_queue() -> TaskQueue:
    global _queue_started
    with _queue_lock:
        if not _queue_started:
            _queue.start()
            _queue_started = True
    return _queue


def existing_queue():
    with _queue_lock:
        return _queue if _queue_started else None


def cancel_existing(*, stop=False) -> int:
    """Explicit Stop/close only; never creates or starts a queue as a side effect."""
    with _queue_lock:
        if not _queue_started:
            return 0
        queue = _queue
        with queue._condition:
            active = [task for task in queue._tasks.values() if task.status in {
                TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.AWAITING_APPROVAL}]
            for task in active:
                task.cancel_flag.set()
                _record_cancelled(task)
            queue._queue.clear()
            if stop:
                queue._running = False
            queue._condition.notify_all()
        worker = queue._worker_thread
    if stop and worker and worker is not threading.current_thread():
        worker.join(timeout=1.0)
    return len(active)
