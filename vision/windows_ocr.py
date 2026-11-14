"""Локальный Windows OCR для кадров OpenCV; без загрузки экрана/камеры."""
from __future__ import annotations
import asyncio
from concurrent.futures import ThreadPoolExecutor
from time import time
from typing import Any
from .vision_events import VisionEvent


def languages() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine
    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


async def _read(frame: Any, language: str) -> list[VisionEvent]:
    import cv2
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode
    from winrt.windows.security.cryptography import CryptographicBuffer
    available = languages()
    chosen = next((lang for lang in available if lang.lower().startswith("ru")), None)
    engine = OcrEngine.try_create_from_language(Language(chosen)) if chosen else OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows OCR: нет языкового пакета. Установите русский язык Windows.")
    bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
    height, width = bgra.shape[:2]
    bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.IGNORE)
    bitmap.copy_from_buffer(CryptographicBuffer.create_from_byte_array(bgra.tobytes()))
    try:
        result = await engine.recognize_async(bitmap)
        events = []
        for line in result.lines:
            for word in line.words:
                r = word.bounding_rect
                events.append(VisionEvent(type="text_detected", timestamp=time(), label=word.text,
                    confidence=1.0, bbox=(int(r.x), int(r.y), int(r.width), int(r.height)),
                    metadata={"language": chosen or "profile", "engine": "windows-ocr", "confidence_available": False}))
        return events
    finally:
        bitmap.close()


def extract(frame: Any, language: str = "rus+eng") -> list[VisionEvent]:
    # WinRT coroutine не должна пересекаться с event loop браузера.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_read(frame, language))).result(timeout=30)
