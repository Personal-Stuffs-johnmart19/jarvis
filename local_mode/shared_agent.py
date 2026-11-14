"""Desktop agent transport; models and agent loop live in the shared Workbench runtime."""

from concurrent.futures import Future, TimeoutError
import json
import threading
import time
from uuid import uuid4


class SharedAgentCancelled(Exception):
    """A user Stop is a normal outcome, not a model connection failure."""


class SharedAgent:
    def __init__(self, emit):
        self.emit = emit
        self.lock = threading.RLock()
        self.model = ""
        self.owner = ""
        self.session = ""
        self.workspace = None
        self.attached = False
        self.pending = None
        self.receipts = {}
        self.cancel_revision = 0
        self._owned_calls = {}

    def _cancel_owned_calls(self):
        for cancel in self._owned_calls.values():
            cancel.set()

    def cancel_current(self):
        with self.lock:
            self.cancel_revision += 1
            self._cancel_owned_calls()
            self.receipts.clear()
            if self.pending and not self.pending[1].done():
                self.pending[1].set_exception(SharedAgentCancelled())

    def attach(self, model="", session="", owner="", workspace=None):
        if not isinstance(model, str) or len(model) > 512:
            raise ValueError("Invalid model identifier")
        if any(not isinstance(v, str) or len(v) > 128 for v in (session, owner)):
            raise ValueError("Invalid session owner")
        if workspace is not None and (not isinstance(workspace, str) or not workspace or len(workspace.encode()) > 8192):
            from local_mode.messages import OwnedValueError
            raise OwnedValueError("Queued task action failed")
        with self.lock:
            if self.owner != owner or self.session != session:
                self._cancel_owned_calls()
                self.receipts.clear()
            self.owner, self.session = owner, session
            self.workspace = workspace
            self.attached = True
            self.model = model

    def detach(self, owner=None):
        with self.lock:
            if owner is not None and owner != self.owner:
                return False
            self.attached = False
            self._cancel_owned_calls()
            self.receipts.clear()
            if self.pending and not self.pending[1].done():
                self.pending[1].set_exception(ValueError("Shared agent disconnected"))

            return True

    def run(self, text, stop):
        request_id, future = uuid4().hex, Future()
        with self.lock:
            if self.pending:
                raise ValueError("Shared agent is busy")
            self.pending = (request_id, future)
            revision = self.cancel_revision
        try:
            self.emit("agent.request", {"id": request_id, "text": text})
            deadline = time.monotonic() + 900
            while not stop.is_set() and time.monotonic() < deadline:
                try:
                    result = future.result(timeout=0.2)
                    with self.lock:
                        if self.cancel_revision != revision:
                            raise SharedAgentCancelled()
                    return result
                except TimeoutError:
                    pass
            raise ValueError("Shared agent request timed out or was stopped")
        finally:
            with self.lock:
                if self.pending and self.pending[0] == request_id:
                    self.pending = None
                if self.cancel_revision == revision:
                    self.receipts.clear()
            self.emit("agent.cancel", {"id": request_id})

    def complete(self, request_id, text, error=None):
        if not isinstance(text, str) or len(text) > 48000:
            raise ValueError("Invalid shared agent response")
        if not text.strip() and not error:
            error = "Agent finished without a text response; review the tool results"
        with self.lock:
            if not self.pending or self.pending[0] != request_id:
                raise ValueError("Expired agent request")
            future = self.pending[1]
            if future.done():
                raise ValueError("Agent request already completed")
            if error:
                future.set_exception(ValueError(str(error)[:1000]))
            else:
                future.set_result({"ok": True, "reply": text})

    def prepare(self, registry, name, args):
        registry.validate(name, args)
        with self.lock:
            now = time.monotonic()
            self.receipts = {k: v for k, v in self.receipts.items() if v[0] > now}
            if len(self.receipts) >= 64:
                raise ValueError("Too many pending actions")
            token = uuid4().hex
            self.receipts[token] = (
                now + 900,
                name,
                json.loads(json.dumps(args)),
                registry.risky(name, args),
            )
            return {"token": token, "requires_approval": self.receipts[token][3]}

    def execute(self, registry, token):
        with self.lock:
            receipt = self.receipts.pop(token, None)
            if not receipt or receipt[0] < time.monotonic():
                raise ValueError("Expired or consumed action receipt")
            # Receipt stays in the trusted host closure, never in model arguments.
            if not receipt[3] and registry.risky(receipt[1], receipt[2]):
                raise ValueError("Action risk changed; request approval again")
            cancel = threading.Event()
            self._owned_calls[token] = cancel
        try:
            from local_mode.owned_process import owned_action_cancel
            registry.source = "local"
            with owned_action_cancel(cancel):
                result = registry.execute(receipt[1], receipt[2], approved=True)
            with self.lock:
                self._owned_calls.pop(token, None)
                if cancel.is_set() and not (isinstance(result, dict)
                        and result.get("ok") is False and result.get("cancelled") is True):
                    return {"ok": False, "cancelled": True, "status": "cancelled", "result": result}
                return result
        finally:
            with self.lock:
                self._owned_calls.pop(token, None)
