"""Музыка основного Chrome: Gemini выбирает видео, приложение проверяет результат."""
import json
import threading
from unittest.mock import Mock, patch
import pytest
from google.genai import types
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.chrome_mcp import ChromeSession


def response(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model",parts=list(parts)))])


@pytest.mark.parametrize("verified",[True,False])
def test_exact_music_command_uses_gemini_and_cannot_fake_success(verified):
    with patch("local_mode.browser_profiles.read_preferences",return_value={"mode":"specific","profile":"Profile 2"}),patch("local_mode.assistant.WindowController"),patch("local_mode.assistant.YouTubePlayer") as youtube,patch("local_mode.assistant.gemini.create_client") as create,patch("local_mode.assistant.gemini.generate",return_value=response(types.Part(text="Музыка включена!"))) as generate:
        bot=LocalAgent(Settings())
        with patch.object(bot.tools,"execute",return_value={"ok":True,"verified":verified,"media":{"paused":False}}) as execute:
            result=bot.run("включи музыку на YouTube")
        create.assert_called_once();generate.assert_called_once()
        youtube.return_value.play.assert_not_called()
        execute.assert_called_once_with("browser_control",{"action":"media_state","profile_id":"Profile 2"})
        assert result["ok"] is verified and result["verified"] is verified
        if not verified:assert "Could not verify" in result["reply"]


@pytest.mark.parametrize("overrides,expected",[({},True),({"muted":True},False),({"ad_showing":True},False),({"paused":True},False),({"current_time":1},False)])
def test_media_verification_requires_progress_sound_and_no_ads(overrides,expected):
    first={"url":"https://www.youtube.com/watch?v=test","video":True,"current_time":1,"paused":False,"muted":False,"volume":.25,"ready_state":4,"ended":False,"ad_showing":False,"error":None}
    second={**first,"current_time":2,**overrides}
    session=ChromeSession.__new__(ChromeSession);session.profile="Profile 2"
    session.tool=Mock(side_effect=[{"ok":True,"text":"### Result\n"+json.dumps(first)},{"ok":True,"text":"### Result\n"+json.dumps(second)}])
    with patch("local_mode.chrome_mcp.time.sleep"):
        assert session.media_state()["verified"] is expected


def test_ad_wait_is_local_and_restores_content_playback():
    session=ChromeSession.__new__(ChromeSession);session.profile="Profile 2";session.lock=threading.RLock()
    session.tool=Mock(return_value={"ok":True})
    session.media_state=Mock(side_effect=[
        {"ok":True,"verified":False,"media":{"ad_showing":True}},
        {"ok":True,"verified":False,"media":{"ad_showing":False,"muted":True}},
        {"ok":True,"verified":True,"media":{"ad_showing":False,"muted":False}},
    ])
    with patch("local_mode.chrome_mcp.time.sleep"):
        assert session.execute({"action":"media_play"})["verified"]
    assert session.media_state.call_count==3
    assert session.tool.call_count==3
    assert all("v.muted=false" in call.args[1]["function"] for call in session.tool.call_args_list)
