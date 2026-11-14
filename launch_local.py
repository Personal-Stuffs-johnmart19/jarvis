"""Launch without a console, retaining diagnostics for startup failures."""
from __future__ import annotations
import contextlib
import sys
import subprocess
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parent


def _show_startup_error(log: Path, detail: str = "") -> None:
    text = (f"Could not start Jarvis.\nStartup log: {log}\n"
            "Native host log: data/logs/desktop-native.log\n"
            "Repair log: data/logs/repair.log\n"
            "Review the logs or run INSTALL.cmd to repair the environment.")
    if detail:
        text += "\n\n" + detail
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Jarvis startup", 0x10)
    except Exception:
        if sys.stderr is not None:
            print(text, file=sys.stderr)


def main() -> int:
    log = ROOT / "data/logs/startup.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8", buffering=1) as output:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                try:
                    from local_mode.instance import is_running
                    if is_running(ROOT):
                        return 0
                    from local_mode.setup_state import check_environment
                    if check_environment(ROOT):
                        # Reload .pth files, native libraries and installed packages.
                        subprocess.Popen([sys.executable, "-X", "utf8", str(ROOT / "launch_local.py"), *sys.argv[1:]],
                                         cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
                        return 0
                    arguments = sys.argv[1:]
                    if not arguments:
                        from local_mode.desktop_launch import launch
                        code = launch(ROOT)
                    else:
                        from local_mode.cli import main as run
                        code = run(arguments)
                except SystemExit as error:
                    code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
                except Exception:
                    traceback.print_exc()
                    code = 1
    except Exception as error:
        _show_startup_error(log, f"Startup diagnostics could not be written: {error}")
        return 1
    if code:
        _show_startup_error(log)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
