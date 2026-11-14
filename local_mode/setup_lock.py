"""Межпроцессная блокировка восстановления: второй запуск ждёт первый."""
from __future__ import annotations
from contextlib import contextmanager
from collections.abc import Iterator
import errno
from pathlib import Path
from threading import RLock
import time

_THREAD_LOCK = RLock()


@contextmanager
def repair_lock(root: Path) -> Iterator[None]:
    import msvcrt
    path = root / "data/repair.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 1800
        while True:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise RuntimeError("Другое восстановление Jarvis ещё выполняется. См. data/logs/repair.log.") from None
                time.sleep(0.2)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
