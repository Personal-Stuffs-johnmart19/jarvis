"""Verified HTML player controls without global media keys or coordinates."""
from __future__ import annotations
import json
import math
import re
from urllib.parse import urlsplit, parse_qs
from typing import Any, Callable
from local_mode.messages import OwnedText, OwnedValueError

# Предпочитаем видимый основной YouTube-плеер; скрытое рекламное video не цель.
_PLAYER = """const candidates=[...document.querySelectorAll('video,audio')];
const v=document.querySelector('#movie_player video.html5-main-video') ||
 candidates.find(e=>e.getClientRects().length && !e.paused) ||
 candidates.find(e=>e.getClientRects().length) || candidates.find(e=>e.tagName==='AUDIO');"""
_STATE = """return {url:location.href,title:document.title,video:!!v,
 current_time:v?.currentTime??0,paused:v?.paused??true,muted:v?.muted??true,
 volume:v?.volume??0,ready_state:v?.readyState??0,ended:v?.ended??false,
 duration:Number.isFinite(v?.duration)?v.duration:null,playback_rate:v?.playbackRate??1,
 fullscreen:!!(v && document.fullscreenElement?.contains(v)),source:v?.currentSrc??'',
 ad_showing:document.querySelector('#movie_player')?.classList.contains('ad-showing')??false,
 error:v?.error?.message??null};"""
STATE_SCRIPT = "() => {" + _PLAYER + _STATE + "}"
# A media play promise may remain pending while a stream loads. Never return
# that promise to Playwright: observe state independently and consume rejection.
PLAY_SCRIPT = "() => {" + _PLAYER + """
if(!v) return {requested:false,error_code:'player_not_found'};
if(v.__jarvisPlayError) {
 const error=v.__jarvisPlayError; v.__jarvisPlayError=null;
 return {requested:false,play_error:error};
}
const ad=document.querySelector('#movie_player')?.classList.contains('ad-showing');
if(ad) {
 const skip=document.querySelector('.ytp-skip-ad-button,.ytp-ad-skip-button-modern,.ytp-ad-skip-button');
 if(skip?.getClientRects().length) skip.click();
} else { v.muted=false; if(v.volume===0) v.volume=.25; }
if(v.paused) {
 if(v.__jarvisPlayPending) return {requested:true,pending:true};
 v.__jarvisPlayPending=true; v.__jarvisPlayError=null;
 try { Promise.resolve(v.play()).then(()=>{v.__jarvisPlayPending=false;},error=>{
  v.__jarvisPlayPending=false; v.__jarvisPlayError=String(error?.name||'PlaybackError');
 }); } catch(error) { v.__jarvisPlayPending=false; v.__jarvisPlayError=String(error?.name||'PlaybackError'); }
}
const error=v.__jarvisPlayError||null; v.__jarvisPlayError=null;
return {requested:true,pending:!!v.__jarvisPlayPending,play_error:error,ad_showing:!!ad};
}"""

MUSIC_RESULTS_SCRIPT = """() => ({videos:[...document.querySelectorAll('ytd-video-renderer')]
 .filter(e=>!e.closest('ytd-ad-slot-renderer,ytd-promoted-video-renderer,[is-ad]') && !e.querySelector('[is-ad],ytd-ad-badge-renderer'))
 .map(e=>e.querySelector('a#video-title[href]'))
 .filter(a=>a && a.getClientRects().length)
 .slice(0,20).map(a=>({url:a.href,title:a.innerText}))})"""


def music_query(args: dict) -> str:
    query = args.get('query', 'music')
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise OwnedValueError('Provide a music search query of 1 to 200 characters.')
    return query.strip()


def observed_music_url(result: dict) -> str | None:
    videos = result.get('videos', [])
    if not isinstance(videos, list):
        return None
    for item in videos[:20]:
        if not isinstance(item, dict) or not isinstance(item.get('url'), str):
            continue
        url = urlsplit(item['url'])
        video = parse_qs(url.query).get('v', [''])[0]
        if (url.scheme == 'https' and url.netloc in {'www.youtube.com', 'youtube.com', 'm.youtube.com'}
                and url.path == '/watch' and re.fullmatch(r'[A-Za-z0-9_-]{11}', video)):
            return 'https://www.youtube.com/watch?v=' + video
    return None
