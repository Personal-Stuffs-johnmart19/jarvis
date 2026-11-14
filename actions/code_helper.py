# actions/code_helper.py
# AI-powered code assistant — writes, edits, explains, runs, builds, debugs, and optimizes code.
#
# Actions:
#   write        → Describe what you want, Gemini writes it, saves to file
#   edit         → Read existing file, apply natural language change
#   explain      → Explain what a piece of code or file does
#   run          → Execute a script file, return output
#   build        → Generate and save code; execution requires a separate approved run
#   screen_debug → Retired capture path; direct to confirmed local capture or manual attachment
#   optimize     → Mevcut kodu Gemini ile optimize et (performans, okunabilirlik, best practices)
#   auto         → (default) Intent auto-detected from context

import sys
import re
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from local_mode.messages import OwnedText, OwnedError, OwnedValueError


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR           = get_base_dir()
API_CONFIG_PATH    = BASE_DIR / "config" / "api_keys.json"
DESKTOP            = Path.home() / "Desktop"
GEMINI_MODEL       = "gemini-3.6-flash"
MAX_SOURCE_BYTES = 2 * 1024 * 1024


def _get_api_key() -> str:
    from api_keys import next_key
    return next_key()


def _get_gemini(model: str = GEMINI_MODEL):
    from local_mode.model import ContentModel
    return ContentModel(model)


