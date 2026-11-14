"""Strict private RPC boundary for managed local chat runtimes."""
from __future__ import annotations


def dispatch(method: str, params: dict, manager):
    fields = {
        "managed_chat.list": set(),
        "managed_chat.resolve": {"model_id", "request_id"},
        "managed_chat.cancel": {"request_id"},
        "managed_chat.unload": {"model_id"},
    }
    if method not in fields or not isinstance(params, dict) or set(params) != fields[method]:
        raise ValueError("Invalid managed chat operation or arguments")
    if "model_id" in params:
        value = params["model_id"]
        if not isinstance(value, str) or not value.startswith("jarvis-managed:") or len(value) > 512:
            raise ValueError("Invalid managed model identifier")
    if "request_id" in params:
        value = params["request_id"]
        if not isinstance(value, str) or not 1 <= len(value) <= 128 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
            raise ValueError("Invalid managed request identifier")
    if method == "managed_chat.list":
        return {"models": manager.list_models()}
    if method == "managed_chat.resolve":
        return manager.resolve(**params)
    if method == "managed_chat.cancel":
        return manager.cancel(params["request_id"])
    return manager.unload(params["model_id"])
