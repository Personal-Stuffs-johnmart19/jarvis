"""Shared credential policy and reads validated against the opened file object."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import stat
import sys
from local_mode.messages import OwnedValueError

_POLICY = Path(__file__).resolve().parents[1] / "apps/desktop/workbench/src/modules/ai/lib/pathPolicy.json"


@lru_cache(maxsize=1)
def _rules():
    policy = json.loads(_POLICY.read_text(encoding="utf-8"))
    return (
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in policy["secretBasenames"]),
        tuple(re.compile(pattern, re.IGNORECASE) for pattern in policy.get("secretPathPatterns", [])),
        tuple(policy["protectedDirs"]),
    )


def _plain_windows_path(value: str) -> str:
    # Do not turn unpaired UTF-16 code units into replacement characters.
    value.encode("utf-8", errors="strict")
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    return value[4:] if value.startswith("\\\\?\\") else value


def _same_path(left: Path, right: Path) -> bool:
    left = (_plain_windows_path(str(left)) if os.name == "nt" else str(left)).replace("\\", "/")
    right = (_plain_windows_path(str(right)) if os.name == "nt" else str(right)).replace("\\", "/")
    if os.name == "nt" and not left.casefold().startswith(("//wsl.localhost/", "//wsl$/")):
        return left.casefold() == right.casefold()
    return left == right


def check_readable(path) -> None:
    value = os.fspath(path)
    if not isinstance(value, str):
        raise OwnedValueError("Tools cannot access keys or tokens.")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise OwnedValueError("Tools require a valid Unicode file path.") from error
    if not value or any(ord(char) < 32 for char in value):
        raise OwnedValueError("Tools require a valid file path.")
    normalized = value.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].replace("\ufeff", " ")
    patterns, path_patterns, directories = _rules()
    def component(part):
        part = part.split(":", 1)[0]
        while part and (part[-1] in ".\ufeff" or part[-1].isspace()):
            part = part[:-1]
        return part.casefold()
    comparison = "/".join(component(part) for part in normalized.split("/"))
    # Shared directory patterns begin with '/'; ZIP members are relative names.
    comparison = "/" + comparison.lstrip("/")
    if (any(pattern.search(basename) for pattern in patterns)
            or any(pattern.search(comparison) for pattern in path_patterns)
            or any(directory + "/" in comparison + "/" for directory in directories)):
        raise OwnedValueError("Tools cannot access keys or tokens.")
    cache = (os.environ.get("LOCALAPPDATA") if os.name == "nt" else
             str(Path.home() / "Library/Caches") if sys.platform == "darwin" else
             os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    if cache:
        directory = Path(cache) / "terax"
        for candidate in (directory, directory.resolve()):
            try:
                inside = os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(candidate)))) == os.path.normcase(str(candidate))
            except ValueError:
                continue
            if inside:
                raise OwnedValueError("Tools cannot access keys or tokens.")


def opened_path(stream, selected=None) -> Path:
    """Resolve the live descriptor; never substitute another pathname lookup."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        function = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        function.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        function.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = function(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise OwnedValueError("Cannot validate opened file handle")
        try:
            return Path(_plain_windows_path(buffer[:length]))
        except UnicodeError as error:
            raise OwnedValueError("Opened file path is not valid Unicode") from error
    if sys.platform.startswith("linux"):
        return Path(os.readlink(f"/proc/self/fd/{stream.fileno()}"))
    if sys.platform == "darwin":
        import fcntl
        value = fcntl.fcntl(stream.fileno(), 50, bytes(1024))  # F_GETPATH
        return Path(value.split(b"\0", 1)[0].decode("utf-8"))
    raise OwnedValueError("Opened file validation is unsupported on this platform")


@contextmanager
def checked_open(path):
    """Open one selected regular file and validate its handle before any bytes."""
    from local_mode.tools import validate_paths

    selected = Path(path).expanduser()
    validate_paths({"path": str(selected)})
    selected = selected.resolve(strict=True)
    validate_paths({"path": str(selected)})
    expected = selected.stat(follow_symlinks=False)
    if not stat.S_ISREG(expected.st_mode):
        raise OwnedValueError("File reads require a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(selected, flags)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not os.path.samestat(expected, before):
            raise OwnedValueError("File changed before opening")
        actual = opened_path(stream, selected)
        check_readable(actual)
        if not _same_path(actual, selected):
            raise OwnedValueError("Opened file differs from selected file")
        yield stream
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise OwnedValueError("File changed while reading")


def checked_stat(path):
    with checked_open(path) as stream:
        return os.fstat(stream.fileno())