CONTROL_ACTIONS = {"media_pause", "media_fullscreen", "media_exit_fullscreen", "media_volume",
                   "media_volume_up", "media_volume_down", "media_mute", "media_unmute",
                   "media_seek_forward", "media_seek_back", "media_rate"}


def _number(args: dict, key: str, default: float, low: float, high: float) -> float:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or not low <= value <= high:
        raise OwnedValueError("Parameter {0} must be a number from {1} to {2}.", key, low, high)
    return float(value)


def control_plan(action: str, args: dict, before: dict) -> tuple[str, dict]:
    if action not in CONTROL_ACTIONS:
        raise OwnedValueError("Unknown player command.")
    if not before.get("video"):
        raise OwnedValueError("No player was found in the selected tab. Open a video.")
    target = {}
    if action == "media_pause":
        body="v.pause();";target={"paused":True}
    elif action in {"media_fullscreen","media_exit_fullscreen"}:
        enter=action=="media_fullscreen"
        body=("if(document.fullscreenElement && !document.fullscreenElement.contains(v)) await document.exitFullscreen(); if(!document.fullscreenElement) await (document.querySelector('#movie_player')||v).requestFullscreen();" if enter else
              "if(document.fullscreenElement) await document.exitFullscreen();")
        target={"fullscreen":enter}
    elif action in {"media_mute","media_unmute"}:
        value=action=="media_mute";body="v.muted="+json.dumps(value)+";";target={"muted":value}
    elif action in {"media_volume","media_volume_up","media_volume_down"}:
        current=float(before.get("volume",0))
        value=(_number(args,"volume",50,0,100)/100 if action=="media_volume" else
               max(0,min(1,current+(_number(args,"step",10,1,100)/100)*(1 if action=="media_volume_up" else -1))))
        body="v.volume="+json.dumps(value)+";"+("v.muted=false;" if value>0 else "")
        target={"volume":value,**({"muted":False} if value>0 else {})}
    elif action == "media_rate":
        value=_number(args,"rate",1,0.25,4);body="v.playbackRate="+json.dumps(value)+";";target={"playback_rate":value}
    else:
        if before.get("ad_showing"):
            raise OwnedValueError("Seeking is unavailable during ads.")
        duration=before.get("duration")
        if not isinstance(duration,(int,float)) or duration<=0:
            raise OwnedValueError("Seeking is unavailable for this stream.")
        delta=_number(args,"seconds",10,1,3600)*(1 if action=="media_seek_forward" else -1)
        value=max(0,min(float(duration)-0.1,float(before.get("current_time",0))+delta))
        body="v.currentTime="+json.dumps(value)+";";target={"current_time":value}
    script="async () => {"+_PLAYER+"if(!v) return {requested:false,error_code:'player_not_found'};"+body+"return {requested:true};}"
    return script,target


def verify_control(action: str, target: dict, before: dict, after: dict) -> dict:
    stable=before.get("url")==after.get("url") and before.get("source","")==after.get("source","")
    verified=bool(stable and after.get("video") and not after.get("error"))
    for key,value in target.items():
        actual=after.get(key)
        if isinstance(value,bool): verified=verified and actual is value
        elif isinstance(actual,(int,float)):
            verified=verified and abs(actual-value) <= (2 if key=="current_time" else .01)
        else:verified=False
    template={"media_pause":"Video paused.","media_fullscreen":"The player is fullscreen.",
              "media_exit_fullscreen":"Fullscreen mode is disabled.","media_mute":"Video sound is muted.",
              "media_unmute":"Video sound is enabled.","media_seek_forward":"Video skipped forward.",
              "media_seek_back":"Video skipped backward."}.get(action,"")
    values=()
    if "volume" in target:
        template="Video volume: {0} percent.";values=(round(after.get('volume',0)*100),)
    if "playback_rate" in target:
        template="Video playback rate: {0}.";values=(after.get('playback_rate',1),)
    return {"ok":bool(verified),"verified":bool(verified),"action":action,"media":after,
            "reply":OwnedText(template,*values) if verified else OwnedText("The player did not confirm the change. Check the tab and retry the command.")}



def validate_control_result(result: Any) -> None:
    # Only our fixed control script emits this sentinel; never classify error text.
    if isinstance(result,dict) and result.get("requested") is False and result.get("error_code")=="player_not_found":
        raise OwnedValueError("No player was found in the selected tab. Open a video.")


