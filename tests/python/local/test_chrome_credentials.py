"""Токены Chrome: DPAPI, ввод в интерфейсе и отсутствие утечек в выводе MCP."""
from unittest.mock import patch
import pytest
from local_mode import chrome_credentials as credentials
from local_mode.chrome_mcp import ChromeSession

TOKEN="synthetic_playwright_test_token_123456789"


@pytest.mark.parametrize("value",[TOKEN,"PLAYWRIGHT_MCP_EXTENSION_TOKEN="+TOKEN,'PLAYWRIGHT_MCP_EXTENSION_TOKEN="'+TOKEN+'"'])
def test_accepts_raw_token_and_assignment(value):
    assert credentials.normalize_token(value)==TOKEN


def test_dpapi_is_encrypted_and_profile_scoped(tmp_path,monkeypatch):
    monkeypatch.setattr(credentials,"DIRECTORY",tmp_path)
    credentials.save_token("Profile 2",TOKEN)
    assert credentials.load_token("Profile 2")==TOKEN
    assert TOKEN.encode() not in credentials.token_path("Profile 2").read_bytes()
    assert credentials.load_token("Default") is None
    assert credentials.load_token("Profile 2","msedge") is None
    credentials.delete_token("Profile 2")
    assert not credentials.has_token("Profile 2")


@pytest.mark.parametrize("value",["bad","x\ncommand", "export PLAYWRIGHT_MCP_EXTENSION_TOKEN="+TOKEN])
def test_invalid_token_is_not_echoed(value):
    with pytest.raises(ValueError) as error:credentials.normalize_token(value)
    assert value not in str(error.value)


def test_connect_url_and_error_tokens_are_redacted():
    session=ChromeSession.__new__(ChromeSession)
    session._secret=TOKEN
    text=session.redact("https://example/?token="+TOKEN+"&x=1 " + TOKEN)
    assert TOKEN not in text and "[redacted]" in text




def test_missing_token_requests_in_app_instead_of_chrome_popup():
    from local_mode import chrome_mcp
    with patch.object(chrome_mcp,"installed",return_value=True), patch.object(credentials,"load_token",return_value=None), patch.object(chrome_mcp,"ChromeSession") as launch:
        result=chrome_mcp.execute("Profile 2",{"action":"snapshot"})
        assert result["setup_kind"]=="chrome_token" and result["profile_id"]=="Profile 2"
        assert result["requires_setup"]
        launch.assert_not_called()


def test_model_tools_cannot_read_chrome_dpapi_files():
    from local_mode.tools import ToolRegistry
    with pytest.raises(ValueError,match="cannot access keys"):
        ToolRegistry().execute("file_controller",{"action":"read","path":str(credentials.token_path("Profile 2"))})
