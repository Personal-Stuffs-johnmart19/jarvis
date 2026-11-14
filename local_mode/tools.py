"""Полный ленивый реестр 53 инструментов оригинального Jarvis.

Вход модели проверяется JSON Schema. Согласие на опасное действие приходит
только отдельной командой пользователя, не аргументом function call.
"""
from __future__ import annotations
import importlib
import inspect
import json
import re
import threading
import logging
import os
from copy import deepcopy
from pathlib import Path, PureWindowsPath
from urllib.parse import urlparse
from typing import Any, Callable
from jsonschema import Draft202012Validator
from google.genai import types
from local_mode.gemini import safe_error
from local_mode.messages import tool_label, OwnedText, OwnedValueError

ROOT = Path(__file__).resolve().parent
DECLARATIONS = json.loads((ROOT / "tool_declarations.json").read_text(encoding="utf-8"))
HANDLERS = json.loads((ROOT / "tool_handlers.json").read_text(encoding="utf-8"))


def declarations() -> list[types.FunctionDeclaration]:
    result = []
    for descriptor in DECLARATIONS:
        parameters = descriptor["parameters"]
        if descriptor["name"] == "code_helper":
            # Keep conditional validation authoritative in the host. Google's
            # raw JSON schema parameter accepts only a subset server-side.
            parameters = deepcopy(parameters)
            parameters.pop("allOf", None)
            parameters["properties"]["args"] = {
                "type": "array", "items": {"type": "string", "maxLength": 2000}, "maxItems": 128,
                "description": "Literal argv for action=run only; omit for other actions.",
            }
            parameters["properties"]["file_path"]["description"] += "; required for action=run"
            parameters["properties"]["code"]["description"] += "; forbidden for action=run, including an empty string"
        result.append(types.FunctionDeclaration(name=descriptor["name"], description=descriptor["description"],
                                               parameters_json_schema=parameters))
    return result


class _ToolPlayer:
    """Синхронный результат выводит основной цикл; фоновые события сохраняются."""
    def __init__(self, player: Any, owner: int) -> None:
        self._player = player
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._player, name)

    def write_log(self, text: str) -> None:
        if threading.get_ident() != self._owner:
            self._player.write_log(text)
        else:
            logging.getLogger(__name__).debug("Tool: %s", text)


