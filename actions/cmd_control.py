import subprocess
from local_mode.processes import run as run_hidden
import sys
import re
from pathlib import Path


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"


def _get_api_key() -> str:
    from api_keys import next_key
    return next_key()


def _get_platform() -> str:
    if sys.platform == "win32":  return "windows"
    if sys.platform == "darwin": return "macos"
    return "linux"

WIN_COMMAND_MAP = [
    (["disk space", "disk usage", "storage", "free space", "c drive space"],
     "wmic logicaldisk get caption,freespace,size /format:list", False),
    (["running processes", "list processes", "show processes", "active processes", "tasklist"],
     "tasklist /fo table", False),
    (["ip address", "my ip", "network info", "ipconfig"],
     "ipconfig /all", False),
    (["ping", "internet connection", "connected to internet"],
     "ping -n 4 google.com", False),
    (["open ports", "listening ports", "netstat"],
     "netstat -an | findstr LISTENING", False),
    (["wifi networks", "available wifi", "wireless networks"],
     "netsh wlan show networks", False),
    (["system info", "computer info", "hardware info", "pc info", "specs"],
     "systeminfo", False),
    (["cpu usage", "processor usage"],
     "wmic cpu get loadpercentage", False),
    (["memory usage", "ram usage"],
     "wmic OS get FreePhysicalMemory,TotalVisibleMemorySize /Value", False),
    (["windows version", "os version"],
     "ver", False),
    (["installed programs", "installed software", "installed apps"],
     "wmic product get name,version /format:table", False),
    (["battery", "battery level", "power status"],
     "powershell (Get-WmiObject -Class Win32_Battery).EstimatedChargeRemaining", False),
    (["current time", "what time", "system time"],
     "time /t", False),
    (["current date", "what date", "system date"],
     "date /t", False),
    (["desktop files", "files on desktop"],
     f'dir "{Path.home() / "Desktop"}"', False),
    (["downloads", "files in downloads"],
     f'dir "{Path.home() / "Downloads"}"', False),
    (["large files", "biggest files", "largest files"],
     'powershell "Get-ChildItem C:\\ -Recurse -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 10 FullName,Length | Format-Table"', False),
]

def _find_hardcoded(task: str) -> str | None:
    task_lower = task.lower()
    
    if "notepad" in task_lower or any(ext in task_lower for ext in [".txt", ".log", ".md", ".csv"]):
        file_match = re.search(r'[\"\']?([\S]+\.(?:txt|log|md|csv|json|xml))[\"\']?', task, re.IGNORECASE)
        if file_match:
            filename = file_match.group(1)
            desktop  = Path.home() / "Desktop"
            filepath = Path(filename) if Path(filename).is_absolute() else desktop / filename
            return f'notepad "{filepath}"'
        if "notepad" in task_lower:
            return "notepad"
    pip_match = re.search(r"install\s+([\w\-]+)", task_lower)
    if pip_match:
        package = pip_match.group(1)
        return f"pip install {package}"

    for keywords, command, _ in WIN_COMMAND_MAP:
        if command and any(kw in task_lower for kw in keywords):
            return command

    return None

BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b", r"\brmdir\s+/s\b", r"\bdel\s+/[fqs]",
    r"\bformat\b", r"\bdiskpart\b", r"\bfdisk\b",
    r"\breg\s+(delete|add)\b", r"\bbcdedit\b",
    r"\bnet\s+localgroup\b",
    r"\bshutdown\b", r"\brestart-computer\b",
    r"\bstop-process\b", r"\bkill\s+-9\b", r"\btaskkill\b",
    r"\beval\b", r"\b__import__\b",
]
_BLOCKED_RE = re.compile("|".join(BLOCKED_PATTERNS), re.IGNORECASE)


def _is_safe(command: str) -> tuple[bool, str]:
    match = _BLOCKED_RE.search(command)
    if match:
        return False, f"Blocked pattern: '{match.group()}'"
    return True, "OK"

