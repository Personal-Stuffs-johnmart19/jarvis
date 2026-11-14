"""Local playlists and a YouTube queue with verified automatic advancement."""
from __future__ import annotations
import json
from pathlib import Path
import re
import threading
from typing import Callable
from urllib.parse import urlsplit, parse_qs
from local_mode.config import ROOT
from local_mode.storage import write_json
from local_mode.messages import OwnedText, OwnedValueError

QUEUE_ACTIONS = {"queue_add","queue_list","queue_play","queue_next","queue_pause","queue_remove","queue_clear",
                 "playlist_save","playlist_load","playlist_append","playlist_list","playlist_delete"}


def video_url(value: str) -> str:
    parsed=urlsplit(value.strip())
    if parsed.scheme not in {"http","https"} or parsed.username or parsed.password:
        raise OwnedValueError('A standard YouTube video URL is required.')
    host=(parsed.hostname or "").lower()
    identifier=(parsed.path.strip("/") if host=="youtu.be" else
                parse_qs(parsed.query).get("v",[""])[0] if host in {"youtube.com","www.youtube.com","m.youtube.com"} else "")
    if host in {"youtube.com","www.youtube.com","m.youtube.com"} and parsed.path.startswith("/shorts/"):
        identifier=parsed.path.split("/")[2]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}",identifier):
        raise OwnedValueError('A URL for an individual YouTube video is required. Named playlists are stored locally in Jarvis.')
    return "https://www.youtube.com/watch?v="+identifier


