"""Проверенный portable Tauri: без Node/Rust на пользовательском ПК."""

from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import urllib.request
import winreg


def webview_available() -> bool:
    # Проверяем все зарегистрированные клиенты: GUID runtime менялся у каналов.
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(
                    hive,
                    r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
                    0,
                    winreg.KEY_READ | view,
                ) as clients:
                    for index in range(winreg.QueryInfoKey(clients)[0]):
                        with winreg.OpenKey(
                            clients, winreg.EnumKey(clients, index)
                        ) as client:
                            try:
                                name = winreg.QueryValueEx(client, "name")[0]
                                version = winreg.QueryValueEx(client, "pv")[0]
                                if (
                                    "webview2" in str(name).lower()
                                    and version
                                    and version != "0.0.0.0"
                                ):
                                    return True
                            except OSError:
                                continue
            except OSError:
                continue
    return False


def ensure_webview(root: Path) -> None:
    if webview_available():
        return
    # Официальный подписанный Microsoft bootstrapper; сетевой сбой покажет диагностику запуска.
    installer = root / ".cache" / "MicrosoftEdgeWebview2Setup.exe"
    installer.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        "https://go.microsoft.com/fwlink/p/?LinkId=2124703", timeout=60
    ) as response:
        content = response.read(10 * 1024 * 1024 + 1)
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("Слишком большой установщик WebView2.")
    installer.write_bytes(content)
    # До запуска проверяем Authenticode и издателя, не доверяем одному URL.
    script = "$s=Get-AuthenticodeSignature -LiteralPath $env:JARVIS_WEBVIEW_INSTALLER; if ($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {exit 1}"
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, JARVIS_WEBVIEW_INSTALLER=str(installer)),
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    subprocess.run(
        [str(installer), "/silent", "/install"],
        check=True,
        timeout=300,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if not webview_available():
        raise RuntimeError(
            "WebView2 пока недоступен. Проверьте Интернет и повторите запуск."
        )


def desktop_binary(root: Path) -> Path:
    folder = root / "desktop-bin"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
    filename = manifest.get("file", "jarvis-desktop.exe")
    # Разрешаем только наши portable-файлы внутри desktop-bin.
    if not isinstance(filename, str) or not re.fullmatch(
        r"jarvis-desktop(?:-[0-9a-f]{64})?\.exe", filename
    ):
        raise ValueError("Некорректное имя интерфейса в manifest.json.")
    binary = folder / filename
    if binary.resolve().parent != folder.resolve():
        raise ValueError("Интерфейс должен находиться внутри desktop-bin.")
    if hashlib.sha256(binary.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("Контрольная сумма интерфейса не совпала. Обновите Jarvis.")
    return binary


def launch(root: Path) -> int:
    binary = desktop_binary(root)
    ensure_webview(root)
    environment = dict(os.environ, JARVIS_ROOT=str(root))
    native_log = root / "data/logs/desktop-native.log"
    native_log.parent.mkdir(parents=True, exist_ok=True)
    with native_log.open("ab") as output:
        result = subprocess.run(
            [str(binary)],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    if result.returncode:
        print(
            f"Jarvis exit code: {result.returncode} (0x{result.returncode & 0xffffffff:08X}); native log: {native_log}"
        )
    return result.returncode
