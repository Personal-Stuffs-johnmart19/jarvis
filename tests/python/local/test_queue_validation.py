import pytest
from local_mode.tools import ToolRegistry

def test_optional_empty_urls_do_not_break_other_browser_actions():
    registry=ToolRegistry()
    registry.validate("browser_control",{"action":"queue_list","urls":[]})
    with pytest.raises(ValueError,match="No video URLs"):
        registry.validate("browser_control",{"action":"queue_add","urls":[]})
    registry.validate("browser_control",{"action":"queue_add","urls":["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]})