def _clean_code(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _resolve_save_path(output_path: str, language: str) -> Path:
    ext_map = {
        "python": ".py", "py": ".py",
        "javascript": ".js", "js": ".js",
        "typescript": ".ts", "ts": ".ts",
        "html": ".html", "css": ".css",
        "java": ".java", "cpp": ".cpp", "c": ".c",
        "bash": ".sh", "shell": ".sh", "powershell": ".ps1",
        "sql": ".sql", "json": ".json", "rust": ".rs", "go": ".go",
    }
    if output_path:
        p = Path(output_path)
        return p if p.is_absolute() else DESKTOP / p
    ext = ext_map.get((language or "python").lower(), ".py")
    return DESKTOP / f"jarvis_code{ext}"


def _read_file(file_path: str) -> tuple[str, str]:
    if not file_path:
        return "", "No file path provided."
    p = Path(file_path)
    if not p.exists():
        return "", f"File not found: {file_path}"
    try:
        from local_mode.file_policy import checked_open
        with checked_open(p) as source:
            contents = source.read(MAX_SOURCE_BYTES + 1)
        if len(contents) > MAX_SOURCE_BYTES:
            raise OwnedValueError("Source file exceeds 2 MiB.")
        return contents.decode("utf-8"), ""
    except OwnedError:
        raise
    except Exception as e:
        return "", f"Could not read file: {e}"


def _replace_windows(temporary: Path, destination: Path, backup: Path) -> None:
    import ctypes
    from ctypes import wintypes
    replace = ctypes.WinDLL("kernel32", use_last_error=True).ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    replace.restype = wintypes.BOOL
    # No IGNORE_ACL_ERRORS: an existing target's DACL must survive publication.
    if not replace(str(destination), str(temporary), str(backup), 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def _publish_file(temporary: Path, destination: Path) -> None:
    if os.name != "nt" or not destination.exists():
        os.replace(temporary, destination)
        return
    backup = temporary.with_suffix(".bak")
    try:
        _replace_windows(temporary, destination, backup)
    except OSError:
        # ReplaceFileW can move the old target to backup even when it fails.
        # Restore it before reporting failure; retain backup if rollback fails.
        if backup.exists():
            os.replace(backup, destination)
        raise
    else:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            # Publication succeeded; a cleanup failure must not misreport it as
            # a failed write. Leave the old bytes as a recoverable backup.
            pass


def _save_file(path: Path, content: str) -> str:
    # Publish only a fully written file. A failure must leave the previous target
    # intact and propagate to the owning action rather than become success prose.
    # Match normal file writes through symlinks; replace the referent, not the link.
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous_mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        previous_mode = None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=destination.parent, prefix=f".{destination.name}.",
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if previous_mode is not None:
            os.chmod(temporary, previous_mode)
        _publish_file(temporary, destination)
        return f"Saved to: {path}"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _code_failure(status: str, template: str, *values) -> dict:
    return {"ok": False, "status": status, "error": OwnedText(template, *values)}


def _preview(code: str, lines: int = 10) -> str:
    all_lines = code.splitlines()
    preview   = "\n".join(all_lines[:lines])
    suffix    = f"\n... ({len(all_lines) - lines} more lines)" if len(all_lines) > lines else ""
    return preview + suffix


def _detect_intent(description: str, file_path: str, code: str) -> str:
    desc = (description or "").lower()

    screen_kw = ["ekrandaki", "screen", "ekranda", "bu hatayı", "why am i getting",
                 "neden hata", "what's wrong", "ne yanlış", "screenshot", "görüntü"]
    if any(k in desc for k in screen_kw):
        return "screen_debug"

    # Optimize keywords
    optimize_kw = ["optimize", "refactor", "clean up", "improve", "temizle",
                   "iyileştir", "daha iyi", "make it better", "hızlandır"]
    if any(k in desc for k in optimize_kw) and (code or file_path):
        return "optimize"

    if file_path:
        p = Path(file_path)
        edit_kw  = ["edit", "update", "modify", "change", "add", "remove",
                    "refactor", "fix", "rename", "replace", "düzenle", "değiştir"]
        run_kw   = ["run", "execute", "launch", "start", "çalıştır"]
        build_kw = ["build", "make it work", "try", "attempt"]

        if p.exists() and any(k in desc for k in edit_kw):
            return "edit"
        if p.exists() and any(k in desc for k in run_kw):
            return "run"
        if any(k in desc for k in build_kw):
            return "build"
        if p.exists():
            return "explain"

    explain_kw = ["explain", "what does", "describe", "analyze", "açıkla", "ne yapıyor"]
    if any(k in desc for k in explain_kw) and (code or file_path):
        return "explain"

    build_kw = ["build", "make it work", "try and", "attempt"]
    if any(k in desc for k in build_kw):
        return "build"

    return "write"

def _write(description: str, language: str, output_path: str, player=None) -> tuple[str, Path]:
    lang  = language or "python"
    model = _get_gemini()

    prompt = f"""You are an expert {lang} developer.
Write clean, working, well-commented {lang} code for the description below.

Rules:
- Output ONLY the code. No explanation, no markdown, no backticks.
- Add helpful inline comments.
- Handle errors and edge cases properly.
- Use modern best practices.

Description: {description}

Code:"""

    response = model.generate_content(prompt)
    code     = _clean_code(response.text)
    path     = _resolve_save_path(output_path, lang)
    _save_file(path, code)
    return code, path


def _interpreters():
    return {
        ".py":  [sys.executable],
        ".js":  ["node"],
        ".ts":  ["ts-node"],
        ".sh":  ["bash"],
        ".ps1": ["powershell", "-File"],
        ".rb":  ["ruby"],
        ".php": ["php"],
    }


def _build(description, language, output_path, player=None) -> dict:
    if not description:
        return _code_failure("invalid_arguments", "Please describe what you want me to build, sir.")

    if player:
        player.write_log("[Code] Build started...")

    try:
        code, path = _write(description, language, output_path, player)
    except Exception as e:
        return _code_failure("generation_or_save_failed", "Could not prepare code: {0}", str(e))
    # Intent approval cannot cover unseen generated side effects. The saved file
    # can be reviewed before a new canonical run request obtains its own receipt.
    return {"ok": True, "status": "saved", "file_path": str(path),
            "execution_pending": True,
            "result": OwnedText("Code saved to {0}. Review it, then request code_helper.run with a separate approval to execute it.", str(path)),
            "preview": _preview(code)}

def _write_action(description, language, output_path, player) -> str | dict:
    if not description:
        return _code_failure("invalid_arguments", "Please describe what you want me to write, sir.")
    if player:
        player.write_log("[Code] Writing code...")
    try:
        code, path = _write(description, language, output_path, player)
        print(f"[Code] ✅ Written: {path}")
        return f"Code written. Saved to: {path}\n\nPreview:\n{_preview(code)}"
    except Exception as e:
        return _code_failure("generation_or_save_failed", "Could not generate or save code: {0}", str(e))


def _edit_action(file_path, instruction, player) -> str | dict:
    if not file_path:
        return _code_failure("invalid_arguments", "Please provide a file path to edit, sir.")
    if not instruction:
        return _code_failure("invalid_arguments", "Please describe what change to make, sir.")

    content, err = _read_file(file_path)
    if err:
        return _code_failure("read_failed", "Could not read code: {0}", err)

    if player:
        player.write_log("[Code] Editing file...")

    prompt = f"""You are an expert code editor.
Apply the following change to the code below.
Return ONLY the complete updated code — no explanation, no markdown, no backticks.

Change: {instruction}

Original code:
{content}

Updated code:"""

    try:
        model = _get_gemini()
        response = model.generate_content(prompt)
        edited   = _clean_code(response.text)
    except Exception as e:
        return _code_failure("generation_failed", "Could not edit code: {0}", str(e))

    try:
        status = _save_file(Path(file_path), edited)
    except Exception as e:
        return _code_failure("save_failed", "Could not save code: {0}", str(e))
    print(f"[Code] ✅ Edited: {file_path}")
    return f"File edited. {status}\n\nPreview:\n{_preview(edited)}"


def _explain_action(file_path, code, player) -> str:
    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return err
    if not code:
        return "Please provide code or a file path to explain, sir."

    if player:
        player.write_log("[Code] Analyzing code...")

    model  = _get_gemini()
    prompt = f"""Explain what this code does in simple, clear language.
Focus on: what it does, how it works, and any important details.
Be concise — 3 to 6 sentences maximum.

Code:
{code[:4000]}

Explanation:"""

    try:
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        return f"Could not explain code: {e}"


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


def _bounded_run_text(raw):
    decoding_loss = False
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", "replace")
            decoding_loss = True
    else:
        text = str(raw or "")
    def size(end):
        return len(json.dumps(text[:end], ensure_ascii=False).encode("utf-8"))
    if size(len(text)) <= 8000:
        return text, False, decoding_loss
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if size(middle) <= 8000:
            low = middle
        else:
            high = middle - 1
    return text[:low], True, decoding_loss


def _run_result(status, *, stdout=b"", stderr=b"", exit_code=None, output_complete=True):
    output, clipped_out, loss_out = _bounded_run_text(stdout)
    errors, clipped_err, loss_err = _bounded_run_text(stderr)
    result = {"ok": status == "succeeded", "status": status, "exit_code": exit_code,
              "stdout": output, "stderr": errors, "timed_out": status == "timed_out",
              "cancelled": status == "cancelled",
              "truncated": clipped_out or clipped_err,
              "decoding_loss": loss_out or loss_err,
              "output_complete": output_complete and not (clipped_out or clipped_err or loss_out or loss_err)}
    if not result["ok"]:
        template = {
            "invalid_arguments": "Code execution arguments are invalid.",
            "failed": "Code execution failed.",
            "execution_error": "Code execution failed.",
            "timed_out": "Code execution timed out; its process tree was stopped.",
            "cancelled": "Code execution was cancelled; its process tree was stopped.",
            "output_limit": "Code execution output exceeded the limit; its process tree was stopped.",
        }.get(status, "Code execution could not start.")
        result["error"] = OwnedText(template)
    return result


def _run_action(file_path, args, timeout, player, *, code_supplied=False):
    if (code_supplied or not file_path or not isinstance(args, list) or len(args) > 128
            or any(not isinstance(arg, str) or len(arg) > 2000 for arg in args)
            or type(timeout) is not int or not 1 <= timeout <= 180):
        return _run_result("invalid_arguments")
    try:
        path = Path(file_path).resolve(strict=True)
        if not path.is_file():
            return _run_result("missing_file")
    except (OSError, ValueError, RuntimeError):
        return _run_result("missing_file")
    command = _interpreters().get(path.suffix.lower())
    if not command:
        return _run_result("unsupported_runtime")
    executable = shutil.which(command[0])
    if not executable:
        return _run_result("missing_runtime")
    if player:
        player.write_log(f"[Code] Running {path.name}...")
    from local_mode.owned_process import run_owned, ProcessTimedOut, ProcessCancelled
    environment = dict(os.environ)
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        # The gate waits for stdin until run_owned has assigned its process tree.
        process = run_owned([sys.executable, "-c", _RUN_GATE],
                            payload=json.dumps({"command": [executable, *command[1:], str(path), *args], "module_root": str(BASE_DIR)}).encode(),
                            timeout=timeout, cwd=str(path.parent), env=environment, max_output=32000)
        return _run_result("succeeded" if process.returncode == 0 else "failed",
                           stdout=process.stdout, stderr=process.stderr, exit_code=process.returncode)
    except ProcessTimedOut:
        # The existing primitive does not expose partial output after termination.
        return _run_result("timed_out", output_complete=False)
    except ProcessCancelled:
        return _run_result("cancelled", output_complete=False)
    except RuntimeError as error:
        status = "output_limit" if str(error) == "Queued tool worker output exceeds the size limit." else "execution_error"
        return _run_result(status, output_complete=False)
    except (OSError, ValueError):
        return _run_result("execution_error", output_complete=False)


def _optimize_action(file_path, code, language, output_path, player) -> str | dict:

    if file_path and not code:
        code, err = _read_file(file_path)
        if err:
            return _code_failure("read_failed", "Could not read code: {0}", err)
    if not code:
        return _code_failure("invalid_arguments", "Please provide code or a file path to optimize, sir.")

    if player:
        player.write_log("[Code] Optimizing code...")

    lang  = language or "python"
    prompt = f"""You are an expert {lang} developer and code reviewer.
Optimize the following code for:
1. Performance — eliminate unnecessary operations, use efficient data structures
2. Readability — clear variable names, proper formatting, logical structure
3. Best practices — modern {lang} patterns, error handling, type hints if applicable
4. Remove dead code, redundant comments, and unnecessary complexity

Return ONLY the optimized code — no explanation, no markdown, no backticks.

Original code:
{code[:6000]}

Optimized code:"""

    try:
        model = _get_gemini()
        response  = model.generate_content(prompt)
        optimized = _clean_code(response.text)
    except Exception as e:
        return _code_failure("generation_failed", "Could not optimize code: {0}", str(e))

    # Kaydet
    if file_path:
        save_path = Path(file_path)
    else:
        save_path = _resolve_save_path(output_path, lang)

    try:
        status = _save_file(save_path, optimized)
    except Exception as e:
        return _code_failure("save_failed", "Could not save code: {0}", str(e))
    print(f"[Code] ✅ Optimized: {save_path}")

    original_lines  = len(code.splitlines())
    optimized_lines = len(optimized.splitlines())
    diff = original_lines - optimized_lines

    return (
        f"Code optimized. {status}\n"
        f"Lines: {original_lines} → {optimized_lines} "
        f"({'−' if diff > 0 else '+'}{abs(diff)} lines)\n\n"
        f"Preview:\n{_preview(optimized)}"
    )


def _screen_debug_action(description, file_path, player, speak=None) -> str:
    from actions.screen_processor import CAPTURE_GUIDANCE
    return CAPTURE_GUIDANCE


def code_helper(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str | dict:
    """
    Called from the assistant runtime.

    parameters:
        action      : write | edit | explain | run | build | screen_debug | optimize | auto
        description : What the code should do / what change to make / what problem to analyze
        language    : Programming language (default: python)
        output_path : Where to save — user specifies full path or filename
        file_path   : Path to existing file (edit / explain / run / build / optimize)
        code        : Raw code string (explain/optimize without a file)
        args        : Literal CLI argument list for run; build does not execute
        timeout     : Run timeout in seconds (default: 30)
    """
    p           = parameters or {}
    action      = p.get("action", "auto").lower().strip()
    description = p.get("description", "").strip()
    language    = p.get("language", "python").strip()
    output_path = p.get("output_path", "").strip()
    file_path   = p.get("file_path", "").strip()
    code        = p.get("code", "").strip()
    args        = p.get("args", [])
    if action == "auto":
        action = _detect_intent(description, file_path, code)
        print(f"[Code] 🤖 Auto-detected: {action}")

    if action == "write":
        return _write_action(description, language, output_path, player)

    elif action == "edit":
        return _edit_action(
            file_path,
            description or p.get("instruction", ""),
            player
        )

    elif action == "explain":
        return _explain_action(file_path, code, player)

    elif action == "run":
        return _run_action(file_path, args, p.get("timeout", 30), player, code_supplied="code" in p)

    elif action == "build":
        return _build(description, language, output_path, player)

    elif action == "optimize":
        return _optimize_action(file_path, code, language, output_path, player)

    elif action == "screen_debug":
        return _screen_debug_action(description, file_path, player, speak)

    else:
        return f"Unknown action: '{action}'. Use write, edit, explain, run, build, optimize, or screen_debug."