def _ask_gemini(task: str) -> str:
    try:
        from local_mode.model import ContentModel
        model = ContentModel("gemini-3.6-flash")

        prompt = (
            f"Convert this request to a single Windows CMD command.\n"
            f"Output ONLY the command. No explanation, no markdown, no backticks.\n"
            f"If unsafe or impossible, output: UNSAFE\n\n"
            f"Request: {task}\n\nCommand:"
        )
        response = model.generate_content(prompt)
        command  = response.text.strip().strip("`").strip()
        if command.startswith("```"):
            lines   = command.split("\n")
            command = "\n".join(lines[1:-1]).strip()
        return command
    except Exception as e:
        return f"ERROR: {e}"

_RUN_GATE = """import json, os, subprocess, sys
request = json.loads(sys.stdin.buffer.readline(64001))
command = request['command']
if os.name == 'nt':
    sys.path.insert(0, request['module_root'])
    from local_mode.processes import run
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    raise SystemExit(run(command, stdin=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup).returncode)
os.execv(command[0], command)
"""


def _native_output_encoding():
    return "oem" if sys.platform == "win32" else None


def _decode_output(raw):
    """Prefer BOM/strict UTF-8; native legacy decoding remains an assumption."""
    if not isinstance(raw, bytes):
        return str(raw), "text", False, False
    encoding = ("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else
                "utf-32" if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")) else
                "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8")
    try:
        return raw.decode(encoding), encoding, False, False
    except UnicodeDecodeError:
        # A BOM declares its encoding; do not reinterpret malformed BOM data.
        native = _native_output_encoding() if encoding == "utf-8" else None
        if native:
            try:
                return raw.decode(native), native, False, True
            except UnicodeDecodeError:
                return raw.decode(native, "replace"), native, True, True
        return raw.decode(encoding, "replace"), encoding, True, False


def _command_result(status, *, stdout=b"", stderr=b"", exit_code=None, complete=True, pid=None):
    from local_mode.messages import OwnedText
    def decode(raw):
        value, encoding, loss, uncertain = _decode_output(raw)
        clipped = len(value.encode("utf-8")) > 8000
        if clipped:
            value = value.encode("utf-8")[:8000].decode("utf-8", "ignore")
        return value, encoding, loss, uncertain, clipped
    out, encoding_out, loss_out, uncertain_out, clip_out = decode(stdout)
    err, encoding_err, loss_err, uncertain_err, clip_err = decode(stderr)
    result = {"ok": status in {"succeeded", "spawned"}, "status": status,
              "exit_code": exit_code, "stdout": out, "stderr": err,
              "timed_out": status == "timed_out", "cancelled": status == "cancelled",
              "truncated": status == "output_limit" or clip_out or clip_err,
              "decoding_loss": loss_out or loss_err,
              "stdout_encoding": encoding_out, "stderr_encoding": encoding_err,
              "encoding_uncertain": uncertain_out or uncertain_err,
              "output_complete": complete and not (clip_out or clip_err or loss_out or loss_err or uncertain_out or uncertain_err),
              "completed": status in {"succeeded", "failed"}}
    if pid is not None:
        result["pid"] = pid
    if status == "spawned":
        result["message"] = OwnedText("Terminal launch requested. Command completion is not verified.")
    elif not result["ok"]:
        result["error"] = OwnedText({
            "failed": "Command execution failed.",
            "timed_out": "Command execution timed out; its process tree was stopped.",
            "cancelled": "Command execution was cancelled; its process tree was stopped.",
            "cleanup_error": "Command cleanup could not be confirmed. Some processes may still be running; verify the outcome before retrying.",
            "output_limit": "Command output exceeded the limit; its process tree was stopped.",
            "invalid_arguments": "Command execution arguments are invalid.",
            "visible_required": "Opening an application requires an explicit visible request.",
        }.get(status, "Command execution could not start."))
    return result


