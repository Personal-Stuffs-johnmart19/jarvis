"""One confirmed local-model screen frame; no inference, disk writes or camera."""
from __future__ import annotations
import base64
import io
import os
from local_mode.messages import OwnedRuntimeError, OwnedValueError

DESTINATION = "http://127.0.0.1:1234"
MAX_JPEG_BYTES = 500_000


def _grab_monitor(index: int):
    import mss
    from PIL import Image
    with mss.mss() as session:
        if index >= len(session.monitors):
            raise OwnedValueError("Screen monitor is unavailable.")
        monitor = dict(session.monitors[index])
        if not 0 < monitor["width"] * monitor["height"] <= 64_000_000:
            raise OwnedValueError("Screen dimensions exceed the capture limit.")
        shot = session.grab(monitor)
        return Image.frombytes("RGB", shot.size, shot.rgb), monitor


def capture_screen(destination: str, monitor: int = 1) -> dict:
    if destination != DESTINATION:
        raise OwnedValueError("Screen capture is restricted to local LM Studio at 127.0.0.1:1234.")
    if type(monitor) is not int or not 1 <= monitor <= 16:
        raise OwnedValueError("Screen monitor must be an integer from 1 to 16.")
    if os.environ.get("JARVIS_DESKTOP_TEST") == "1":
        raise OwnedRuntimeError("Personal screen capture is disabled in QA.")
    image, geometry = _grab_monitor(monitor)
    from PIL import Image
    image = image.convert("RGB")
    image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    for edge, quality in ((1280, 65), (1280, 45), (960, 45)):
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality)
        data = output.getvalue()
        if len(data) <= MAX_JPEG_BYTES:
            break
    else:
        raise OwnedRuntimeError("Screen image exceeds the transport limit.")
    return {
        "image": base64.b64encode(data).decode("ascii"),
        "monitor": monitor, "image_width": image.width, "image_height": image.height,
        "screen_left": geometry["left"], "screen_top": geometry["top"],
        "screen_width": geometry["width"], "screen_height": geometry["height"],
    }
