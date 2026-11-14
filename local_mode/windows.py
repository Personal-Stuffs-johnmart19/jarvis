"""Окна Windows: адресный вызов Win32 и обязательная проверка результата."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import sys
import time


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    maximized: bool


class WindowController:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Управление окнами поддерживается только в Windows.")
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.GetForegroundWindow.restype = wintypes.HWND
        self.api.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.api.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.api.IsWindow.argtypes = [wintypes.HWND]
        self.api.IsWindowVisible.argtypes = [wintypes.HWND]
        self.api.IsZoomed.argtypes = [wintypes.HWND]
        self.api.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.api.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]

    def describe(self, hwnd: int) -> Window | None:
        if not hwnd or not self.api.IsWindow(hwnd) or not self.api.IsWindowVisible(hwnd):
            return None
        pid = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Наш GUI и терминалы не становятся неявной целью команды пользователя.
        if pid.value == os.getpid():
            return None
        klass = ctypes.create_unicode_buffer(256)
        self.api.GetClassNameW(hwnd, klass, 256)
        if klass.value in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "Progman", "WorkerW"}:
            return None
        size = self.api.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(size + 1)
        self.api.GetWindowTextW(hwnd, title, size + 1)
        if not title.value.strip():
            return None
        return Window(hwnd=int(hwnd), title=title.value, maximized=bool(self.api.IsZoomed(hwnd)))

    def foreground(self) -> Window | None:
        return self.describe(self.api.GetForegroundWindow())

    def list_windows(self) -> list[Window]:
        result: list[Window] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd: int, extra: int) -> bool:
            item = self.describe(hwnd)
            if item:
                result.append(item)
            return True

        self.api.EnumWindows(callback, 0)
        return result

    def maximize(self, title: str = "", target: Window | None = None) -> dict[str, object]:
        if title.strip():
            windows = self.list_windows()
            exact = [window for window in windows if window.title.casefold() == title.strip().casefold()]
            matches = exact or [window for window in windows if title.strip().casefold() in window.title.casefold()]
            if len(matches) != 1:
                raise ValueError(f"Найдено окон: {len(matches)}. Уточните заголовок окна.")
            target = matches[0]
        elif target is None:
            target = self.foreground()
        if target is None:
            raise ValueError("Активное окно не выбрано. Назовите приложение или выберите окно перед голосовой командой.")
        current = self.describe(target.hwnd)
        if current is None or current.title != target.title:
            raise RuntimeError("Целевое окно изменилось или закрыто. Выберите его снова.")
        # SW_MAXIMIZE=3: только изменяем состояние указанного HWND, без Alt+Tab.
        self.api.ShowWindowAsync(current.hwnd, 3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.api.IsZoomed(current.hwnd):
                return {"ok": True, "action": "maximize_window", "title": current.title,
                        "hwnd": current.hwnd, "verified": True, "changed": not current.maximized}
            time.sleep(0.05)
        raise RuntimeError("Windows не подтвердила развёрнутое состояние окна.")
