from unittest.mock import patch
import pytest
from local_mode.local_chat import LocalChat

@pytest.mark.parametrize('model,projector,mode', [('', '', 'vulkan'), ('missing.gguf','','cpu'), (__file__,'','cpu'), ('x','','cuda')])
def test_invalid_model_never_starts(model,projector,mode):
    with patch('local_mode.local_chat.subprocess.Popen') as launch:
        with pytest.raises((ValueError,OSError)):
            LocalChat().start(model,projector,mode)
        launch.assert_not_called()

def test_stop_does_not_touch_foreign_process():
    chat=LocalChat()
    assert not chat.stop()['running']
    assert chat.status()['baseURL'].startswith('http://127.0.0.1:')