class PlaybackQueue:
    def __init__(self, path: Path | None = None) -> None:
        self.path=path or ROOT/"data"/"video-playlists.json"
        self.lock=threading.RLock();self.operation=threading.RLock()
        saved=json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        self.items=[video_url(url) for url in saved.get("items",[])][:200]
        self.playlists={str(name):[video_url(url) for url in urls][:200] for name,urls in saved.get("playlists",{}).items()}
        self.position=-1;self.playing=False;self.generation=0;self.target={};self.current_url="";self.browser=None
        self.report: Callable[[str],None]=lambda text:None
        self.stop=threading.Event();self.thread=None

    def _save(self) -> None:
        write_json(self.path,{"items":self.items,"playlists":self.playlists})

    def state(self) -> dict:
        with self.lock:
            return {"ok":True,"queue":list(self.items),"position":self.position,"playing":self.playing,
                    "playlists":list(self.playlists),"reply":OwnedText('Videos in queue: {0}. Playlists: {1}.', len(self.items), len(self.playlists))}

    def _start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.stop.clear();self.thread=threading.Thread(target=self._loop,daemon=True,name="Jarvis-video-queue")
            self.thread.start()

    def execute(self, args: dict, browser: Callable[[dict],dict], target: dict, report: Callable[[str],None] | None = None) -> dict:
        action=args["action"]
        with self.operation:
            if action in {"queue_play","queue_next"}:
                with self.lock:
                    self.browser=browser
                    if self.target.get("profile_id")==target.get("profile_id"):
                        self.target={**self.target,**target}
                    else:
                        if self.playing:return {"ok":False,"reply":OwnedText('Pause the queue in the previous profile first.')}
                        self.target=dict(target)
                    if report:self.report=report
                    index=(self.position+1 if action=="queue_next" else max(0,self.position))
                return self._play(index)
            if action=="queue_pause":
                with self.lock:
                    self.playing=False;self.generation+=1;stored=dict(self.target)
                if not stored:return {"ok":False,"reply":OwnedText('The queue has not started yet.')}
                actual=browser({"action":"media_state",**stored}).get("media",{})
                try:matches=video_url(actual.get("url",""))==self.current_url
                except ValueError:matches=False
                if not matches:return {"ok":False,"verified":False,"reply":OwnedText('The queue tab changed. Other players were left untouched.')}
                return browser({"action":"media_pause",**stored})
            with self.lock:
                if action=="queue_add":
                    raw=args.get("urls") or [args.get("url","")]
                    if not isinstance(raw,list) or not raw or len(raw)>200:raise OwnedValueError('Add between 1 and 200 URLs.')
                    urls=[video_url(url) for url in raw]
                    if len(self.items)+len(urls)>200:raise OwnedValueError('The queue supports a maximum of 200 videos.')
                    self.items.extend(urls)
                elif action=="queue_clear":
                    self.items=[];self.position=-1;self.playing=False;self.generation+=1
                elif action=="queue_remove":
                    index=args.get("index")
                    if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(self.items):raise OwnedValueError('Specify a video index from queue_list (zero-based).')
                    self.items.pop(index)
                    if index<self.position:self.position-=1
                    elif index==self.position:self.playing=False;self.position-=1;self.generation+=1
                elif action.startswith("playlist_") and action!="playlist_list":
                    name=str(args.get("playlist","")).strip()
                    if not name or len(name)>100:raise OwnedValueError('Playlist names must contain between 1 and 100 characters.')
                    if action=="playlist_save":self.playlists[name]=list(self.items)
                    else:
                        if name not in self.playlists:raise OwnedValueError('Playlist not found.')
                        if action=="playlist_delete":del self.playlists[name]
                        elif action=="playlist_load":
                            self.items=list(self.playlists[name]);self.position=-1;self.playing=False;self.generation+=1
                        elif action=="playlist_append":
                            if len(self.items)+len(self.playlists[name])>200:raise OwnedValueError('The queue supports a maximum of 200 videos.')
                            self.items.extend(self.playlists[name])
                elif action not in {"queue_list","playlist_list"}:raise OwnedValueError('Unknown queue command.')
                if action not in {"queue_list","playlist_list"}:self._save()
                result = self.state()
                if action == "playlist_save":result["reply"] = OwnedText('Playlist “{0}” saved locally in Jarvis.', name)
                elif action == "playlist_load":result["reply"] = OwnedText('Playlist “{0}” loaded into the queue.', name)
                elif action == "playlist_append":result["reply"] = OwnedText('Playlist “{0}” appended to the queue.', name)
                elif action == "playlist_delete":result["reply"] = OwnedText('Local playlist “{0}” deleted.', name)
                elif action == "queue_clear":result["reply"] = OwnedText('Queue cleared.')
                return result

    def _play(self, index: int) -> dict:
        with self.lock:
            if not 0<=index<len(self.items):
                self.playing=False
                return {"ok":False,"reply":OwnedText('The queue is empty or has finished.')}
            self.generation+=1;generation=self.generation;self.playing=False
            url=self.items[index];target=dict(self.target);browser=self.browser;previous_url=self.current_url
        if target.get("tab_id") and previous_url:
            actual=browser({"action":"media_state",**target}).get("media",{})
            try:matches=video_url(actual.get("url",""))==previous_url
            except ValueError:matches=False
            if not matches:
                # Индексы MCP-вкладок могут измениться после закрытия соседней.
                # Не навигируем постороннюю страницу: для очереди открываем новую.
                target.pop("tab_id",None)
        result=browser({"action":"go_to" if target.get("tab_id") else "new_tab","url":url,**target})
        if not result.get("ok"):return result
        tab=result.get("tab_id")
        if tab is None:
            match=re.search(r"(?m)^- (\d+): \(current\)",result.get("text",""))
            tab=match[1] if match else target.get("tab_id")
        if tab is None:return {"ok":False,"reply":OwnedText('The browser did not return a tab ID for the queue.')}
        target["tab_id"]=str(tab)
        result=browser({"action":"media_play",**target})
        with self.lock:
            if generation!=self.generation:return {"ok":False,"reply":OwnedText('Queue operation cancelled.')}
            self.target=target;self.current_url=url;self.position=index;self.playing=bool(result.get("verified"))
        if self.playing:
            self._start();self.report(OwnedText('Queue: playing video {0} of {1}.', index+1, len(self.items)))
        return {**result,"queue_position":index,"reply":OwnedText('Playing video {0} of {1}.', index+1, len(self.items)) if self.playing else result.get("reply") or result.get("error") or OwnedText('The player did not confirm playback.')}

    def tick(self) -> None:
        with self.operation:
            with self.lock:
                if not self.playing or self.browser is None:return
                target=dict(self.target);index=self.position;url=self.items[index];generation=self.generation;browser=self.browser
            result=browser({"action":"media_state",**target})
            media=result.get("media",{})
            if not result.get("ok") or not media.get("video"):
                with self.lock:self.playing=False
                self.report(OwnedText('Queue paused: the tab or player is unavailable.'));return
            if media.get("ad_showing"):return
            try:same=video_url(media.get("url",""))==url
            except ValueError:same=False
            if not same:
                with self.lock:self.playing=False
                self.report(OwnedText('Queue paused: the video in the tab changed.'));return
            if media.get("ended"):
                with self.lock:
                    if generation!=self.generation:return
                    if index+1>=len(self.items):
                        self.playing=False;self.report(OwnedText('Queue finished.'));return
                self._play(index+1)

    def _loop(self) -> None:
        while not self.stop.wait(1):
            try:self.tick()
            except Exception:
                with self.lock:self.playing=False
                self.report(OwnedText('Queue paused because the connection to the player failed.'))

    def close(self) -> None:
        self.stop.set()
        with self.lock:self.playing=False;self.generation+=1


_instance=None
_instance_lock=threading.Lock()
def playback_queue() -> PlaybackQueue:
    global _instance
    with _instance_lock:
        if _instance is None:_instance=PlaybackQueue()
        return _instance


def close_queue() -> None:
    if _instance is not None:
        _instance.close()
