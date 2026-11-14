"""Translation outcomes through the registry and queued executor; no model calls."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from actions import translate as translation_action
from agent import executor as execution
from agent.task_approval import TaskExecutionFailed
from agent.task_queue import TaskQueue, TaskStatus
from local_mode.tools import ToolRegistry


def test_successful_translation_preserves_text_verbatim(monkeypatch):
    text = "  Привет!\nerror is a literal word here.  "
    translator = Mock(return_value={"text": text})
    monkeypatch.setattr(translation_action, "translate", translator)
    assert translation_action.translate_action({"text": "hello", "to": "ru"}) == text
    result = ToolRegistry().execute("translate", {"text": "hello", "to": "ru"})
    assert result["ok"] is True and result["result"] == text
    assert translator.call_count == 2


def test_registry_preserves_structured_translation_failure(monkeypatch):
    translator = Mock(side_effect=RuntimeError("translation fixture failed"))
    monkeypatch.setattr(translation_action, "translate", translator)
    result = ToolRegistry().execute("translate", {"text": "hello", "to": "ru"})
    assert result == {"action": "translate", "ok": False, "error": "translation fixture failed"}
    translator.assert_called_once()


def test_queued_translation_failure_is_terminal_without_retry_or_downstream(monkeypatch):
    from local_mode import model, owned_process

    translator = Mock(side_effect=RuntimeError("translation fixture failed"))
    monkeypatch.setattr(translation_action, "translate", translator)
    registry = ToolRegistry()
    plan = Mock(return_value={"steps": [
        {"step": 1, "tool": "translate", "parameters": {"text": "hello", "to": "ru"}},
        {"step": 2, "tool": "translate", "parameters": {"text": "must not run"}},
    ]})
    summary = Mock(side_effect=AssertionError("Failure cannot produce a success summary"))
    provider = Mock(side_effect=AssertionError("Unexpected model call"))
    monkeypatch.setattr(execution, "create_plan", plan)
    monkeypatch.setattr(execution.AgentExecutor, "_summarize", summary)
    monkeypatch.setattr(model, "ContentModel", provider)

    # Replace only the process boundary, retaining real registry dispatch and
    # executor terminal-result handling. This does not test process cleanup.
    requests = []
    def worker(_command, *, payload, on_line, **_kwargs):
        request = json.loads(payload)
        requests.append(request)
        result = registry.execute(request["tool"], request["args"], approved=request["approved"])
        on_line(json.dumps({"event": "result", "result": result}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(owned_process, "run_owned", worker)

    queue = TaskQueue()
    task_id = queue.submit("translate fixture", registry=registry)
    task = queue._tasks[task_id]
    queue._queue.remove(task)
    task.status = TaskStatus.RUNNING
    queue._active_count += 1
    queue._run_task_body(task)

    status = queue.get_status(task_id)
    assert status["status"] == "failed"
    assert status["result"]["failed_step_result"] == {
        "action": "translate", "ok": False, "error": "translation fixture failed"}
    assert status["result"]["step_results"] == {}
    assert status["completed_steps"] == []
    assert status["result"]["failed_step"]["step"] == 1
    with pytest.raises(TaskExecutionFailed):
        execution.AgentExecutor().execute("translate fixture", registry=registry, checkpoint=task.checkpoint)
    assert len(requests) == 1
    translator.assert_called_once()
    plan.assert_called_once()
    summary.assert_not_called()
    provider.assert_not_called()
