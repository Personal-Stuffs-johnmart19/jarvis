"""Хранение ключа DPAPI: расшифровка только в той же учётной записи Windows."""
from __future__ import annotations
import ctypes
import json
from ctypes import wintypes
from pathlib import Path
import sys
from local_mode.config import ROOT

KEY_FILE = ROOT / 'data' / 'credentials' / 'gemini.dpapi'
BOOTSTRAP_FILE = ROOT / 'config' / 'bootstrap-key.json'


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _transform(value: bytes, protect: bool) -> bytes:
    if sys.platform != 'win32':
        raise RuntimeError('DPAPI доступен только в Windows.')
    source_buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Не удалось прочитать/защитить ключ Windows DPAPI. Введите ключ заново через --set-key.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def save_key(key: str, path: Path = KEY_FILE) -> None:
    key = key.strip()
    if not 20 <= len(key) <= 4096 or any(char.isspace() for char in key):
        raise ValueError('Нужен непустой API-ключ без пробелов.')
    encrypted = _transform(key.encode('utf-8'), True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_key(path: Path = KEY_FILE) -> str | None:
    if path.is_file():
        try:
            return _transform(path.read_bytes(), False).decode('utf-8')
        except RuntimeError:
            # После переноса на другую Windows импортируем разрешённый bootstrap заново.
            if path != KEY_FILE or not BOOTSTRAP_FILE.is_file():
                raise
    if path == KEY_FILE and BOOTSTRAP_FILE.is_file():
        value = json.loads(BOOTSTRAP_FILE.read_text(encoding='utf-8-sig')).get('gemini_api_key')
        if not isinstance(value, str):
            raise ValueError('Некорректный bootstrap-файл ключа.')
        save_key(value, path)
        return value.strip()
    return None
