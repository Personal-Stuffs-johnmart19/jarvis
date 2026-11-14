"""RAG for real terminal task streams retained in canonical saved SDK results.

A task ID is the persisted toolCallId, not an invented native process/task ID.
Interactive/private PTY scrollback is deliberately outside this importer.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from local_mode import rag_sources

_TERMINAL_TOOLS = {"bash_run", "utility_run", "run_code"}


def _terminal_snapshot(session_id: str, task_id: str, stream: str = "stdout", *, expected_path: str | None = None) -> tuple:
    """Index one exact saved stdout/stderr stream by session and tool-call ID."""
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise ValueError("Invalid saved terminal session ID")
    if (not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 255
            or any(ord(c) < 32 for c in task_id)):
        raise ValueError("Invalid saved terminal task/tool-call ID")
    if stream not in {"stdout", "stderr"}:
        raise ValueError("Choose a saved stdout or stderr stream")
    root = rag_sources._conversations_root().resolve(strict=True)
    path = rag_sources._safe_file(root / f"session-{session_id}.json", root)
    if expected_path is not None and path != Path(expected_path):
        from local_mode.messages import OwnedValueError
        raise OwnedValueError("RAG source location changed; import it again.")
    messages = json.loads(rag_sources._read(path, root))
    if not isinstance(messages, list):
        raise ValueError("Saved conversation must contain a message array")
    matches = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        parts = message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("toolCallId") == task_id:
                matches.append((message, part))
    if len(matches) != 1:
        raise ValueError("Saved task ID must identify exactly one assistant tool result")
    message, part = matches[0]
    if part.get("state") != "output-available":
        raise ValueError("Terminal task must have a completed saved result")
    kind = part.get("type")
    if kind == "dynamic-tool":
        tool_name = part.get("toolName")
    elif isinstance(kind, str) and kind.startswith("tool-"):
        tool_name = kind[5:]
    else:
        raise ValueError("Saved task is not a tool result")
    if tool_name == "jarvis_action":
        arguments = part.get("input")
        tool_name = arguments.get("name") if isinstance(arguments, dict) else None
    if not isinstance(tool_name, str) or tool_name not in _TERMINAL_TOOLS:
        raise ValueError("Only saved bash_run, utility_run and run_code outputs can be indexed")
    approval = part.get("approval")
    if isinstance(approval, dict) and approval.get("approved") is False:
        raise ValueError("Denied terminal tasks cannot be indexed")
    output = part.get("output")
    if (not isinstance(output, dict) or not isinstance(output.get("stdout"), str)
            or not isinstance(output.get("stderr"), str) or "exit_code" not in output
            or (output["exit_code"] is not None and type(output["exit_code"]) is not int)):
        raise ValueError("Saved task has no structured terminal execution result")
    turn_id = message.get("id")
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 255:
        raise ValueError("Saved terminal result must retain its message ID")
    source = {
        "source_kind": "terminal", "session_id": session_id, "turn_id": turn_id,
        "task_id": task_id, "tool_call_id": task_id, "tool_name": tool_name,
        "stream": stream, "path": str(path), "coordinate_space": "saved_terminal_stream",
        "source_truncated": output.get("truncated") is True,
    }
    # Each stream retains its original bytes-as-decoded-text coordinates. Never
    # concatenate streams or invent a stdout/stderr ordering that was not saved.
    return output[stream], source, f"Terminal {task_id} / {stream}"


def ingest_terminal(session_id: str, task_id: str, stream: str = "stdout") -> dict:
    return rag_sources._snapshot(*_terminal_snapshot(session_id, task_id, stream))
