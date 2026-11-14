"""One queued handler in an owned process; protocol starts only after stdin grant."""
from __future__ import annotations

from contextlib import redirect_stdout, nullcontext
import json
import sys
from uuid import uuid4

_PROTOCOL = sys.stdout
MAX_INPUT = 64000
MAX_RESPONSE = 512000


def emit(value):
    _PROTOCOL.write(json.dumps(value, ensure_ascii=False) + "\n")
    _PROTOCOL.flush()


def request_text(request: dict) -> dict:
    """Relay a helper prompt to its captured parent provider; never open an SDK here."""
    identifier = uuid4().hex
    emit({"event": "text_request", "request_id": identifier, "request": request})
    raw = sys.stdin.buffer.readline(MAX_RESPONSE + 1)
    if not raw or len(raw) > MAX_RESPONSE:
        raise RuntimeError("Queued text provider relay closed or exceeded its response bound.")
    response = json.loads(raw)
    if response.get("event") != "text_response" or response.get("request_id") != identifier:
        raise RuntimeError("Queued text provider response does not match the request.")
    if response.get("error"):
        raise RuntimeError(str(response["error"])[:1500])
    value = response.get("response")
    if not isinstance(value, dict):
        raise RuntimeError("Invalid queued text provider response.")
    return value


def main():
    try:
        raw = sys.stdin.buffer.readline(MAX_INPUT + 1)
        if not raw or len(raw) > MAX_INPUT:
            raise ValueError("Invalid queued tool request size.")
        request = json.loads(raw)
        if (not isinstance(request, dict) or not isinstance(request.get("tool"), str)
                or not isinstance(request.get("args"), dict) or type(request.get("approved")) is not bool
                or type(request.get("relay_text", False)) is not bool):
            raise ValueError("Invalid queued tool request.")
        # Imports/handler work start after the parent has assigned our Job.
        with redirect_stdout(sys.stderr):
            from local_mode.tools import ToolRegistry
            registry = ToolRegistry(speak=lambda text: emit({"event": "feedback", "text": str(text)[:4000]}))
            binding = nullcontext()
            if request.get("relay_text", False):
                from local_mode.model import using_text_provider
                binding = using_text_provider(lambda request, **_: request_text(request))
            # An explicit GUI binding never falls back after a relay failure.
            # CLI calls without a binding keep their configured SDK in this worker.
            with binding:
                value = registry.execute(request["tool"], request["args"], approved=request["approved"])
        emit({"event": "result", "result": value})
    except Exception as error:
        from local_mode.gemini import safe_error
        emit({"event": "failure", "error": safe_error(error), "type": type(error).__name__})


if __name__ == "__main__":
    main()
