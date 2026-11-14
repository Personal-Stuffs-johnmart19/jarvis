"""Private broker bootstrap for the embedded Jarvis workspace."""

from __future__ import annotations
import atexit
import threading
from local_mode.companion_broker import GeminiBroker

_LOCK = threading.RLock()
_BROKER: GeminiBroker | None = None


def workbench_environment() -> dict[str, str]:
    global _BROKER
    with _LOCK:
        if _BROKER is None:
            _BROKER = GeminiBroker()
        return _BROKER.environment()


def close() -> None:
    global _BROKER
    with _LOCK:
        if _BROKER is not None:
            _BROKER.close()
            _BROKER = None


atexit.register(close)
