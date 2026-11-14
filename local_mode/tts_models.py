"""Managed synthesis weights; selecting weights never changes the chosen speaker."""
from __future__ import annotations
import json
import os
from pathlib import Path
from local_mode.config import ROOT


def settings_path() -> Path:
    return ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data") / "tts-model.json"


def model_configuration() -> tuple[Path, str, str]:
    """Revalidate selected assets before reuse; no hashing or downloads on discovery."""
    from local_mode.audio_cpp_install import MODEL
    path = settings_path()
    if not path.exists():
        return MODEL, "vulkan", "builtin"
    if path.stat().st_size > 16384:
        raise ValueError("Invalid TTS model settings")
    selection = json.loads(path.read_text(encoding="utf8"))
    if not isinstance(selection, dict) or selection.get("mode") not in {"vulkan", "cpu"}:
        raise ValueError("Invalid TTS model selection")
    from local_mode.model_library import validate_selected_model
    derived = validate_selected_model(selection, "tts")
    identity = json.dumps(selection, sort_keys=True, separators=(",", ":"))
    return Path(derived["model_path"]), selection["mode"], identity


def refresh_engine(engines: dict, identity: str, previous: str | None) -> str:
    """Called between utterances on the owning speech thread, never mid-playback."""
    if previous is not None and previous != identity:
        engine = engines.pop("supertonic", None)
        if engine is not None:
            engine.close()
    return identity


def status() -> dict:
    _, mode, identity = model_configuration()
    selection = {} if identity == "builtin" else json.loads(identity)
    return {"family": "supertonic", "managed": identity != "builtin", "mode": mode,
            "repo_id": selection.get("repo_id", "Built-in Supertonic 3"),
            "revision": selection.get("revision", "")}


def prepare_engine(engines: dict, previous: str | None) -> tuple[str, str | None]:
    """Invalid selection retires the cached engine and leaves CPU fallback available."""
    try:
        _, _, identity = model_configuration()
    except (OSError, ValueError, TypeError) as error:
        engine = engines.pop("supertonic", None)
        if engine is not None:
            engine.close()
        engines["supertonic"] = None
        return "invalid-selection", str(error)
    return refresh_engine(engines, identity, previous), None
