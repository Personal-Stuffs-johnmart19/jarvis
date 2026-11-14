"""Атомарные JSON-файлы и защита общего кэша от конкурирующих клиентов."""
from __future__ import annotations
from functools import wraps
import json
import os
import time
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

LOCK = RLock()


def synchronized(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        with LOCK:
            return function(*args, **kwargs)
    return call


@synchronized
def write_json(path: Path, values: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(values, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Windows иногда кратко удерживает файл антивирусом/индексатором.
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as error:
                if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.01 * 2 ** attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
