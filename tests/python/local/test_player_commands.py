"""Команды плеера без глобальных клавиш, кликов и живого аккаунта."""
from unittest.mock import Mock,patch
import pytest
from local_mode.media_player import player_request,control_plan,verify_control,control_player
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.tools import ToolRegistry


@pytest.mark.parametrize("command,action",[
 ("Используй плеер на полный экран","media_fullscreen"),
 ("поставь видео на паузу","media_pause"),("продолжи видео","media_resume"),
 ("сделай видео громче","media_volume_up"),("сделай видео тише","media_volume_down"),
 ("выключи звук видео","media_mute"),("включи звук видео","media_unmute"),
 ("выключи полноэкранный режим","media_exit_fullscreen"),
 ("покажи очередь","queue_list"),("следующее видео","queue_next")])
def test_player_commands_use_verified_browser_api_without_gemini(command,action):
 with patch("local_mode.assistant.WindowController"),patch("local_mode.assistant.YouTubePlayer"),patch("local_mode.assistant.gemini.create_client") as model:
  bot=LocalAgent(Settings())
  with patch.object(bot.tools,"execute",return_value={"ok":True,"verified":True}) as execute:
   assert bot.run(command)["verified"]
  assert execute.call_args.args==("browser_control",{"action":action})
  model.assert_not_called()


@pytest.mark.parametrize("text",["не поставь видео на паузу","не делай видео громче","расскажи о полноэкранном режиме"])
def test_negations_and_explanations_do_not_execute(text):assert player_request(text) is None


def state(**overrides):
 return {"url":"https://www.youtube.com/watch?v=abcdefghijk","source":"blob:video1","video":True,"paused":False,"volume":.4,"muted":False,"duration":60,"current_time":20,"fullscreen":False,**overrides}


def test_pause_requires_independent_state_not_command_report():
 evaluate=Mock(side_effect=[state(),{"requested":True},state(paused=False)])
 result=control_player(evaluate,"media_pause",{})
 assert not result["ok"] and not result["verified"] and evaluate.call_count==3


@pytest.mark.parametrize("action,args,target",[("media_volume_up",{},.5),("media_volume_down",{},.3),("media_volume_up",{"step":100},1),("media_volume_down",{"step":100},0)])
def test_volume_clamps_at_valid_bounds(action,args,target):
 script,expected=control_plan(action,args,state())
 assert abs(expected["volume"]-target)<.001
 assert verify_control(action,expected,state(),state(volume=target))["verified"]


@pytest.mark.parametrize("args",[{"volume":101},{"volume":-1},{"volume":True},{"volume":float("nan")}])
def test_invalid_values_cannot_reach_javascript(args):
 with pytest.raises(ValueError):control_plan("media_volume",args,state())


def test_change_of_video_or_failed_fullscreen_is_not_success():
 assert not verify_control("media_fullscreen",{"fullscreen":True},state(),state())["verified"]
 assert not verify_control("media_pause",{"paused":True},state(),state(paused=True,source="blob:other"))["verified"]


def test_schema_validates_volume_and_queue_parameters():
 registry=ToolRegistry()
 registry.validate("browser_control",{"action":"media_volume","volume":40})
 registry.validate("browser_control",{"action":"queue_add","urls":["https://youtu.be/abcdefghijk"]})
 from jsonschema import ValidationError
 with pytest.raises(ValidationError):registry.validate("browser_control",{"action":"media_volume","volume":150})


def test_pause_cancels_pending_ad_wait_without_consuming_model_steps():
 import threading
 from local_mode.chrome_mcp import ChromeSession
 session=ChromeSession.__new__(ChromeSession);session.lock=threading.RLock()
 session._media_wait_cancel=threading.Event();session.media_state=Mock()
 def call(*args):
  session._media_wait_cancel.set()
  return {"ok":True}
 session.tool=Mock(side_effect=call)
 result=session.execute({"action":"media_play"})
 assert result["cancelled"] and not result["verified"]
 session.media_state.assert_not_called()
