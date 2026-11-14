"""Bounded, credential-free requests to the selected frontend model."""
from __future__ import annotations

from concurrent.futures import Future, TimeoutError
from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
import threading
import time
from uuid import uuid4

from local_mode.messages import OwnedValueError, OwnedRuntimeError

_provider = ContextVar("jarvis_text_provider", default=None)
MAX_TEXT_BYTES = 48000
MAX_WIRE_BYTES = 56000


@contextmanager
def using_text_provider(provider):
    token = _provider.set(provider)
    try:
        yield
    finally:
        _provider.reset(token)


def current_text_provider():
    return _provider.get()


def validate_text_request(request):
    fields = {"system", "prompt", "max_output_tokens", "temperature", "json_mode", "timeout_ms"}
    if not isinstance(request, dict) or set(request) - fields:
        raise OwnedValueError("Invalid selected-model helper request.")
    result = {"system": request.get("system", ""), "prompt": request.get("prompt", ""),
              "max_output_tokens": request.get("max_output_tokens", 2048),
              "timeout_ms": request.get("timeout_ms", 120000), "json_mode": request.get("json_mode", False)}
    if any(not isinstance(result[key], str) for key in ("system", "prompt")) or not result["prompt"].strip():
        raise OwnedValueError("A text helper requires a non-empty text prompt.")
    if len((result["system"] + result["prompt"]).encode("utf-8")) > MAX_TEXT_BYTES:
        raise OwnedValueError("The text helper input is too large; read a smaller file range.")
    for key, minimum, maximum in (("max_output_tokens", 1, 4096), ("timeout_ms", 1000, 120000)):
        if type(result[key]) is not int or not minimum <= result[key] <= maximum:
            raise OwnedValueError("Invalid text helper generation limits.")
    if type(result["json_mode"]) is not bool:
        raise OwnedValueError("Invalid text helper JSON setting.")
    if "temperature" in request:
        value = request["temperature"]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2:
            raise OwnedValueError("Invalid text helper temperature.")
        result["temperature"] = value
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_WIRE_BYTES:
        raise OwnedValueError("The text helper input exceeds the desktop message limit.")
    return result


class SharedTextProvider:
    """One short-lived generation; no tools, conversation replay, or cloud fallback."""
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.RLock()
        self.identity = None
        self.pending = None
        self.revision = 0

    def attach(self, owner, session, route):
        if any(not isinstance(value, str) or len(value) > 128 for value in (owner, session, route)):
            raise OwnedValueError("Invalid selected-model helper identity.")
        identity = (owner, session, route) if owner and session and route else None
        with self.lock:
            if self.identity == identity:
                return
            self.revision += 1
            self.identity = identity
            pending = self.pending
            self.pending = None
        self._cancel(pending, "The selected model or chat changed during the text helper request.")

    def _cancel(self, pending, reason):
        if pending:
            with self.lock:
                if not pending[2].done():
                    pending[2].set_exception(OwnedRuntimeError(reason))
            self.emit("helper.text.cancel", {"id": pending[0]})

    def cancel(self):
        with self.lock:
            self.revision += 1
            pending = self.pending
            self.pending = None
        self._cancel(pending, "The text helper request was stopped.")

    def detach(self):
        self.attach("", "", "")

    def binding(self):
        with self.lock:
            identity = self.identity
            revision = self.revision

        def generate(request, *, cancel_flag=None, deadline=None):
            request = validate_text_request(request)
            if cancel_flag is not None and cancel_flag.is_set():
                raise OwnedRuntimeError("The text helper request was stopped.")
            if identity is None:
                raise OwnedRuntimeError("Open a Jarvis chat with a configured model before using text helpers.")
            expires = min(time.monotonic() + request["timeout_ms"] / 1000, deadline if deadline is not None else math.inf)
            if expires <= time.monotonic():
                raise OwnedRuntimeError("The selected-model text helper timed out.")
            request_id, future = uuid4().hex, Future()
            with self.lock:
                if self.identity != identity:
                    raise OwnedRuntimeError("The selected model or chat changed during the text helper request.")
                if self.revision != revision:
                    raise OwnedRuntimeError("The text helper request was stopped.")
                if self.pending:
                    raise OwnedRuntimeError("A selected-model text helper is already running.")
                pending = (request_id, identity, future)
                self.pending = pending
            try:
                self.emit("helper.text.request", {**request, "timeout_ms": max(1, int((expires - time.monotonic()) * 1000)), "id": request_id,
                    "owner": identity[0], "session": identity[1], "route": identity[2]})
                while time.monotonic() < expires:
                    if cancel_flag is not None and cancel_flag.is_set():
                        raise OwnedRuntimeError("The text helper request was stopped.")
                    try:
                        result = future.result(timeout=min(0.1, max(0.001, expires - time.monotonic())))
                        with self.lock:
                            if self.identity != identity:
                                raise OwnedRuntimeError("The selected model or chat changed during the text helper request.")
                            if self.revision != revision:
                                raise OwnedRuntimeError("The text helper request was stopped.")
                            if self.pending is not pending or (cancel_flag is not None and cancel_flag.is_set()):
                                raise OwnedRuntimeError("The text helper request was stopped.")
                            if time.monotonic() >= expires:
                                raise OwnedRuntimeError("The selected-model text helper timed out.")
                        return result
                    except TimeoutError:
                        pass
                raise OwnedRuntimeError("The selected-model text helper timed out.")
            finally:
                with self.lock:
                    if self.pending is pending:
                        self.pending = None
                self.emit("helper.text.cancel", {"id": request_id})
        return generate

    def complete(self, params):
        fields = {"id", "owner", "session", "route", "text", "error"}
        if not isinstance(params, dict) or set(params) - fields:
            raise OwnedValueError("Invalid text helper response.")
        text, error = params.get("text", ""), params.get("error")
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            raise OwnedValueError("Invalid text helper response.")
        if error is not None and (not isinstance(error, str) or len(error) > 1000):
            raise OwnedValueError("Invalid text helper response.")
        identity = tuple(params.get(key) for key in ("owner", "session", "route"))
        with self.lock:
            pending = self.pending
            if not pending or pending[0] != params.get("id") or pending[1] != identity or self.identity != identity or pending[2].done():
                raise OwnedValueError("Expired or already completed text helper response.")
            if error:
                pending[2].set_exception(OwnedRuntimeError("The selected-model text helper failed: {0}", error))
            elif not text.strip():
                pending[2].set_exception(OwnedRuntimeError("The selected-model text helper returned no text."))
            else:
                pending[2].set_result({"text": text})
        return {"ok": True}
