"""Execute a validated queued plan once, preserving outcomes across approval handoff."""
import json
import os
import sys
import threading
from copy import deepcopy
from pathlib import Path
from typing import Callable

from agent.planner import create_plan
from agent.task_approval import (QueueApprovalRequired, ToolCancelled, ToolTimedOut,
                                 TaskExecutionFailed, ToolNotDispatched, QueueUiReadRequired, QueueUiDispatchRequired,
                                 ToolReportedFailure, FAILED_STEP_MESSAGE, TIMED_OUT_STEP_MESSAGE)
from local_mode.messages import OwnedError, OwnedText


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR = get_base_dir()


class UnknownToolError(RuntimeError):
    """The named tool has no registered dispatcher."""



def _inject_context(params: dict, tool: str, step_results: dict, goal: str = "") -> dict:
    """Resolve explicit value references only; literal arguments are never rewritten."""
    from copy import deepcopy

    def resolve(value, depth=0):
        if depth > 24:
            raise ValueError("Result reference nesting is too deep")
        if isinstance(value, list):
            return [resolve(item, depth + 1) for item in value]
        if not isinstance(value, dict):
            return value
        if set(value) != {"$result"}:
            return {key: resolve(item, depth + 1) for key, item in value.items()}
        reference = value["$result"]
        if (not isinstance(reference, dict) or set(reference) != {"step", "path"}
                or type(reference["step"]) is not int or not isinstance(reference["path"], list)):
            raise ValueError("Invalid result reference")
        number = reference["step"]
        if number not in step_results:
            raise ValueError("Result reference must name a completed earlier step")
        result = step_results[number]
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                pass
        for part in reference["path"]:
            if isinstance(result, dict) and isinstance(part, str) and part in result:
                result = result[part]
            elif isinstance(result, list) and type(part) is int and 0 <= part < len(result):
                result = result[part]
            else:
                raise ValueError("Result reference path does not exist")
        # Referenced results are copied as opaque data, never recursively interpreted.
        return deepcopy(result)

    if not isinstance(params, dict) or set(params) == {"$result"}:
        raise ValueError("Result references must be parameter values")
    return resolve(params)

TOOL_TIMEOUT_S = 180


def _requires_host(tool: str, parameters: dict) -> bool:
    """Live singleton state and deliberately persistent UI launches stay in host."""
    if tool == "cmd_control":
        command = str(parameters.get("command") or parameters.get("task") or "").lower()
        return bool(parameters.get("visible", True)) or any(word in command for word in ("notepad", "explorer", "start "))
    if tool == "file_controller":
        return parameters.get("action") in {"organize_desktop", "organize", "organize_folder", "undo_organize", "organize_undo"}
    return tool not in {
        "web_search", "weather_report", "system_status", "read_pdf", "pdf_qa",
        "read_document", "document_qa", "translate", "code_helper", "screen_process",
    }