def control_player(evaluate: Callable[[str], Any], action: str, args: dict) -> dict:
    before=evaluate(STATE_SCRIPT)
    script,target=control_plan(action,args,before)
    validate_control_result(evaluate(script))
    # Отдельный запрос к DOM: отчёт команды сам по себе не доказывает результат.
    after=evaluate(STATE_SCRIPT)
    return verify_control(action,target,before,after)


def player_request(text: str) -> dict | None:
    value=re.sub(r"[^\w\s%]"," ",text.casefold().replace("ё","е"))
    value=re.sub(r"\s+"," ",value).strip()
    value=re.sub(r"^пожалуйста | пожалуйста$","",value)
    value=re.sub(r"^(?:джарвис|jarvis|джарвиз|жарвис) ","",value)
    queue_commands = {"покажи очередь":"queue_list", "покажи очередь видео":"queue_list",
                      "запусти очередь":"queue_play", "включи очередь":"queue_play",
                      "следующее видео":"queue_next", "следующий ролик":"queue_next",
                      "приостанови очередь":"queue_pause", "останови очередь":"queue_pause",
                      "очисти очередь":"queue_clear", "покажи плейлисты":"playlist_list"}
    if value in queue_commands:return {"action":queue_commands[value]}
    playlist=re.fullmatch(r"(сохрани очередь как|сохрани плейлист|загрузи плейлист|добавь плейлист) (.+)",value)
    if playlist:
        action={"сохрани очередь как":"playlist_save","сохрани плейлист":"playlist_save","загрузи плейлист":"playlist_load","добавь плейлист":"playlist_append"}[playlist[1]]
        return {"action":action,"playlist":playlist[2]}
    if re.fullmatch(r"(?:поставь|поставить|поставьте|переведи) (?:видео|ролик|плеер|воспроизведение) на паузу|(?:останови|приостанови|выключи) (?:видео|ролик|плеер|воспроизведение)|(?:пауза|стоп) (?:видео|плеер|ролик)",value):
        return {"action":"media_pause"}
    if re.fullmatch(r"(?:продолжи|возобнови|включи) (?:видео|ролик|воспроизведение)|сними (?:видео|ролик) с паузы",value):
        return {"action":"media_resume"}
    if re.fullmatch(r"(?:разверни|включи|используй|сделай|открой) (?:видео|плеер|ролик) (?:на весь экран|на полный экран)|(?:включи )?полноэкранный режим(?: плеера| видео)?",value):
        return {"action":"media_fullscreen"}
    if re.fullmatch(r"(?:выйди из|выключи|отключи) полноэкранн(?:ого режима|ый режим)|верни (?:видео|плеер) в окно",value):
        return {"action":"media_exit_fullscreen"}
    if re.fullmatch(r"(?:выключи|отключи|убери) звук(?: видео| плеера)?",value):
        return {"action":"media_mute"}
    if re.fullmatch(r"(?:включи|верни) звук(?: видео| плеера)?",value):
        return {"action":"media_unmute"}
    change=re.fullmatch(r"(увеличь|уменьши|повысь|понизь) громкость(?: видео| плеера)?(?: на (\d+)(?: процентов|%)?)?",value)
    if change:return {"action":"media_volume_up" if change[1] in {"увеличь","повысь"} else "media_volume_down",**({"step":int(change[2])} if change[2] else {})}
    match=re.fullmatch(r"(?:сделай )?(?:видео |плеер |ролик )?(громче|тише)(?: видео| плеер| ролик)?(?: на (\d+)(?: процентов|%)?)?",value)
    if match:
        return {"action":"media_volume_up" if match[1]=="громче" else "media_volume_down",**({"step":int(match[2])} if match[2] else {})}
    match=re.fullmatch(r"(?:установи |поставь |сделай )?громкость(?: видео| плеера)?(?: на)? (\d+)(?: процентов|%)?",value)
    if match:return {"action":"media_volume","volume":int(match[1])}
    match=re.fullmatch(r"(?:перемотай |перемести )?(?:видео |ролик )?(вперёд|вперед|назад)(?: на)? (\d+)(?: секунд[уы]?)?",value)
    if match:return {"action":"media_seek_back" if match[1]=="назад" else "media_seek_forward","seconds":int(match[2])}
    return None
