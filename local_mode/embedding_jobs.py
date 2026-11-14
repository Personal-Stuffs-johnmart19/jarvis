"""Request-owned cancellation for embedding work; transports leave no waiter threads."""
from __future__ import annotations
import asyncio
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import threading
from uuid import uuid4

class Cancelled(RuntimeError):
    pass

_current = ContextVar("embedding_cancellation", default=None)
_accepted = ContextVar("embedding_accepted_generation", default=None)
_generation = 0
_lock = threading.Lock()
_active = {}
_finished = OrderedDict()
_early = OrderedDict()

def _remember(items, key):
    items[key] = None
    while len(items) > 128:
        items.popitem(last=False)

def event():
    return _current.get()

def check():
    token = event()
    if token is not None and token.is_set():
        raise Cancelled("Embedding operation cancelled")

@contextmanager
def operation(request_id=None):
    key = request_id or uuid4().hex
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise ValueError("Invalid embedding request ID")
    token = threading.Event()
    with _lock:
        if key in _active or key in _finished:
            raise ValueError("Embedding request ID already used")
        if key in _early:
            _early.pop(key)
            token.set()
        if _accepted.get() is not None and _accepted.get() != _generation:
            token.set()
        _active[key] = token
    context = _current.set(token)
    try:
        check()
        yield
    finally:
        _current.reset(context)
        with _lock:
            _active.pop(key, None)
            _remember(_finished, key)

def cancel(request_id):
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise ValueError("Invalid embedding request ID")
    with _lock:
        token = _active.get(request_id)
        if token is not None:
            token.set()
        elif request_id not in _finished:
            _remember(_early, request_id)
        return {"ok": True, "cancelled": token is not None, "finished": request_id in _finished}

def generation():
    with _lock:
        return _generation

@contextmanager
def accepted(value):
    context = _accepted.set(value)
    try:
        yield
    finally:
        _accepted.reset(context)

def cancel_all():
    global _generation
    with _lock:
        _generation += 1
        for token in _active.values():
            token.set()

def run_async(factory):
    token = event()
    check()
    async def run():
        task = asyncio.create_task(factory())
        try:
            while not task.done():
                if token is not None and token.is_set():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    raise Cancelled("Embedding operation cancelled")
                await asyncio.wait({task}, timeout=0.05)
            result = await task
            check()
            return result
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    return asyncio.run(run())
