"""Запуск без консоли с журналом и понятной диагностикой ошибки старта."""
from __future__ import annotations
import contextlib
import sys
import subprocess
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parent


def main() -> int:
    log = ROOT / 'data' / 'logs' / 'startup.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('a', encoding='utf-8', buffering=1) as output:
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            try:
                from local_mode.instance import is_running
                if is_running(ROOT):
                    return 0
                from local_mode.setup_state import check_environment
                if check_environment(ROOT):
                    # Новый процесс подхватит .pth, DLL и новые пакеты после pip.
                    subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT / 'launch_local.py'), *sys.argv[1:]],
                                     cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
                    return 0
                arguments = sys.argv[1:]
                if not arguments:
                    from local_mode.desktop_launch import launch
                    code = launch(ROOT)
                else:
                    from local_mode.cli import main as run
                    code = run(arguments)
            except Exception:
                traceback.print_exc()
                code = 1
    if code:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f'Не удалось запустить Jarvis.\nДиагностика: {log}\nНативный интерфейс: data/logs/desktop-native.log\nВосстановление: data/logs/repair.log\nПовторите запуск или отправьте журналы ошибки.', 'Jarvis', 0x10)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
