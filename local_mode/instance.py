"""Именованный mutex Windows: повторный запуск не создаёт второй микрофон."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _name(root: Path) -> str:
    return "Local\\Jarvis-" + hashlib.sha256(str(root).casefold().encode()).hexdigest()[:20]


def is_running(root: Path = ROOT) -> bool:
    """Проверяем до импорта SDK, чтобы не восстанавливать занятые пакеты."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    api.OpenMutexW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenMutexW(0x00100000, False, _name(root))
    if handle:
        api.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == 5



class SingleInstance:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.handle = None
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]

    def __enter__(self) -> bool:
        name = _name(self.root)
        self.handle = self.api.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.get_last_error() != 183

    def __exit__(self, *args) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
