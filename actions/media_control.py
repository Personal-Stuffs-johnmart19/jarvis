"""Compatibility adapter for the selected browser player; no global media keys."""

_ACTIONS = {
    "play": "media_resume", "pause": "media_pause", "stop": "media_pause",
    "next": "queue_next", "skip": "queue_next",
    "volume_up": "media_volume_up", "vol_up": "media_volume_up",
    "volume_down": "media_volume_down", "vol_down": "media_volume_down",
    "mute": "media_mute", "unmute": "media_unmute",
}


def media_control_action(parameters: dict, response=None, player=None, session_memory=None):
    from actions.browser_control import browser_control

    action = str((parameters or {}).get("action", "")).strip().lower()
    if action in {"toggle", "play_pause"}:
        state = browser_control({**parameters, "action": "media_state"})
        if not state.get("ok") or not state.get("media", {}).get("video"):
            return {"ok": False, "error": "Select a browser player first."}
        action = "play" if state["media"].get("paused") else "pause"
    mapped = _ACTIONS.get(action)
    if not mapped:
        return {"ok": False, "error": "Choose an explicit player action: play, pause, next (Jarvis queue), volume_up, volume_down, mute or unmute."}
    return browser_control({**parameters, "action": mapped})
