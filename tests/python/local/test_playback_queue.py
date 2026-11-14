"""Автопереход очереди, профиль и локальные плейлисты без YouTube/Computer Use."""
from unittest.mock import Mock,patch
import pytest
from local_mode.playback_queue import PlaybackQueue,video_url

A="https://www.youtube.com/watch?v=abcdefghijk"
B="https://www.youtube.com/watch?v=lmnopqrstuv"


def test_video_url_rejects_other_hosts_and_preserves_video_id():
 assert video_url("https://youtu.be/abcdefghijk?t=30")==A
 for value in ["https://youtube.com.evil.test/watch?v=abcdefghijk","file:///secret","https://youtube.com/playlist?list=123"]:
  with pytest.raises(ValueError):video_url(value)


def test_playlist_roundtrip_and_append_without_account_api(tmp_path):
 queue=PlaybackQueue(tmp_path/"lists.json");browser=Mock()
 queue.execute({"action":"queue_add","urls":[A,B]},browser,{})
 queue.execute({"action":"playlist_save","playlist":"Музыка"},browser,{})
 queue.execute({"action":"queue_clear"},browser,{})
 queue.execute({"action":"playlist_append","playlist":"Музыка"},browser,{})
 restored=PlaybackQueue(queue.path)
 assert restored.items==[A,B] and restored.playlists["Музыка"]==[A,B]
 assert not restored.playing
 browser.assert_not_called()


def test_auto_next_is_bound_to_profile_and_tab_and_ignores_ad_end(tmp_path):
 queue=PlaybackQueue(tmp_path/"lists.json");events=[]
 def browser(args):
  events.append(dict(args))
  if args["action"] in {"new_tab","go_to"}:return {"ok":True,"tab_id":"7"}
  if args["action"]=="media_play":return {"ok":True,"verified":True}
  return {"ok":True,"media":{"video":True,"url":A,"ended":True,"ad_showing":True}}
 target={"profile_id":"Profile 2"}
 queue.execute({"action":"queue_add","urls":[A,B]},browser,target)
 with patch.object(queue,"_start"):
  assert queue.execute({"action":"queue_play"},browser,target)["verified"]
  queue.tick();assert queue.position==0
  queue.browser=lambda args: {"ok":True,"media":{"video":True,"url":A,"ended":True,"ad_showing":False}} if args["action"]=="media_state" else browser(args)
  queue.tick()
 assert queue.position==1
 assert all(event["profile_id"]=="Profile 2" for event in events)
 assert events[-2]=={"action":"go_to","url":B,"profile_id":"Profile 2","tab_id":"7"}
 assert events[-1]["tab_id"]=="7"


def test_manual_navigation_stops_queue_instead_of_hijacking_new_page(tmp_path):
 queue=PlaybackQueue(tmp_path/"lists.json");queue.items=[A,B];queue.position=0;queue.playing=True
 queue.browser=Mock(return_value={"ok":True,"media":{"video":True,"url":B,"ended":True}})
 queue.tick()
 assert not queue.playing and queue.browser.call_count==1


def test_failed_playback_is_never_marked_playing(tmp_path):
 queue=PlaybackQueue(tmp_path/"lists.json");queue.items=[A]
 browser=Mock(side_effect=[{"ok":True,"tab_id":"1"},{"ok":False,"verified":False,"error":"реклама"}])
 result=queue.execute({"action":"queue_play"},browser,{"profile_id":"Profile 2"})
 assert not result["verified"] and not queue.playing


def test_invalid_append_does_not_partially_change_queue(tmp_path):
 queue=PlaybackQueue(tmp_path/"lists.json");queue.items=[A]
 with pytest.raises(ValueError):queue.execute({"action":"queue_add","urls":[B,"not a url"]},Mock(),{})
 assert queue.items==[A]


def test_stale_tab_index_never_pauses_or_navigates_unrelated_page(tmp_path):
 queue=PlaybackQueue(tmp_path/"lists.json");queue.items=[A,B];queue.position=0
 queue.target={"profile_id":"Profile 2","tab_id":"7"};queue.current_url=A
 events=[]
 def browser(args):
  events.append(args)
  if args["action"]=="media_state":return {"ok":True,"media":{"url":"https://example.com/","video":False}}
  if args["action"]=="new_tab":return {"ok":True,"tab_id":"9"}
  if args["action"]=="media_play":return {"ok":True,"verified":True}
  raise AssertionError("Unrelated tab was mutated")
 assert not queue.execute({"action":"queue_pause"},browser,{})["ok"]
 assert len(events)==1
 queue.browser=browser
 with patch.object(queue,"_start"):
  assert queue._play(1)["verified"]
 assert not any(event["action"]=="go_to" for event in events)
 assert next(event for event in events if event["action"]=="new_tab").get("tab_id") is None
 assert queue.target["tab_id"]=="9"
