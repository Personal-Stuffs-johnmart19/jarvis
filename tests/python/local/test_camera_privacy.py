"""Камера включается только явным RPC выбранного доступного устройства."""
from unittest.mock import patch, Mock
import pytest
from local_mode.camera_devices import CameraDevice
from local_mode.desktop_host import DesktopRuntime


def test_camera_permission_is_explicit_and_resettable():
    value=DesktopRuntime.__new__(DesktopRuntime);value.preview=False;value.event=Mock()
    device=CameraDevice(702,'USB Camera',700)
    with patch('local_mode.camera_devices.list_cameras',return_value=[device]),patch('local_mode.camera_devices.select_camera') as select:
        value.dispatch('camera.list',{})
        select.assert_not_called()
        with pytest.raises(ValueError):value.dispatch('camera.allow',{'enabled':True,'index':999})
        select.assert_not_called()
        value.dispatch('camera.allow',{'enabled':True,'index':702})
        select.assert_called_once_with(device)
        value.dispatch('camera.allow',{'enabled':False})
        select.assert_called_with(None)
