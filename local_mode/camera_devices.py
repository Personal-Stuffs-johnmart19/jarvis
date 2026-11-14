"""Selecting a camera does not capture frames; permission lasts until exit."""
from __future__ import annotations
from dataclasses import dataclass
import threading
from local_mode.messages import OwnedRuntimeError

@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str
    backend: int

_lock = threading.Lock()
_selected: CameraDevice | None = None


def list_cameras() -> list[CameraDevice]:
    # DirectShow перечисляет устройства без VideoCapture и чтения кадров.
    import cv2
    from cv2_enumerate_cameras import enumerate_cameras
    return [CameraDevice(item.index, item.name, item.backend)
            for item in enumerate_cameras(cv2.CAP_DSHOW)]


def select_camera(device: CameraDevice | None) -> None:
    global _selected
    with _lock:
        _selected = device


def selected_camera() -> CameraDevice:
    with _lock:
        device = _selected
    if device is None:
        raise OwnedRuntimeError('Camera disabled. Allow access in Settings → Advanced Settings → Camera.')
    return device
