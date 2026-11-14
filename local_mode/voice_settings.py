"""Стабильные идентификаторы голосов и атомарные предпочтения озвучки."""
from __future__ import annotations
import json
import os
from local_mode.config import ROOT
from local_mode.storage import write_json
from local_mode.messages import OwnedValueError

DEFAULT_VOICE = "supertonic:F1"
GEMINI_VOICES = {"auto": "Automatic: Lite for short / Flash for long responses",
    "flash": "3.8 Flash", "lite": "3.8 Flash-Lite", "preview": "3.1 Flash Preview"}
SILERO_SPEAKERS = {"xenia": "Xenia", "baya": "Baya", "kseniya": "Kseniya",
                   "aidar": "Aidar", "eugene": "Eugene"}
SUPERTONIC_SPEAKERS = tuple(f"{gender}{number}" for gender in ("F", "M") for number in range(1, 6))
PREFERENCES = ROOT / (".cache/desktop-test" if os.getenv("JARVIS_DESKTOP_TEST") == "1" else "data") / "voice-preferences.json"


def validate_voice(voice: str) -> str:
    if voice == "qwen3:anime-female":
        return voice
    if voice.startswith("piper:managed:"):
        from local_mode.piper_models import resolve
        resolve(voice)
        return voice
    if (voice in {"gemini:" + name for name in GEMINI_VOICES} or voice in {"silero:" + name for name in SILERO_SPEAKERS} or voice == "piper:denis"
            or voice in {"supertonic:" + name for name in SUPERTONIC_SPEAKERS}):
        return voice
    if voice.startswith("sapi:") and 5 < len(voice) <= 1024 and not any(ord(char) < 32 for char in voice):
        return voice
    raise OwnedValueError("Unknown voice. Choose a voice from the list.")


def read_voice() -> str:
    if not PREFERENCES.exists():
        return DEFAULT_VOICE
    data = json.loads(PREFERENCES.read_text(encoding="utf-8-sig"))
    voice = validate_voice(data["voice"])
    # Старые установки переводим на автоматический GPU/CPU без ручного выбора.
    # Новый явный выбор пользователя сохраняется в формате версии 2.
    if data.get("version", 1) == 1 and not voice.startswith("supertonic:"):
        return DEFAULT_VOICE
    return voice


def save_voice(voice: str) -> None:
    write_json(PREFERENCES, {"version": 2, "voice": validate_voice(voice)})


def neural_voices() -> list[dict[str, str]]:
    from local_mode.piper_models import voices as managed_piper_voices
    from local_mode.qwen_voice import VOICE_ID, VOICE_NAME
    return [{"id": VOICE_ID, "name": VOICE_NAME, "language": "en,ru"}] + managed_piper_voices() + [{"id": "gemini:" + key, "name": "Gemini TTS — " + name + " (cloud)",
             "language": "419"} for key, name in GEMINI_VOICES.items()] + [{"id": "supertonic:" + key,
             "name": "Supertonic 3 — " + ("Female " if key.startswith("F") else "Male ") + key + " (Vulkan / automatic GPU)",
             "language": "419"} for key in SUPERTONIC_SPEAKERS] + [{"id": "silero:" + key, "name": "Silero — " + name, "language": "419"}
            for key, name in SILERO_SPEAKERS.items()] + [
            {"id": "piper:denis", "name": "Piper — Denis", "language": "419"}]


def gemini_tts_configured() -> bool:
    """Check actual shared credential values locally, never key-file existence alone."""
    from api_keys import get_all_keys
    from local_mode.credentials import load_key
    try:
        if any(isinstance(key, str) and key.strip() for key in get_all_keys()):
            return True
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
        pass
    # A malformed legacy/environment catalogue must not mask an independently
    # usable encrypted/bootstrap credential in the shared credential store.
    try:
        key = load_key()
        return isinstance(key, str) and bool(key.strip())
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError):
        return False


def available_neural_voices() -> list[dict[str, str]]:
    """Local engines support lazy assets; cloud choices require a configured key.

    This is configuration readiness, not a network/quota/remote model probe.
    Keep neural_voices as the complete metadata source for saved selection labels.
    """
    configured = gemini_tts_configured()
    from local_mode.qwen_voice import available as qwen_available
    return [voice for voice in neural_voices()
            if (configured or not voice["id"].startswith("gemini:"))
            and (not voice["id"].startswith("qwen3:") or qwen_available())]
