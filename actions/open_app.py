# actions/open_app.py
# Jarvis — Cross-Platform App Launcher

import ctypes
import time
import json
from functools import lru_cache
import os
import subprocess
from local_mode.processes import run as run_hidden
import platform
import shutil
from pathlib import Path

_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    "chrome":             {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "spotify"},
    "vscode":             {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "telegram"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gedit"},
    "calculator":         {"Windows": "calc.exe",               "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "bash"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "gimp"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "vlc":                {"Windows": "vlc",                    "Darwin": "VLC",                 "Linux": "vlc"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Preferences",  "Linux": "gnome-control-center"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "bash"},
    "edge":               {"Windows": "msedge",                 "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion"},
    "blender":            {"Windows": "blender",                "Darwin": "Blender",             "Linux": "blender"},
    "capcut":             {"Windows": "CapCut",                 "Darwin": "CapCut",              "Linux": "capcut"},
    "postman":            {"Windows": "Postman",                "Darwin": "Postman",             "Linux": "postman"},
    "figma":              {"Windows": "Figma",                  "Darwin": "Figma",               "Linux": "figma"},
}


def _normalize(raw: str) -> str:
    system = platform.system()
    key    = raw.lower().strip()
    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(system, raw)
    if system == "Windows":
        return raw
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(system, raw)
    return raw


def _start_menu_shortcuts() -> list[Path]:
    """All .lnk files from the user + system Start Menu programs folders."""
    dirs = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    links = []
    for d in dirs:
        if d.exists():
            try:
                links.extend(p for p in d.rglob("*.lnk") if p.is_file())
            except Exception:
                pass
    return links

def _registry_app_paths() -> list[tuple[str, str]]:
    """(DisplayName, InstallLocation) from Uninstall registry keys."""
    result = []
    try:
        import winreg
    except ImportError:
        return result

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        sub = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sub) as k:
                            name = winreg.QueryValueEx(k, "DisplayName")[0]
                            loc  = ""
                            try:
                                loc = winreg.QueryValueEx(k, "InstallLocation")[0]
                            except OSError:
                                pass
                            if name:
                                result.append((name, loc))
                    except OSError:
                        continue
        except OSError:
            continue
    return result

def _find_registry_exe(app_name: str) -> str | None:
    app_lower = app_name.lower().strip()
    for name, loc in _registry_app_paths():
        if app_lower in name.lower():
            if loc and Path(loc).is_dir():
                for exe in Path(loc).iterdir():
                    if exe.suffix.lower() == ".exe" and app_lower in exe.stem.lower():
                        return str(exe)
    return None

def _launch_start_menu(app_name: str) -> bool:
    """Launch via Start Menu .lnk — the most reliable Windows path."""
    app_lower = app_name.lower().strip()
    for link in _start_menu_shortcuts():
        stem_lower = link.stem.lower()
        if app_lower == stem_lower or (app_lower in stem_lower and len(app_lower) > 2):
            try:
                os.startfile(str(link))
                return True
            except Exception:
                continue
    return False

