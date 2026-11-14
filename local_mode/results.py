"""Present app-owned results; preserve user/model/provider content verbatim."""
from __future__ import annotations
from typing import Any
import re
from local_mode.messages import message, tool_label, OwnedText


def _display_text(value: Any, locale: str) -> str:
    if isinstance(value, OwnedText):
        return message(value.template, *(_display_text(item, locale) if isinstance(item, OwnedText) else item for item in value.values), locale=locale)
    return str(value)


def present_result(result: dict[str, Any], *, locale: str = "en") -> dict[str, Any]:
    """Render chat fields and retain settings message provenance for live locale changes."""
    return {**result, **{
        key: _display_text(result[key], locale)
        for key in ("reply", "error", "result")
        if isinstance(result.get(key), OwnedText)
    }, **({"message": result["message"].payload()} if isinstance(result.get("message"), OwnedText) else {})}


def user_message(result: dict[str, Any], *, locale: str = "en") -> str:
    if result.get("action") == "game_updater" and not result.get("ok") and result.get("result"):
        return message("Could not complete the game action. {0}", result["result"], locale=locale)
    if result.get("reply"):
        return _display_text(result["reply"], locale)
    if not result.get("ok"):
        return _display_text(result.get("error") or result.get("result") or message("Action failed.", locale=locale), locale)
    action = result.get("action")
    if action == "maximize_window":
        return message("Window maximized.", locale=locale) if result.get("verified") else message("Could not verify that the window was maximized.", locale=locale)
    if action in {"youtube_play", "youtube_video"} and "verified" in result:
        return message("YouTube music is playing.", locale=locale) if result["verified"] else message("Could not verify playback.", locale=locale)
    if action == "youtube_pause":
        return message("Music paused.", locale=locale)
    if action == "list_windows":
        names = [str(item.get("title", "")) for item in result.get("windows", [])]
        return message("Open windows: {0}", "; ".join(names[:20]), locale=locale) if names else message("No matching open windows.", locale=locale)
    if action == "wake_word" and "enabled" in result:
        return message("Wake-word activation is enabled. Say Jarvis followed by a command.", locale=locale) if result["enabled"] else message("Wake-word activation is disabled.", locale=locale)
    if result.get("result"):
        return _display_text(result["result"], locale)
    if action == "schedule":
        return message("Reminder saved.", locale=locale) if "schedule" in result else message("Active reminders: {0}.", len(result.get("schedules", [])), locale=locale)
    if action == "agent_task" and result.get("task_id"):
        return message("Task queued: {0}", result["task_id"], locale=locale)
    return message("Action completed.", locale=locale)


def spoken_message(result: dict[str, Any], *, max_chars: int = 1200, locale: str = "en") -> str:
    """Prepare speech without exposing confirmation arguments, code or tables."""
    if result.get("requires_confirmation"):
        action = str(result.get("action", ""))
        return message("Confirmation required: {0}. See the chat for details. Say confirm or cancel.",
                       tool_label(action, locale=locale), locale=locale)
    if result.get("requires_setup"):
        return message("Connection setup is required. See the chat and settings for details.", locale=locale)
    if isinstance(result.get("result"), (dict, list)) and not result.get("reply"):
        return message("The result is shown in chat.", locale=locale) if result.get("ok") else message("Action failed. See the chat for details.", locale=locale)
    text = user_message(result, locale=locale)
    if text.lstrip().startswith(('{','[')):
        import json
        try:
            if isinstance(json.loads(text),(dict,list)):
                return message("The structured result is shown in chat.", locale=locale)
        except (ValueError,TypeError):
            pass
    text = re.sub(r"```.*?(?:```|$)", message(" Code is shown in chat. ", locale=locale), text, flags=re.S)
    text = re.sub(r"`[^`]+`", message(" excerpt in chat ", locale=locale), text)
    text = re.sub(r"(?m)^\s*\|.*$", "", text)
    text = re.sub(r"https?://\S+", message("link in chat", locale=locale), text)
    text = re.sub(r"[A-Za-z]:[\\/][^\n]+", message("path shown in chat", locale=locale), text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        limit = max_chars - 100
        end = max(text.rfind(". ", 0, limit), text.rfind("! ", 0, limit), text.rfind("? ", 0, limit))
        text = text[:end+1] if end > 0 else text[:limit].rsplit(" ", 1)[0]
        text += message(" The full response is in chat.", locale=locale)
    return text
