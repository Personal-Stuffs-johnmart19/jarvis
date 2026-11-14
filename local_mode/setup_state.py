"""Быстрая диагностика: идея dependency_cache из RuinedFooocus, только stdlib."""
from __future__ import annotations
import hashlib
import ctypes
from importlib import metadata
import json
import os
import subprocess
from pathlib import Path
import sys
import sysconfig
from local_mode.storage import write_json

ROOT = Path(__file__).resolve().parent.parent
MODEL_FILES = (
    "models/silero/v5_5_ru.pt",
    "models/vosk-model-small-ru-0.22/am/final.mdl",
    "models/whisper-small/model.bin", "models/whisper-small/config.json",
    "models/whisper-small/tokenizer.json", "models/whisper-small/vocabulary.txt",
    "models/piper/ru_RU-denis-medium.onnx", "models/piper/ru_RU-denis-medium.onnx.json",
)


def fingerprint(root: Path = ROOT) -> str:
    """Без pip/сети/импорта torch; большие модели проверяются по stat на обычном старте."""
    # Установщик использует python.exe, ярлык pythonw.exe: это одно окружение.
    identity = (str(Path(sys.executable).parent), sys.prefix, sys.version)
    digest = hashlib.sha256(repr(identity).encode())
    for name in ("requirements/full.lock.txt", "setup_local.ps1"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes())
    # Vulkan необязателен: отсутствие GPU/модели не должно блокировать Silero CPU.
    manifest = root / "local_mode/audio_cpp_assets.json"
    digest.update(manifest.read_bytes() if manifest.is_file() else b"no-audio-cpp-manifest")
    for name in ("models/audio_cpp/supertonic-3-orig.gguf", "models/audio_cpp/runtime/audiocpp_server.exe"):
        optional = root / name
        stat = optional.stat() if optional.exists() else None
        digest.update(repr((name, stat.st_size, stat.st_mtime_ns) if stat else (name, "missing")).encode())
    for directory in sorted({sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}):
        for entry in sorted(Path(directory).glob("*.dist-info/METADATA")):
            stat = entry.stat()
            digest.update(repr((str(entry), stat.st_size, stat.st_mtime_ns)).encode())
    for name in MODEL_FILES:
        stat = (root / name).stat()
        if stat.st_size == 0:
            raise ValueError("Пустой файл модели: " + name)
        digest.update(repr((name, stat.st_size, stat.st_mtime_ns)).encode())
    return digest.hexdigest()


def is_ready(root: Path = ROOT) -> bool:
    try:
        saved = json.loads((root / "data/setup-state.json").read_text(encoding="utf-8"))
        return isinstance(saved, dict) and saved.get("fingerprint") == fingerprint(root)
    except (OSError, ValueError, UnicodeError):
        return False


def record(root: Path = ROOT) -> None:
    """Установщик вызывает после проверки пакетов и моделей."""
    write_json(root / "data/setup-state.json", {"fingerprint": fingerprint(root)})


def _legacy_ready(root: Path) -> bool:
    """Миграция уже настроенной версии: проверяем точный lock без pip/сети."""
    try:
        if (root / "data/setup-complete.txt").read_text().strip() != "unified-v4":
            return False
        if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
            return False
        for line in (root / "requirements/full.lock.txt").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, expected = line.partition("==")
            if not separator:
                return False
            installed = metadata.version(name)
            if installed != expected and ("+" in expected or installed.partition("+")[0] != expected):
                return False
        fingerprint(root)
        return True
    except (OSError, ValueError, metadata.PackageNotFoundError):
        return False


def _system_directory() -> Path:
    """Use the OS directory API; inherited SystemRoot/WINDIR are untrusted."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_directory = kernel.GetSystemDirectoryW
    get_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Windows system directory could not be resolved through Win32.")
    directory = Path(buffer.value)
    if not directory.is_absolute():
        raise OSError("Windows returned a non-absolute system directory.")
    return directory


def _run_repair(root: Path) -> None:
    """Пользователь разрешил фоновую установку; весь вывод остаётся в журнале."""
    log = root / "data/logs/repair.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    powershell = _system_directory() / "WindowsPowerShell/v1.0/powershell.exe"
    print(f"Восстановление окружения Jarvis… Журнал: {log}", flush=True)
    with log.open("a", encoding="utf-8", buffering=1) as output:
        output.write("\n=== Автоматическое восстановление окружения ===\n")
        output.flush()
        result = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(root / "setup_local.ps1"), "-NonInteractive"], cwd=root,
                                stdout=output, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                                creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f"Восстановление Jarvis не завершилось (код {result.returncode}). См. {log}. Повторный запуск повторит попытку.")


def check_environment(root: Path = ROOT) -> bool:
    """True требует свежего процесса после установки; False — окружение готово."""
    if is_ready(root):
        return False
    from local_mode.setup_lock import repair_lock
    with repair_lock(root):
        # Первый экземпляр мог завершить восстановление, пока второй ждал.
        if is_ready(root):
            return True
        # A launcher/metadata fingerprint can become stale without breaking the
        # installed environment. Revalidate every current lock pin and asset.
        if _legacy_ready(root):
            record(root)
            return False
        _run_repair(root)
        if not is_ready(root):
            raise RuntimeError("Установщик не подтвердил готовность окружения. См. data/logs/repair.log.")
    return True
