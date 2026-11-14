"""Synthetic pixels only: never capture the personal desktop or call a model."""
import base64
import io
from unittest.mock import Mock
import pytest
from PIL import Image
from local_mode import screen_capture as screen


def test_capture_is_bounded_and_preserves_desktop_geometry(monkeypatch):
    monkeypatch.delenv("JARVIS_DESKTOP_TEST", raising=False)
    image = Image.new("RGB", (2560, 1440), "red")
    grab = Mock(return_value=(image, {"left":-2560,"top":120,"width":2560,"height":1440}))
    monkeypatch.setattr(screen, "_grab_monitor", grab)
    result = screen.capture_screen(screen.DESTINATION, 2)
    grab.assert_called_once_with(2)
    raw = base64.b64decode(result["image"], validate=True)
    assert len(raw) <= screen.MAX_JPEG_BYTES
    assert Image.open(io.BytesIO(raw)).size == (1280,720)
    assert result["screen_left"] == -2560 and result["screen_top"] == 120
    assert result["screen_width"] == 2560 and result["image_width"] == 1280


@pytest.mark.parametrize("destination,monitor", [("https://example.com",1), (screen.DESTINATION,True), (screen.DESTINATION,0), (screen.DESTINATION,17)])
def test_invalid_capture_is_rejected_before_grabbing(monkeypatch,destination,monitor):
    grab = Mock()
    monkeypatch.setattr(screen, "_grab_monitor", grab)
    with pytest.raises(ValueError):
        screen.capture_screen(destination,monitor)
    grab.assert_not_called()


def test_qa_and_native_dispatch_cannot_capture_personal_screen(monkeypatch):
    from local_mode.desktop_host import DesktopRuntime
    monkeypatch.setenv("JARVIS_DESKTOP_TEST","1")
    grab = Mock()
    monkeypatch.setattr(screen,"_grab_monitor",grab)
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    with pytest.raises(RuntimeError,match="disabled in QA"):
        runtime.dispatch("screen.capture",{"destination":screen.DESTINATION})
    with pytest.raises(ValueError,match="Unexpected"):
        runtime.dispatch("screen.capture",{"destination":screen.DESTINATION,"path":"private"})
    grab.assert_not_called()