def _argument_values(value, depth=0):
    if depth > 16:
        raise ValueError("Tool arguments are nested too deeply.")
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            if isinstance(item, (dict, list)):
                yield from _argument_values(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _argument_values(item, depth + 1)


def _windows_file_path(value):
    """Use the filesystem namespace, never devices, streams or shell aliases.

    Long drive/UNC paths remain supported; normalize their prefix for the same
    credential comparison as ordinary paths. See Microsoft's Naming a File guide.
    """
    failure = "Windows device paths, data streams and ambiguous filenames are not allowed."
    raw = value.replace("/", "\\")
    if raw.startswith("\\\\.\\"):
        raise OwnedValueError(failure)
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
        if raw.upper().startswith("UNC\\"):
            raw = "\\\\" + raw[4:]
        elif not re.match(r"^[A-Za-z]:\\", raw):
            raise OwnedValueError(failure)
    path = PureWindowsPath(raw)
    if path.drive and not path.root:
        raise OwnedValueError(failure)
    for part in path.parts:
        if part == path.anchor or part in {".", ".."}:
            continue
        stem = part.split(".", 1)[0].rstrip(" ").casefold()
        if (part.endswith((".", " ")) or any(c in part for c in ':*?"<>|')
                or any(ord(c) < 32 for c in part)
                or stem in {"con", "prn", "aux", "nul", "conin$", "conout$"}
                or len(stem) == 4 and stem[:3] in {"com", "lpt"} and stem[3] in "123456789¹²³"):
            raise OwnedValueError(failure)
    return str(path)


def _credential_path(path):
    from local_mode.file_policy import check_readable
    check_readable(path)
    return (any(part.casefold() in {".ssh", ".aws", ".gnupg", ".codex"} for part in path.parts)
            or path.name.casefold().startswith(".env")
            or path.name.casefold() in {"api_keys.json", "google_token.json", "bootstrap-key.json"})


def validate_paths(args):
    # Доступ к файлам ключей запрещён до вызова исходного инструмента.
    # Иначе содержимое файла могло бы уйти в следующий запрос модели.
    from local_mode.config import ROOT as PROJECT
    protected = [PROJECT / "config" / filename for filename in
        ("bootstrap-key.json", "api_keys.json", "google_token.json", "bridge_token.txt", "bridge_token.json")]
    protected.append(PROJECT / ".env")
    protected.extend([PROJECT / "data" / "network" / name for name in ("network_preferences.json", "proxy_cache.json")])
    for key, value in _argument_values(args):
        if not isinstance(value, str):
            continue
        if key in {"path", "source", "file", "file_path", "output_path", "destination"}:
            candidate = Path(_windows_file_path(value) if os.name == "nt" else value).expanduser()
            if _credential_path(candidate):
                raise OwnedValueError("Tools cannot access keys or tokens.")
            resolved = (candidate if candidate.is_absolute() else PROJECT / candidate).resolve()
            if _credential_path(resolved):
                raise OwnedValueError("Tools cannot access keys or tokens.")
            if any(resolved == item.resolve() for item in protected) or (PROJECT / "data" / "credentials").resolve() in resolved.parents:
                raise ValueError("Tools cannot access keys, tokens or internal network settings.")
        if key == "url" and urlparse(value).scheme.lower() not in {"http", "https", ""}:
            raise ValueError("Only HTTP/HTTPS URLs are allowed.")


class ToolRegistry:
    def __init__(self, player: Any = None, speak: Callable[[str], None] | None = None) -> None:
        self.player = player
        self.speak = speak
        self.wake_enabled = False
        self.pending: tuple[str, str, dict[str, Any]] | None = None
        self.source = "local"
        from local_mode.browser_api import shared_browser_api
        self.browser = shared_browser_api()
        self.special: dict[str, Callable[[dict[str, Any]], Any]] = {
            "browser_control": self.browser,
        }
        self.schemas = {d["name"]: d["parameters"] for d in DECLARATIONS}
        self._validators = {}

    def validate(self, name: str, args: dict[str, Any]) -> None:
        if name not in self.schemas:
            raise ValueError("Unknown tool: " + name)
        if len(json.dumps(args, ensure_ascii=False)) > 64000:
            raise ValueError("Tool request is too large.")
        validate_paths(args)
        validator = self._validators.get(name)
        if validator is None:
            validator = Draft202012Validator(dict(self.schemas[name], additionalProperties=False))
            self._validators[name] = validator
        validator.validate(args)
        if name == "browser_control" and args.get("action") == "queue_add" and not args.get("urls") and not args.get("url"):
            raise ValueError("No video URLs were provided. Find observed YouTube video links before adding to the queue. The queue was not changed.")

    def risky(self, name: str, args: dict[str, Any]) -> bool:
        from safety import classify, Risk
        if name == "game_updater":
            from actions.game_updater import steam_ready_result
            if steam_ready_result(args):
                return False
        # Автономный агент/сгенерированный код и внешняя отправка требуют
        # подтверждения независимо от оптимистичной исходной классификации.
        return (name in {"dev_agent", "agent_task", "send_message", "api_add_key"}
                or (name == "code_helper" and args.get("action", "auto") not in {"explain", "optimize"})
                or (name == "wake_word" and args.get("action") in {"record", "train", "reset"})
                or classify(name, args) >= Risk.HIGH)

    def execute(self, name: str, args: dict[str, Any], *, approved: bool = False) -> dict[str, Any]:
        self.validate(name, args)
        if name == "game_updater":
            from actions.game_updater import steam_ready_result
            ready = steam_ready_result(args)
            if ready:
                return ready
        if name == "cmd_control" and args.get("command"):
            from actions.cmd_control import explicit_command_error
            if error := explicit_command_error(args["command"]):
                return {"ok": False, "action": name, "error": error}
        if self.risky(name, args) and not approved:
            # Самое опасное — позволить модели самой прислать confirm=true.
            # Поэтому pending хранится приложением, а таких полей в схеме нет.
            self.pending = (self.source, name, dict(args))
            from safety import sanitize
            safe = sanitize(name, args)
            labels = {"task": "Task", "command": "Command", "action": "Action",
                      "path": "Path", "url": "URL", "visible": "Open terminal",
                      "game_name": "Game", "platform": "Platform", "app_id": "Application ID"}
            details = "\n".join(f"{labels.get(key, key)}: {value}" for key, value in safe.items())
            return {"ok": False, "action": name, "requires_confirmation": True,
                    "reply": "Confirmation required: " + tool_label(name) +
                             "\n" + details[:6000] +
                             "\nChoose Confirm or Cancel, or type confirm or cancel.",
                    "parameters": safe}
        return self._invoke(name, args)

    def confirm(self, accept: bool) -> dict[str, Any]:
        if self.pending and self.pending[0] != self.source:
            return {"ok": False, "reply": "Confirmation must come from the same channel as the request."}
        pending, self.pending = self.pending, None
        if pending is None:
            return {"ok": False, "reply": "No action is waiting for confirmation."}
        if not accept:
            return {"ok": True, "reply": "Action cancelled."}
        return self.execute(pending[1], pending[2], approved=True)

    def feedback(self) -> dict[str, Any]:
        owner = threading.get_ident()
        def background_speak(text: str) -> None:
            if self.speak and threading.get_ident() != owner:
                self.speak(text)
        return {"player": _ToolPlayer(self.player, owner) if self.player is not None else None,
                "speak": background_speak if self.speak else None}

    def _invoke(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name in self.special:
                value = self.special[name](args)
            elif name in HANDLERS:
                module, attribute = HANDLERS[name]
                handler = getattr(importlib.import_module(module), attribute)
                optional = self.feedback()
                kwargs = {k: v for k, v in optional.items() if k in inspect.signature(handler).parameters}
                value = handler(parameters=args, **kwargs)
            else:
                value = self._builtin(name, args)
            if isinstance(value, dict):
                return {"action": name, **value}
            # Строка исходного инструмента — отчёт, а не независимая проверка
            # физического результата. Не присваиваем ей verified=true.
            text = str(value)
            failed = bool(re.match(r"^(?:❌|⛔|ошибка|не удалось|error\b|failed\b|(?:i )?could not\b|(?:i )?couldn't\b|unknown\b|blocked\b|no .* available\b|.*? failed:)", text.strip(), re.I))
            if name == "game_updater":
                failed = failed or bool(re.search(r"could not|not found|not installed|failed|errors?:|не удалось|не найден|не установлен", text, re.I))
            return {"ok": not failed, "action": name, "result": value if isinstance(value, OwnedText) else text, "verification": "tool_report"}
        except Exception as error:
            return {"ok": False, "action": name, "error": safe_error(error)}

    def _builtin(self, name: str, args: dict[str, Any]) -> Any:
        from memory import memory_manager as memory
        if name == "save_memory":
            return memory.remember(args["key"], args["value"], args.get("category", "notes"))
        if name == "recall_memory":
            return memory.search_memory(args.get("query", ""), args.get("category", ""))
        if name == "forget_memory":
            return memory.forget(args["key"], args.get("category", "notes"))
        if name == "clear_memory":
            return memory.clear_memory(args.get("category", ""))
        if name in {"set_mode", "set_mood"}:
            key = "mode" if name == "set_mode" else "mood"
            return memory.remember("assistant_" + key, args[key], "preferences")
        if name == "set_voice":
            if not self.player or not hasattr(self.player, "speaker"):
                raise ValueError("Speech is not connected in this mode.")
            return self.player.speaker.select(args["voice"])
        if name == "wake_word":
            action = args.get("action", "status")
            if action in {"start", "stop"}:
                self.wake_enabled = action == "start"
            elif action in {"record", "train", "reset"}:
                from actions.wake_word import wake_word
                return wake_word(parameters=args, player=self.player)
            elif action not in {"status", "info"}:
                raise ValueError("Use start/stop/status/record/train/reset.")
            return {"ok": True, "enabled": self.wake_enabled, "engine": "vosk", "phrase": "джарвис"}
        if name == "create_presentation":
            from actions.office_builder import create_presentation
            return create_presentation(**args)
        if name == "create_spreadsheet":
            from actions.office_builder import create_spreadsheet
            return create_spreadsheet(**args)
        if name == "smart_home_control":
            from actions.smart_home import smart_home_control
            return smart_home_control(**args)
        if name == "api_add_key":
            from api_keys import add_key
            add_key(args["key"])
            return "Key updated using Windows DPAPI."
        if name == "google_auth_status":
            import google_oauth
            return google_oauth.status()
        if name == "google_auth":
            import google_oauth
            import threading
            if not google_oauth.is_configured():
                return {"ok": False, "setup_required": "Google OAuth client_id/client_secret in config/api_keys.json"}
            info = google_oauth.auth_start()
            if not info.get("ok"):
                return info
            threading.Thread(target=google_oauth.auth_poll, args=(info["device_code"],),
                             kwargs={"interval": info.get("interval", 5), "timeout": 600}, daemon=True).start()
            return {k: v for k, v in info.items() if k != "device_code"}
        raise ValueError("No handler: " + name)
