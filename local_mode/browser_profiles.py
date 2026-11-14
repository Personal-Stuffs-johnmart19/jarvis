"""Выбор профиля Chrome без копирования cookies и изменения личных настроек."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
from local_mode.config import ROOT
from local_mode.messages import OwnedValueError, OwnedRuntimeError
from local_mode.storage import write_json

PREFERENCES = ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST")=="1" else "data") / "browser-preferences.json"
MANAGED = ROOT / "data" / "local-browser"


def user_data(channel: str = "chrome") -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", ""))
    return base / {"msedge": "Microsoft/Edge/User Data", "chromium": "Chromium/User Data"}.get(channel, "Google/Chrome/User Data")


def profiles(channel: str = "chrome") -> tuple[list[dict[str, str]], str]:
    try:
        data = json.loads((user_data(channel) / "Local State").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], ""
    info = data.get("profile", {})
    result = [{"id": key, "name": str(value.get("name") or key), "email": str(value.get("user_name") or "")}
              for key, value in info.get("info_cache", {}).items()
              if re.fullmatch(r"Default|Profile [0-9]+", key) and isinstance(value, dict)]
    current = str(info.get("last_used", ""))
    if current not in {item["id"] for item in result}:
        current = result[0]["id"] if result else ""
    return result, current


def read_preferences() -> dict[str, str]:
    if not PREFERENCES.exists():
        return {"mode": "current", "profile": ""}
    data = json.loads(PREFERENCES.read_text(encoding="utf-8-sig"))
    if data.get("mode") not in {"current", "specific", "new"}:
        raise OwnedValueError("Unknown browser profile mode.")
    if data.get("engine") not in {None, "chrome"}:
        return {"mode": "current", "profile": ""}
    return {"mode": data["mode"], "profile": str(data.get("profile", ""))}


def save_preferences(mode: str, profile: str = "", channel: str = "chrome", engine: str = "chrome") -> None:
    if engine != "chrome":
        raise OwnedValueError("Unknown browser.")
    if mode not in {"current", "specific", "new"}:
        raise OwnedValueError("Unknown browser profile mode.")
    if mode == "specific" and profile not in {item["id"] for item in profiles(channel)[0]}:
        raise OwnedValueError("Select an existing profile from the list.")
    write_json(PREFERENCES, {"mode": mode, "profile": profile if mode == "specific" else ""})


def executable(channel: str) -> str:
    if channel == "chromium":
        # Путь комплектного Chromium читается без открытия браузера.
        import importlib.util
        spec = importlib.util.find_spec("playwright")
        if spec and spec.origin:
            import glob
            base = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright")))
            candidates = sorted(glob.glob(str(base / "chromium-*" / "chrome-win*" / "chrome.exe")))
            if candidates:
                return candidates[-1]
        raise OwnedValueError("Chromium is not installed. Run Jarvis setup again or select Chrome.")
    name = "msedge.exe" if channel == "msedge" else "chrome.exe"
    found = shutil.which(name)
    if found:
        return found
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}") as key:
                value = str(winreg.QueryValueEx(key, "")[0]).strip('"')
                if Path(value).is_file():
                    return value
        except OSError:
            continue
    raise OwnedValueError("Chrome/Edge was not found. Install the browser and try again.")


def launch_arguments(url: str, channel: str = "chrome", incognito: bool = False) -> list[str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or any(ord(c) < 32 for c in url):
        raise OwnedValueError("Only valid HTTP/HTTPS URLs are allowed.")
    prefs = read_preferences()
    args = [executable(channel)]
    if prefs["mode"] == "new":
        args.append("--user-data-dir=" + str(MANAGED))
    else:
        available, last = profiles(channel)
        profile = prefs["profile"] if prefs["mode"] == "specific" else last
        if profile:
            if profile not in {item["id"] for item in available}:
                raise OwnedValueError("The selected profile no longer exists. Open Jarvis browser settings.")
            args.append("--profile-directory=" + profile)
        elif prefs["mode"] == "specific":
            raise OwnedValueError("Select a profile in Jarvis browser settings.")
    if incognito:
        args.append("--incognito")
    return [*args, "--new-tab", url]


def open_url(url: str, channel: str = "chrome", incognito: bool = False) -> None:
    # Обычный запуск передаёт вкладку работающему Chrome через его механизм singleton.
    # Не включаем remote debugging и не копируем личный каталог в профиль автоматизации.
    subprocess.Popen(launch_arguments(url, channel, incognito), close_fds=True)


_MANAGED_LOCK = threading.Lock()


def managed_endpoint(channel: str = "chrome") -> str:
    """Один отдельный Chrome для обоих контроллеров, CDP только на loopback."""
    import httpx
    def running() -> str | None:
        try:
            lines = (MANAGED / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
            port = int(lines[0])
            if not 1 <= port <= 65535 or not lines[1].startswith("/devtools/browser/"):
                return None
            endpoint = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=0.5) as client:
                response = client.get(endpoint + "/json/version")
                response.raise_for_status()
                if urlsplit(response.json().get("webSocketDebuggerUrl", "")).path == lines[1]:
                    return endpoint
        except (OSError, ValueError, IndexError, httpx.HTTPError):
            pass
        return None
    with _MANAGED_LOCK:
        endpoint = running()
        if endpoint:
            return endpoint
        MANAGED.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([executable(channel), "--user-data-dir=" + str(MANAGED),
                                    "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
                                    "--no-first-run", "--no-default-browser-check", "about:blank"],
                                   close_fds=True)
        for _ in range(80):
            endpoint = running()
            if endpoint:
                return endpoint
            time.sleep(0.1)
        raise OwnedRuntimeError("Close the separate Jarvis browser window and try again: the browser connection is unavailable.")



def control_endpoint(channel: str = "chrome") -> str:
    """Личный Chrome подключается только после явного разрешения отладки в Chrome."""
    prefs = read_preferences()
    if prefs["mode"] == "new":
        return managed_endpoint(channel)
    available, current = profiles(channel)
    if prefs["mode"] == "specific" and prefs["profile"] != current:
        raise OwnedValueError("Activate the selected profile in Chrome and enable remote debugging at chrome://inspect/#remote-debugging. Connecting to another profile is not allowed.")
    if len(available) != 1:
        raise OwnedValueError("A CDP session cannot be reliably attributed when Chrome has multiple personal profiles. Select a separate Jarvis profile to control pages; opening links in the selected personal profile does not require debugging.")
    import httpx
    try:
        lines = (user_data(channel) / "DevToolsActivePort").read_text(encoding="utf-8").splitlines()
        port = int(lines[0])
        if not 1 <= port <= 65535 or not lines[1].startswith("/devtools/browser/"):
            raise OwnedValueError("Invalid Chrome endpoint.")
        endpoint = f"http://127.0.0.1:{port}"
        with httpx.Client(trust_env=False, timeout=1) as client:
            response = client.get(endpoint + "/json/version")
            response.raise_for_status()
            if urlsplit(response.json().get("webSocketDebuggerUrl", "")).path != lines[1]:
                raise OwnedValueError("The Chrome endpoint is stale.")
        return endpoint
    except (OSError, ValueError, IndexError, httpx.HTTPError) as error:
        raise OwnedValueError("To control personal tabs, enable remote debugging in Chrome 144+ at chrome://inspect/#remote-debugging and confirm the connection in Chrome. Alternatively, select a separate Jarvis profile.") from error
