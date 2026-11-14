"""Настоящие DOM-действия проверяются отдельным headless smoke; здесь — границы агента."""
from unittest.mock import patch
from google.genai import types
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.tools import declarations


def test_browser_schema_exposes_tabs_snapshot_and_refs():
    tool = next(d for d in declarations() if d.name == "browser_control")
    assert "ref" in tool.parameters_json_schema["properties"]
    assert "tab_id" in tool.parameters_json_schema["properties"]
    assert "new_tab" in tool.parameters_json_schema["properties"]["action"]["description"]


def test_last_tool_result_is_preserved_at_step_limit():
    response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="web_search", args={"query":"test"}, id="one"))]))])
    with patch("local_mode.assistant.WindowController"), patch("local_mode.assistant.YouTubePlayer"), patch("local_mode.assistant.gemini.create_client"), patch("local_mode.assistant.gemini.generate", return_value=response):
        bot = LocalAgent(Settings(max_rounds=1))
        with patch.object(bot.tools, "execute", return_value={"ok":True,"result":"Нашёл страницу"}) as execute:
            result = bot.run("Найди страницу")
        assert result["partial"] and result["action"] == "step_limit"
        assert result["results"][0]["result"] == "Нашёл страницу"
        assert bot.history[-1].parts[0].function_response.id == "one"
        execute.assert_called_once()


def test_personal_browser_does_not_enable_debugging_automatically(tmp_path):
    from local_mode import browser_profiles as bp
    from local_mode.browser_api import BrowserAPI
    with patch.object(bp, "read_preferences", return_value={"mode":"current", "profile":""}), patch.object(bp, "user_data", return_value=tmp_path), patch.object(bp,"profiles",return_value=([{"id":"Default"}],"Default")), patch("local_mode.chrome_mcp.installed",return_value=False), patch.object(bp.subprocess, "Popen") as launch:
        result = BrowserAPI()({"action":"snapshot"})
        assert result["requires_setup"]
        assert "Playwright Extension" in result["reply"]
        launch.assert_not_called()


def test_explicit_profile_cannot_attach_to_another_profile():
    from local_mode import browser_profiles as bp
    import pytest
    with patch.object(bp,"read_preferences",return_value={"mode":"specific","profile":"Profile 2"}), patch.object(bp,"profiles",return_value=([{"id":"Default"},{"id":"Profile 2"}],"Default")):
        from local_mode.messages import OwnedValueError, message
        with pytest.raises(OwnedValueError, match="Connecting to another profile is not allowed") as caught:
            bp.control_endpoint()
        error = caught.value
        assert str(error) == message(error.template, *error.values, locale="en")
        assert "другому профилю" in message(error.template, *error.values, locale="ru")
