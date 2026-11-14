"""Совместимый запуск автоматической установки Windows из Python."""
from pathlib import Path
import subprocess
import sys
if __name__ == "__main__":
    script = Path(__file__).resolve().parents[2] / "setup_local.ps1"
    raise SystemExit(subprocess.call(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *sys.argv[1:]]))