def _run_silent(command: str, timeout: int = 20) -> dict:
    import json
    from local_mode.owned_process import run_owned, ProcessCancelled, ProcessTimedOut, ProcessCleanupError
    if type(timeout) is not int or not 1 <= timeout <= 180:
        return _command_result("invalid_arguments", complete=False)
    platform = _get_platform()
    if platform == "windows":
        # CMD parses its command tail itself; list2cmdline's backslash escaping
        # changes quoted executable paths rather than quoting them for CMD.
        args = f'cmd.exe /d /s /c "{command}"'
    else:
        args = ["/bin/zsh" if platform == "macos" else "/bin/bash", "-c", command]
    try:
        result = run_owned([sys.executable, "-c", _RUN_GATE],
                           payload=json.dumps({"command": args, "module_root": str(BASE_DIR)}).encode(),
                           timeout=timeout, cwd=str(Path.home()), max_output=32000)
        return _command_result("succeeded" if result.returncode == 0 else "failed",
                               stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode)
    except ProcessCancelled:
        return _command_result("cancelled", complete=False)
    except ProcessTimedOut:
        return _command_result("timed_out", complete=False)
    except ProcessCleanupError:
        return _command_result("cleanup_error", complete=False)
    except RuntimeError as error:
        status = "output_limit" if str(error) == "Queued tool worker output exceeds the size limit." else "execution_error"
        return _command_result(status, complete=False)
    except (OSError, ValueError):
        return _command_result("execution_error", complete=False)


def _run_visible(command: str) -> dict:
    import shutil
    from local_mode.owned_process import check_action_cancelled, ProcessCancelled
    try:
        check_action_cancelled()
        platform = _get_platform()
        if platform == "windows":
            check_action_cancelled()
            process = subprocess.Popen(f'cmd.exe /d /s /k "{command}"',
                                       creationflags=subprocess.CREATE_NEW_CONSOLE)
        elif platform == "macos":
            escaped = command.replace("\\", "\\\\").replace('"', '\\"')
            check_action_cancelled()
            result = run_hidden(["osascript", "-e",
                                 f'tell application "Terminal" to do script "{escaped}"'],
                                capture_output=True, timeout=10)
            if result.returncode:
                return _command_result("execution_error", complete=False)
            return _command_result("spawned", complete=False)
        else:
            terminal = next((path for name in ("gnome-terminal", "xterm", "konsole")
                             if (path := shutil.which(name))), None)
            if terminal is None:
                return _command_result("execution_error", complete=False)
            args = [terminal, "-e" if Path(terminal).name in {"xterm", "konsole"} else "--",
                    "bash", "-c", f"{command}; exec bash"]
            check_action_cancelled()
            process = subprocess.Popen(args)
        return _command_result("spawned", complete=False, pid=process.pid)
    except ProcessCancelled:
        return _command_result("cancelled", complete=False)
    except (OSError, subprocess.TimeoutExpired):
        return _command_result("execution_error", complete=False)

def explicit_command_error(command: str) -> str | None:
    """Одинаковая проверка до подтверждения и перед выполнением команды."""
    safe, reason = _is_safe(command)
    if not safe:
        return "Команда запрещена правилами терминала: " + reason
    if re.search(r'[&|<>^()%;!\r\n]', command):
        return ("Эта команда содержит неподдерживаемый синтаксис оболочки. "
                "Подтверждение не разрешает такой синтаксис. Уточните задачу, "
                "чтобы использовать встроенный инструмент вместо команды терминала.")
    return None


def cmd_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None
) -> str | dict:
    task    = (parameters or {}).get("task", "").strip()
    command = (parameters or {}).get("command", "").strip()
    visible = (parameters or {}).get("visible") is True

    if not task and not command:
        return _command_result("invalid_arguments", complete=False)

    hardcoded = False
    if not command:
        command = _find_hardcoded(task)
        if command:
            hardcoded = True
            print(f"[CMD] ⚡ Hardcoded: {command[:80]}")
        else:
            # Safety-first default: do not generate arbitrary shell commands from the model.
            # Users can still run commands by explicitly supplying `command`,
            # or by using one of the hardcoded safe intents above.
            return _command_result("invalid_arguments", complete=False)

    safe, reason = _is_safe(command)
    if not safe:
        return {"ok": False, "error": f"Blocked for safety: {reason}"}

    # Model-supplied explicit commands must not contain shell chaining / redirect
    # metacharacters or embedded newlines (cmd /c and cmd /k both interpret them).
    if not hardcoded:
        if error := explicit_command_error(command):
            return {"ok": False, "error": error}

    if player:
        player.write_log(f"[CMD] {command[:60]}")

    if any(x in command.lower() for x in ["notepad", "explorer", "start "]):
        if re.search(r'[&|<>^()%;!\r\n]', command):
            return _command_result("invalid_arguments", complete=False)
        if not visible:
            return _command_result("visible_required", complete=False)
    return _run_visible(command) if visible else _run_silent(command)