def _call_tool(tool: str, parameters: dict, speak: Callable | None, *, registry=None,
               approved: bool = False, cancel_flag: threading.Event | None = None,
               timeout: float = TOOL_TIMEOUT_S) -> str:
    from agent.task_approval import QueueApprovalRequired, ToolCancelled, ToolTimedOut
    from local_mode.tools import ToolRegistry
    from local_mode.owned_process import run_owned, ProcessCancelled, ProcessTimedOut
    import time

    if cancel_flag is not None and cancel_flag.is_set():
        raise ToolCancelled("Queued tool was cancelled before execution.")
    registry = registry if registry is not None else ToolRegistry(speak=speak)
    if isinstance(getattr(registry, "schemas", None), dict) and tool not in registry.schemas:
        raise UnknownToolError("Unknown tool: " + tool)
    try:
        registry.validate(tool, parameters)
    except (QueueApprovalRequired, ToolCancelled, ToolTimedOut, TaskExecutionFailed):
        raise
    except OwnedError as error:
        raise ToolNotDispatched(OwnedText(error.template, *error.values)) from error
    except Exception as error:
        raise ToolNotDispatched(OwnedText(
            "The queued action could not be validated. Its handler was not run. Completed work is preserved.")) from error
    if callable(getattr(registry, "queued_ui_read", None)) and registry.queued_ui_read(tool):
        raise QueueUiReadRequired(tool, parameters)
    if callable(getattr(registry, "queued_ui_dispatch", None)) and registry.queued_ui_dispatch(tool):
        raise QueueUiDispatchRequired(tool, parameters)
    if registry.risky(tool, parameters) and not approved:
        raise QueueApprovalRequired(tool, parameters)
    if _requires_host(tool, parameters):
        # Host services retain their real queue/browser/session state and their own
        # cancellation contracts. Never claim that a parent call was hard-killed.
        result = registry.execute(tool, parameters, approved=approved)
        if cancel_flag is not None and cancel_flag.is_set():
            raise ToolCancelled("Queued host tool completed after cancellation; its result was not resumed.")
    else:
        events = []
        deadline = time.monotonic() + timeout

        def on_line(raw):
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise RuntimeError("Invalid queued worker event.")
            kind = event.get("event")
            if kind == "feedback":
                if speak and isinstance(event.get("text"), str):
                    speak(event["text"][:4000])
            elif kind == "text_request":
                if not relay_text:
                    raise RuntimeError("CLI worker cannot request a parent text provider relay.")
                from local_mode import model
                relay = getattr(model, "relay_text_request", None)
                if relay is None:
                    raise RuntimeError("Queued helper has no selected parent provider relay.")
                try:
                    value = relay(event.get("request"), cancel_flag=cancel_flag, deadline=deadline)
                except Exception:
                    if cancel_flag is not None and cancel_flag.is_set():
                        raise ToolCancelled("Queued helper was cancelled during its parent provider request.") from None
                    if time.monotonic() >= deadline:
                        raise ToolTimedOut("Queued helper timed out during its parent provider request.") from None
                    raise
                return json.dumps({"event": "text_response", "request_id": event.get("request_id"), "response": value}, ensure_ascii=False).encode("utf-8")
            elif kind in {"result", "failure"}:
                events.append(event)
                if len(events) > 1:
                    raise RuntimeError("Queued worker returned multiple terminal results.")
            else:
                raise RuntimeError("Unknown queued worker event.")
            return None

        from local_mode.model import current_text_provider
        relay_text = bool(current_text_provider())
        environment = dict(os.environ)
        for name in list(environment):
            if name == "PLAYWRIGHT_MCP_EXTENSION_TOKEN" or (relay_text and
                    (name.endswith("_API_KEY") or name == "JARVIS_GEMINI_API_KEYS")):
                environment.pop(name, None)
        environment["JARVIS_QUEUED_TOOL_WORKER"] = "1"
        environment["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + environment.get("PYTHONPATH", "")
        request = json.dumps({"tool": tool, "args": parameters, "approved": bool(approved), "relay_text": relay_text}, ensure_ascii=False).encode("utf-8")
        try:
            process = run_owned([sys.executable, "-X", "utf8", "-m", "agent.tool_worker"],
                                payload=request, timeout=timeout, cancel=cancel_flag,
                                cwd=BASE_DIR, env=environment, on_line=on_line)
        except ProcessCancelled as error:
            raise ToolCancelled(str(error)) from error
        except ProcessTimedOut as error:
            raise ToolTimedOut(f"Queued tool {tool} timed out; its owned process tree was stopped.") from error
        if cancel_flag is not None and cancel_flag.is_set():
            raise ToolCancelled("Queued tool was cancelled before its result could be resumed.")
        if time.monotonic() >= deadline:
            raise ToolTimedOut(f"Queued tool {tool} timed out; its owned process tree was stopped.")
        if process.returncode or len(events) != 1:
            raise RuntimeError("Queued tool worker exited without one complete result.")
        if events[0]["event"] == "failure":
            if events[0].get("type") == "UnknownToolError":
                raise UnknownToolError(events[0].get("error", "Unknown tool"))
            raise RuntimeError(events[0].get("error", "Queued tool worker failed."))
        result = events[0].get("result")
    if isinstance(result, dict) and result.get("requires_confirmation"):
        raise QueueApprovalRequired(tool, parameters)
    if isinstance(result, dict) and result.get("ok") is False:
        raise ToolReportedFailure(OwnedText("Queued task action failed"), result)
    return json.dumps(result, ensure_ascii=False)


def _call_tool_with_timeout(tool: str, parameters: dict, speak: Callable | None,
                            cancel_flag: threading.Event | None = None, *,
                            registry=None, approved: bool = False) -> str:
    """Run isolated handlers in an owned tree; parent services retain host state."""
    return _call_tool(tool, parameters, speak, registry=registry, approved=approved,
                      cancel_flag=cancel_flag, timeout=TOOL_TIMEOUT_S)


class AgentExecutor:
    def execute(
        self,
        goal: str,
        speak: Callable | None = None,
        cancel_flag: threading.Event | None = None,
        checkpoint: dict | None = None,
        registry=None,
    ) -> str:
        if cancel_flag and cancel_flag.is_set():
            raise ToolCancelled("Task cancelled")
        checkpoint = checkpoint if checkpoint is not None else {}
        # Approval resumption is distinct from retrying a failed outcome. The
        # latter must never re-enter a handler from a preserved checkpoint.
        if checkpoint.get("failed_step") is not None:
            raise TaskExecutionFailed(checkpoint.get("failure_message") or OwnedText("Queued task action failed"))
        if not checkpoint:
            checkpoint.update(plan=create_plan(goal, registry=registry), completed_steps=[],
                              step_results={}, next_step=0)
        completed_steps = checkpoint["completed_steps"]
        step_results = checkpoint["step_results"]
        plan_results = checkpoint.setdefault("plan_results", {})
        skipped_steps = checkpoint.setdefault("skipped_steps", [])
        steps = checkpoint["plan"].get("steps", [])
        if not isinstance(steps, list) or not steps:
            raise TaskExecutionFailed(OwnedText("The queued task has no valid plan. Completed work is preserved."))

        def fail(step, message, stage="execution"):
            checkpoint["failed_step"] = deepcopy(step)
            if stage in {"execution", "validation", "availability"} and "pending_parameters" in checkpoint:
                checkpoint["failed_step"]["parameters"] = deepcopy(checkpoint["pending_parameters"])
            checkpoint["failure_message"] = message
            checkpoint["failure_stage"] = stage
            return TaskExecutionFailed(message)

        for index, step in enumerate(steps):
            if index < checkpoint["next_step"]:
                continue
            if cancel_flag and cancel_flag.is_set():
                raise ToolCancelled("Task cancelled")
            number, tool = step["step"], step["tool"]
            if number in step_results or number in plan_results:
                raise fail(step, OwnedText("The queued task has no valid plan. Completed work is preserved."), "checkpoint")
            if "approved_step_result" in checkpoint:
                params = checkpoint["pending_parameters"]
            else:
                try:
                    params = _inject_context(step.get("parameters", {}), tool, plan_results, goal=goal)
                except ValueError as error:
                    raise fail(step, OwnedText("The queued task has no valid plan. Completed work is preserved."), "arguments") from error
            checkpoint["pending_parameters"] = deepcopy(params)
            try:
                if "approved_step_result" in checkpoint:
                    result = checkpoint.pop("approved_step_result")
                else:
                    result = _call_tool_with_timeout(tool, params, speak, cancel_flag, registry=registry)
            except (QueueApprovalRequired, ToolCancelled, ToolTimedOut):
                raise
            except UnknownToolError as error:
                message = OwnedText("Queued step {0} requires unavailable tool {1}. Completed work is preserved.", number, tool)
                if step.get("critical") is False:
                    skipped_steps.append({"step": deepcopy(step), "reason": message})
                    checkpoint["next_step"] = index + 1
                    continue
                raise fail(step, message, "availability") from error
            except TaskExecutionFailed as error:
                stage = "validation" if isinstance(error, ToolNotDispatched) else "execution"
                message = error.message
                if isinstance(error, ToolReportedFailure):
                    checkpoint["failed_step_result"] = deepcopy(error.result)
                    timed_out = error.result.get("timed_out") is True
                    stage = "timeout" if timed_out else "execution"
                    message = OwnedText(TIMED_OUT_STEP_MESSAGE if timed_out else FAILED_STEP_MESSAGE, number)
                raise fail(step, message, stage) from error
            except Exception as error:
                # An exception or ok:false result is not evidence of rollback.
                # Do not pay for recovery, invent code, or replay an action.
                raise fail(step, OwnedText(FAILED_STEP_MESSAGE, number)) from error
            step_results[number] = result
            plan_results[number] = result
            completed_steps.append(deepcopy(step))
            checkpoint["next_step"] = index + 1

        if cancel_flag and cancel_flag.is_set():
            raise ToolCancelled("Task cancelled")
        if skipped_steps:
            if not completed_steps:
                raise TaskExecutionFailed(OwnedText("No queued steps completed. The task is incomplete."))
            return OwnedText("Required queued steps completed; {0} optional steps were skipped.", len(skipped_steps))
        return self._summarize(goal, completed_steps, speak)

    def _summarize(self, goal: str, completed_steps: list, speak: Callable | None) -> str:
        fallback = f"All done, sir. Completed {len(completed_steps)} steps for: {goal[:60]}."
        try:
            from local_mode.model import ContentModel
            model     = ContentModel(model_name="gemini-3.6-flash")
            steps_str = "\n".join(f"- {s.get('description', '')}" for s in completed_steps)
            prompt    = (
                f'User goal: "{goal}"\n'
                f"Completed steps:\n{steps_str}\n\n"
                "Write a single natural sentence summarizing what was accomplished. "
                "Address the user as 'sir'. Be direct and positive."
            )
            response = model.generate_content(prompt)
            summary  = response.text.strip()
            if speak: speak(summary)
            return summary
        except (ToolCancelled, ToolTimedOut):
            raise
        except Exception:
            if speak: speak(fallback)
            return fallback
