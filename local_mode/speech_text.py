"""Безопасная подготовка речи: предложения, паузы и поддерживаемая просодия."""
from __future__ import annotations
from dataclasses import dataclass
from html import escape
import re
import textwrap


@dataclass(frozen=True)
class SpeechPart:
    text: str
    pause_ms: int = 0


def speech_parts(text: str, limit: int = 160) -> list[SpeechPart]:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)", "", text)
    text = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\ufe0f\u200d]", "", text)
    text = re.sub(r"[`*_#]", "", text).replace("\r\n", "\n").strip()
    result: list[SpeechPart] = []
    def emit(value: str, pause: int) -> None:
        if not any(char.isalnum() for char in value): return
        pieces = textwrap.wrap(value, width=limit, break_on_hyphens=False)
        for index, piece in enumerate(pieces):
            result.append(SpeechPart(piece, pause if index == len(pieces)-1 else 80))
    start = index = 0
    while index < len(text):
        if text[index] == "\n":
            end = index + 1
            while end < len(text) and text[end].isspace(): end += 1
            pause = 500 if text[index:end].count("\n") > 1 else 320
            emit(text[start:index], pause)
            if result:
                result[-1] = SpeechPart(result[-1].text, max(result[-1].pause_ms, pause))
            start = index = end
            continue
        if text[index] in ".!?…":
            end = index + 1
            while end < len(text) and text[end] in '.!?…»”")': end += 1
            value = text[start:end].strip()
            abbreviated = bool(re.search(r"(?:\b(?:т|е|д|п|г|ул|им|стр|др|руб|мин|сек)|\b[А-ЯA-Z])\.$", value))
            if (end == len(text) or text[end].isspace()) and not abbreviated:
                pause = 420 if value.rstrip('»”")').endswith(("…", "...")) else 240
                emit(value, pause)
                start = end
            index = end
        else:
            index += 1
    emit(text[start:], 0)
    if result:
        result[-1] = SpeechPart(result[-1].text, 0)
    return result


def speech_speed(text: str) -> float:
    end = text.rstrip('»”") ')
    return 0.94 if end.endswith(("…", "...")) else 0.98 if end.endswith("?") else 1.03 if end.endswith("!") else 1.0


def silero_ssml(text: str) -> str:
    end = text.rstrip('»”") ')
    rate = "slow" if end.endswith(("…", "...")) else "medium"
    pitch = "high" if end.endswith("!") else "medium"
    return f'<speak><prosody rate="{rate}" pitch="{pitch}">{escape(text)}</prosody></speak>'


def sapi_xml(text: str) -> str:
    pieces = []
    for part in speech_parts(text, 240):
        pitch = 1 if part.text.rstrip('»”") ').endswith("!") else 0
        rate = -1 if speech_speed(part.text) < 1 else 0
        pieces.append(f'<pitch middle="{pitch}"><rate speed="{rate}">{escape(part.text)}</rate></pitch>')
        if part.pause_ms: pieces.append(f'<silence msec="{part.pause_ms}"/>')
    return '<pitch middle="0">' + ''.join(pieces) + '</pitch>'
