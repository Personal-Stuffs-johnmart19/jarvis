# telegram_bot.py
# Jarvis — Telegram remote-control bot.
#
# Plain HTTP long-polling (no extra deps). Only the owner's chat_id is allowed.
# Supports text commands and voice messages (transcribed via Gemini).

import io
import json
import sys
import time
from pathlib import Path

try:
    import requests
    _REQUESTS = True
except ImportError:
    _REQUESTS = False

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TRANSCRIBE_MODEL = "local-vosk-ru"

_last_update = 0


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def load_config() -> dict:
    path = get_base_dir() / "config" / "api_keys.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def api_call(token: str, method: str, json_data: dict | None = None,
             params: dict | None = None, timeout: int = 40) -> dict | None:
    if not _REQUESTS:
        return None
    url = TELEGRAM_API.format(token=token, method=method)
    try:
        if json_data is not None:
            resp = requests.post(url, json=json_data, timeout=timeout)
        else:
            resp = requests.get(url, params=params, timeout=timeout)
        data = resp.json()
        return data if data.get("ok") else None
    except Exception:
        return None


def get_updates(token: str, offset: int, timeout: int = 30) -> list[dict]:
    """Long-poll for new updates. Returns a list of update dicts."""
    data = api_call(token, "getUpdates",
                    params={"offset": offset, "timeout": timeout,
                            "allowed_updates": ["message", "callback_query"]},
                    timeout=timeout + 10)
    return (data or {}).get("result") or []


def send_message(token: str, chat_id, text: str, parse_mode: str = "HTML",
                 reply_markup: dict | None = None) -> dict | None:
    """Send a message (HTML by default). Returns the sent message dict (to
    grab message_id for later edits) or None on failure."""
    if not text:
        return None
    last = None
    chunks = _split_text(text)
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup and i == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        last = api_call(token, "sendMessage", payload)
    return last


def send_typing(token: str, chat_id) -> None:
    """Tell Telegram we're working (typing action bubble)."""
    api_call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})


def send_photo(token: str, chat_id, image_bytes: bytes,
               caption: str = "", parse_mode: str = "HTML") -> None:
    """Send a photograph (bytes) with an optional HTML caption."""
    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token, method="sendPhoto"),
            data={"chat_id": chat_id, "caption": caption,
                  "parse_mode": parse_mode},
            files={"photo": ("shot.png", image_bytes,
                             "image/png")},
            timeout=40,
        )
        resp.json()
    except Exception:
        pass


def edit_message(token: str, chat_id, message_id, text: str,
                 parse_mode: str = "HTML",
                 reply_markup: dict | None = None) -> bool:
    """Edit an already-sent message in place (the '⏳…' → final answer flow)."""
    if not text or not message_id:
        return False
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return bool(api_call(token, "editMessageText", payload))


def answer_callback(token: str, callback_id: str, text: str | None = None) -> None:
    """Acknowledge an inline-button press so Telegram stops spinners."""
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    api_call(token, "answerCallbackQuery", payload)


def html_escape(text: str) -> str:
    """Escape text safely for Telegram HTML parse mode."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _split_text(text: str, limit: int = 3800) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts = []
    while text:
        cut = text[:limit]
        if len(text) > limit:
            idx = max(cut.rfind("\n"), cut.rfind(". "), cut.rfind("; "))
            if idx > limit // 2:
                cut, text = cut[:idx + 1], text[idx + 1:].lstrip()
            else:
                text = text[limit:]
        else:
            text = ""
        parts.append(cut)
    return parts


def download_file(token: str, file_id: str) -> bytes | None:
    """Download a Telegram file (e.g. a voice message) as bytes."""
    data = api_call(token, "getFile", {"file_id": file_id})
    if not data:
        return None
    path = data.get("result", {}).get("file_path")
    if not path:
        return None
    try:
        resp = requests.get(
            f"https://api.telegram.org/file/bot{token}/{path}", timeout=40)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def transcribe_voice(api_key: str, audio_bytes: bytes) -> str:
    """OGG/Opus Telegram → PCM16 → Vosk; API-ключ оставлен для совместимости."""
    import json
    import av
    from local_mode.config import Settings
    from local_mode.speech import RussianRecognizer
    if len(audio_bytes) > 20 * 1024 * 1024:
        raise ValueError("Голосовое сообщение больше 20 МиБ.")
    # PyAV 19 изменил API open(): не используем устаревший metadata_errors
    # из helper старой версии faster-whisper. Здесь также ограничиваем PCM
    # во время декодирования, а не после выделения неограниченной памяти.
    chunks = []
    size = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(io.BytesIO(audio_bytes), mode="r") as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                block = converted.to_ndarray().tobytes()
                size += len(block)
                if size > 16000 * 2 * 180:
                    raise ValueError("Голосовое сообщение длиннее трёх минут.")
                chunks.append(block)
        for converted in resampler.resample(None):
            chunks.append(converted.to_ndarray().tobytes())
    data = b"".join(chunks)
    recognizer = RussianRecognizer(Settings.from_env().vosk_model)
    decoder = recognizer._decoder(16000)
    if recognizer.engine == "whisper":
        return recognizer._accurate(data, 16000)
    texts = []
    for offset in range(0, len(data), 8000):
        if decoder.AcceptWaveform(data[offset:offset + 8000]):
            texts.append(json.loads(decoder.Result()).get("text", ""))
    texts.append(json.loads(decoder.FinalResult()).get("text", ""))
    return " ".join(t for t in texts if t).strip()


def ping_bot(token: str) -> bool:
    """Verify the token works."""
    data = api_call(token, "getMe")
    return bool(data and data.get("result"))


BOT_COMMANDS = [
    {"command": "start",       "description": "Boshlash / yordam"},
    {"command": "help",        "description": "Yordam va misollar"},
    {"command": "status",      "description": "Jarvis statusi (mute/holat)"},
    {"command": "mode",        "description": "Rejimni o'zgartirish (girlfriend, butler...)"},
    {"command": "voice",       "description": "Ovozni o'zgartirish (Gemini ovozlari)"},
    {"command": "mute",        "description": "Mikrofonni o'chirish"},
    {"command": "unmute",      "description": "Mikrofonni yoqish"},
    {"command": "screenshot",  "description": "Ekran rasmini yuborish"},
]


def set_bot_commands(token: str) -> bool:
    """Register the /-command list in Telegram's bot menu (setMyCommands)."""
    data = api_call(token, "setMyCommands",
                    {"commands": BOT_COMMANDS})
    return bool(data and data.get("ok"))


def mark_read(token: str, offset: int):
    """Confirm an update offset so Telegram stops re-delivering it."""
    api_call(token, "getUpdates", params={"offset": offset, "timeout": 1})