def _windows_directory() -> Path:
    """Resolve the OS installation through Win32, never an inherited environment."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_directory = kernel.GetWindowsDirectoryW
    get_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
    get_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("Windows directory could not be resolved through Win32.")
    directory = Path(buffer.value)
    if not directory.is_absolute():
        raise OSError("Windows returned a non-absolute installation directory.")
    return directory


@lru_cache(maxsize=1)
def _windows_start_apps(bucket: int) -> list[dict[str, str]]:
    """Каталог из Windows кэшируется на минуту; текст модели не попадает в скрипт."""
    powershell = _windows_directory() / "System32/WindowsPowerShell/v1.0/powershell.exe"
    script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); Get-StartApps | Select-Object -First 2000 Name,AppID | ConvertTo-Json -Compress"
    result = run_hidden([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=20, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    data = json.loads(result.stdout.decode("utf-8-sig"))
    items = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("Name"), str)
            and isinstance(item.get("AppID"), str) and item["AppID"]
            and not any(ord(c) < 32 for c in item["AppID"])]


def _launch_windows(app_name: str) -> bool:
    """Запускаем найденный объект, без cmd/start и печати текста модели в меню Пуск."""
    if not app_name or len(app_name) > 200 or any(ord(c) < 32 for c in app_name):
        raise ValueError("Некорректное имя приложения.")
    if app_name == "ms-settings:":
        os.startfile("ms-settings:")
        return True
    # Системные программы запускаем по полному пути, без поиска в текущем каталоге.
    system_apps = {"notepad.exe", "calc.exe", "mspaint.exe", "taskmgr.exe", "cmd.exe", "powershell.exe", "charmap.exe", "snippingtool.exe"}
    if app_name.casefold() in system_apps:
        relative = "WindowsPowerShell/v1.0/powershell.exe" if app_name.casefold() == "powershell.exe" else app_name.casefold()
        executable = _windows_directory() / "System32" / relative
        if executable.is_file():
            subprocess.Popen([str(executable)], shell=False, close_fds=True)
            return True
    if app_name.casefold() == "explorer.exe":
        subprocess.Popen([str(_windows_directory() / "explorer.exe")], shell=False, close_fds=True)
        return True
    try:
        items = _windows_start_apps(int(time.monotonic() // 60))
    except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
        items = []
    packaged = {"mspaint.exe": "Microsoft.Paint_8wekyb3d8bbwe!App",
                "calc.exe": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                "notepad.exe": "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App",
                "snippingtool.exe": "Microsoft.ScreenSketch_8wekyb3d8bbwe!App"}
    packaged_id = packaged.get(app_name.casefold())
    packaged_items = [item for item in items if item["AppID"] == packaged_id] if packaged_id else []
    query = app_name.casefold()
    exact = [item for item in items if item["Name"].casefold() == query]
    matches = packaged_items or exact or [item for item in items if len(query) >= 3 and query in item["Name"].casefold()]
    # Один AppID может иметь несколько локализованных отображаемых имён.
    matches = list({item["AppID"]: item for item in matches}.values())
    if len(matches) > 1:
        raise ValueError("Уточните название приложения: " + ", ".join(item["Name"] for item in matches[:8]))
    if matches:
        explorer = _windows_directory() / "explorer.exe"
        subprocess.Popen([str(explorer), "shell:AppsFolder\\" + matches[0]["AppID"]], shell=False, close_fds=True)
        return True
    # Сохраняем исходную поддержку зарегистрированных программ и ярлыков.
    if _launch_start_menu(app_name):
        return True
    executable = _find_registry_exe(app_name)
    if executable:
        subprocess.Popen([executable], shell=False, close_fds=True)
        return True
    return False


def _launch_macos(app_name: str) -> bool:
    try:
        result = run_hidden(["open", "-a", app_name], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = run_hidden(["open", "-a", f"{app_name}.app"], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ macOS Spotlight failed: {e}")
        return False



def _launch_linux(app_name: str) -> bool:
    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-"))
    )
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        run_hidden(["xdg-open", app_name], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    try:
        desktop_name = app_name.lower().replace(" ", "-")
        run_hidden(["gtk-launch", desktop_name], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "Please specify which application to open, sir."

    system   = platform.system()
    launcher = _OS_LAUNCHERS.get(system)

    if launcher is None:
        return f"Unsupported OS: {system}"

    normalized = _normalize(app_name)
    print(f"[open_app] 🚀 Launching: {app_name} → {normalized} ({system})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        success = launcher(normalized)

        if success:
            return (f"Запрос запуска передан Windows: {app_name}. Готовность окна ещё не подтверждена."
                    if system == "Windows" else f"Opened {app_name} successfully, sir.")

        if normalized != app_name:
            success = launcher(app_name)
            if success:
                return (f"Запрос запуска передан Windows: {app_name}. Готовность окна ещё не подтверждена."
                        if system == "Windows" else f"Opened {app_name} successfully, sir.")

        return (
            f"Failed to open {app_name}: application not found or launch not confirmed. "
            f"It may still be loading or might not be installed."
        )

    except Exception as e:
        print(f"[open_app] ❌ {e}")
        return f"Failed to open {app_name}, sir: {e}"