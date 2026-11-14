# actions/task_manager.py
# Jarvis — Agent Task Status & Control

from agent.task_queue import get_queue
from local_mode.messages import OwnedText, state_label


def task_manager(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Query or control background agent tasks.
    actions: status | list | cancel"""
    params = parameters or {}
    action = str(params.get("action", "list")).lower().strip()
    queue  = get_queue()

    if action in ("status", "check"):
        task_id = str(params.get("task_id", "")).strip()
        if not task_id:
            return "Please provide a task_id, sir."
        info = queue.get_status(task_id)
        if not info:
            return f"No task found with ID {task_id}, sir."
        status = info["status"]
        if status == "awaiting_approval":
            return {"ok": True, **info,
                    "resume": {"name": "tasks.resume", "args": info["pending_action"]},
                    "hint": "This queued task is paused before execution. Request this exact resume through jarvis_action and its normal approval UI; do not submit the goal again."}
        if status == "completed" and info.get("result"):
            return f"Task {task_id} completed. Result: {info['result']}"
        if status == "failed":
            return f"Task {task_id} failed. {info.get('error', '')}"
        if status == "cancelled":
            return f"Task {task_id} was cancelled."
        return f"Task {task_id} is {status}. Goal: {info['goal']}"

    if action in ("cancel", "stop", "abort"):
        task_id = str(params.get("task_id", "")).strip()
        if not task_id:
            return "Please provide a task_id to cancel, sir."
        if queue.cancel(task_id):
            return f"Task {task_id} cancelled."
        return f"Could not cancel task {task_id}. It may be already finished."

    if action in ("list", "all", "tasks"):
        tasks = queue.get_all_statuses()
        if not tasks:
            return "No background tasks, sir."
        lines = []
        for t in tasks[-8:]:
            status = OwnedText(state_label(t["status"])) if t["status"] == "awaiting_approval" else t["status"]
            lines.append(OwnedText("{0}: {1} — {2}", t["task_id"], status, t["goal"]))
        joined = OwnedText(" | ".join("{" + str(i) + "}" for i in range(len(lines))), *lines)
        return OwnedText("Tasks: {0}", joined)

    return f"Unknown task action: {action}, sir."
