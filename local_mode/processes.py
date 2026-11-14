"""Служебные команды Windows выполняются без всплывающей консоли."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import Any


def run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """Сохраняем вывод и код возврата; явный запуск терминала через Popen не меняем."""
    if os.name == "nt":
        # PowerShell 5.1 иначе выдаёт русские строки в OEM-кодировке даже при
        # encoding="utf-8" у Python. Меняем только явно запрошенный UTF-8 вывод.
        command = args[0] if args else kwargs.get("args")
        encoding = str(kwargs.get("encoding", "")).lower().replace("-", "")
        if isinstance(command, (list, tuple)) and command and encoding == "utf8" and Path(str(command[0])).name.lower() in {"powershell", "powershell.exe"}:
            command = list(command)
            for index, option in enumerate(command[:-1]):
                if str(option).lower() == "-command" and isinstance(command[index + 1], str):
                    command[index + 1] = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); " + command[index + 1]
                    if args:
                        args = (command, *args[1:])
                    else:
                        kwargs["args"] = command
                    break
        flags = kwargs.get("creationflags", 0)
        # Явно запрошенная отдельная консоль имеет приоритет.
        if not flags & subprocess.CREATE_NEW_CONSOLE:
            kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
    return subprocess.run(*args, **kwargs)
