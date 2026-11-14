"""Bounded owned subprocesses: cancellation closes the entire child process tree."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

import ctypes
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable


_owned_cancel: ContextVar[threading.Event | None] = ContextVar("owned_action_cancel", default=None)


@contextmanager
def owned_action_cancel(cancel: threading.Event):
    """Private synchronous action scope; explicit run_owned cancellation wins."""
    token = _owned_cancel.set(cancel)
    try:
        yield
    finally:
        _owned_cancel.reset(token)


class ProcessCancelled(RuntimeError):
    pass


class ProcessTimedOut(TimeoutError):
    pass


class ProcessCleanupError(RuntimeError):
    """The owned tree could not be confirmed stopped; never report cancellation success."""


class _JobAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in
                ("total_user", "total_kernel", "period_user", "period_kernel")] + [
                    (name, wintypes.DWORD) for name in
                    ("page_faults", "total_processes", "active_processes", "terminated_processes")]


def check_action_cancelled():
    """Check the cancellation owned by the current synchronous tool action."""
    cancel = _owned_cancel.get()
    if cancel is not None and cancel.is_set():
        raise ProcessCancelled("Queued tool was cancelled.")


class _WindowsJob:
    """Match the native ProcessJob KILL_ON_JOB_CLOSE ownership contract."""
    def __init__(self, process):
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                  wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.QueryInformationJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        self.api, self.handle = api, api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not api.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def close(self, *, deadline=None):
        if self.handle:
            try:
                # KILL_ON_JOB_CLOSE initiates asynchronous termination. Retain
                # the owned handle until the kernel confirms no active members,
                # including descendants; waiting only for Popen's root is not enough.
                if not self.api.TerminateJobObject(self.handle, 1):
                    raise ProcessCleanupError("Owned process tree termination could not be requested.")
                if deadline is None:
                    deadline = time.monotonic() + 3
                while True:
                    info = _JobAccounting()
                    if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                        raise ProcessCleanupError("Owned process tree cleanup could not be queried.")
                    if info.active_processes == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise ProcessCleanupError("Owned process tree cleanup could not be confirmed.")
                    time.sleep(0.01)
            finally:
                self.api.CloseHandle(self.handle)
                self.handle = None


def _finish_cleanup(process, readers, close_tree, remaining, prior_error, *, windows):
    """Attempt every owned-resource cleanup, preserving prior uncertainty."""
    error = prior_error if isinstance(prior_error, ProcessCleanupError) else None

    def attempt(operation):
        nonlocal error
        try:
            operation()
        except Exception as failure:
            if error is None:
                error = failure if isinstance(failure, ProcessCleanupError) else ProcessCleanupError(
                    "Owned process tree cleanup could not be confirmed.")
                if error is not failure:
                    error.__cause__ = failure

    try:
        attempt(close_tree)
    finally:
        try:
            attempt(lambda: process.kill() if process.poll() is None else None)
            attempt(lambda: process.wait(timeout=remaining()))
        finally:
            try:
                # wait() caches returncode but retains CPython's owned process
                # handle; an exception traceback must not prolong its lifetime.
                if windows:
                    attempt(lambda: process._handle.Close())
            finally:
                try:
                    attempt(lambda: process.stdin.close() if process.stdin and not process.stdin.closed else None)
                finally:
                    for reader in readers:
                        attempt(lambda reader=reader: reader.join(timeout=min(2, remaining())))
    if error is not None:
        raise error


def run_owned(command: list[str], *, payload: bytes, timeout: float,
              cancel: threading.Event | None = None, cwd=None, env=None,
              on_line: Callable[[bytes], bytes | None] | None = None,
              max_output: int = 512000) -> subprocess.CompletedProcess:
    """Worker must await stdin before executing, so Job assignment wins the spawn race.

    Worker stdout is bounded newline-delimited protocol. Diagnostic stderr stays
    bounded and private. The Job/process group is closed even after successful
    root exit so no descendants can outlive their queued tool call.
    """
    if cancel is None:
        cancel = _owned_cancel.get()
    if timeout <= 0 or max_output < 1 or len(payload) > 64000:
        raise ValueError("Invalid owned worker bounds.")
    if cancel is not None and cancel.is_set():
        raise ProcessCancelled("Queued tool was cancelled before launch.")
    options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
               "cwd": cwd, "env": env}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(command, **options)
    job = None
    readers = []
    messages = queue.Queue(maxsize=128)
    overflow = threading.Event()
    stdout, stderr = bytearray(), bytearray()
    lock = threading.Lock()
    total = 0
    deadline = time.monotonic() + timeout
    cleanup_deadline = None

    def remaining_cleanup():
        return max(0, cleanup_deadline - time.monotonic())

    def close_tree():
        nonlocal job, cleanup_deadline
        if cleanup_deadline is None:
            cleanup_deadline = time.monotonic() + 3
        if job is not None:
            job.close(deadline=cleanup_deadline)
            job = None
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def read(stream, target, protocol):
        nonlocal total
        try:
            while True:
                part = stream.readline(max_output + 1) if protocol else stream.read(4096)
                if not part:
                    break
                with lock:
                    total += len(part)
                    if total > max_output:
                        overflow.set()
                        break
                    target.extend(part)
                if protocol and on_line is not None:
                    try:
                        messages.put(part, timeout=0.1)
                    except queue.Full:
                        overflow.set()
                        break
        finally:
            stream.close()

    def check_stop():
        if cancel is not None and cancel.is_set():
            raise ProcessCancelled("Queued tool was cancelled.")
        if time.monotonic() >= deadline:
            raise ProcessTimedOut("Queued tool reached its execution timeout.")

    def drain():
        while True:
            try:
                line = messages.get_nowait()
            except queue.Empty:
                return
            if on_line is not None:
                response = on_line(line)
                check_stop()
                if response is not None:
                    if len(response) > max_output:
                        raise RuntimeError("Queued tool relay response exceeds the size limit.")
                    process.stdin.write(response + b"\n")
                    process.stdin.flush()

    try:
        if os.name == "nt":
            job = _WindowsJob(process)
        for stream, target, protocol in [(process.stdout, stdout, True), (process.stderr, stderr, False)]:
            reader = threading.Thread(target=read, args=(stream, target, protocol), daemon=True)
            readers.append(reader)
            reader.start()
        # The child reads this only after tree ownership has been established.
        process.stdin.write(payload + b"\n")
        process.stdin.flush()
        while True:
            check_stop()
            if overflow.is_set():
                raise RuntimeError("Queued tool worker output exceeds the size limit.")
            drain()
            if process.poll() is not None:
                break
            if cancel is not None:
                cancel.wait(0.03)
            else:
                time.sleep(0.03)
        close_tree()
        for reader in readers:
            reader.join(timeout=min(2, remaining_cleanup()))
        if any(reader.is_alive() for reader in readers):
            raise RuntimeError("Queued tool worker output did not close.")
        if overflow.is_set():
            raise RuntimeError("Queued tool worker output exceeds the size limit.")
        drain()
        check_stop()
        return subprocess.CompletedProcess(command, process.returncode, bytes(stdout), bytes(stderr))
    finally:
        _finish_cleanup(process, readers, close_tree, remaining_cleanup, sys.exception(), windows=os.name == "nt")
