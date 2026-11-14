"""Playback startup uses only synthetic DOM/protocol fixtures, never user Chrome."""
import asyncio
import hashlib
import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from local_mode import chrome_mcp
from local_mode.browser_api import BrowserAPI
from local_mode.media_player import (PLAY_SCRIPT, MUSIC_RESULTS_SCRIPT,
    music_query, observed_music_url)


def session():
    value = chrome_mcp.ChromeSession.__new__(chrome_mcp.ChromeSession)
    value.lock = threading.RLock()
    value.profile = 'Fixture'
    value._secret = 'fixture-token'
    value.sequence = 0
    value.send = Mock()
    value.messages = queue.Queue()
    value.close = Mock()
    return value


def test_no_player_fails_without_wait_or_navigation():
    value = session()
    value._media_evaluate = Mock(return_value={'requested':False,'error_code':'player_not_found'})
    value.media_state = Mock(side_effect=AssertionError('No polling without a player'))
    value.tool = Mock(side_effect=AssertionError('No automatic music selection'))
    result = value.execute({'action':'media_play'})
    assert not result['ok'] and not result['verified']
    assert 'No player' in result['error']
    value._media_evaluate.assert_called_once_with(PLAY_SCRIPT)


def test_music_search_navigates_only_to_observed_ordinary_video():
    value = session()
    value.tool = Mock(return_value={'ok':True})
    value._media_evaluate = Mock(side_effect=[
        {'videos':[{'url':'https://www.youtube.com/shorts/aaaaaaaaaaa'},
                   {'url':'https://www.youtube.com/watch?v=bbbbbbbbbbb&list=opaque'}]},
        {'video':True},
        {'requested':True},
    ])
    value.media_state = Mock(return_value={'ok':True,'verified':True,'media':{}})
    result = value.execute({'action':'music_play','query':'jazz & piano'})
    assert result['verified']
    assert value.tool.call_args_list[0].args == ('browser_navigate', {'url':'https://www.youtube.com/results?search_query=jazz+%26+piano'})
    assert value.tool.call_args_list[1].args == ('browser_navigate', {'url':'https://www.youtube.com/watch?v=bbbbbbbbbbb'})
    assert value._media_evaluate.call_args_list[0].args == (MUSIC_RESULTS_SCRIPT,)


def test_empty_music_results_never_invent_a_video_or_start_playback(monkeypatch):
    monkeypatch.setattr(chrome_mcp,'MUSIC_READY_TIMEOUT',0)
    value = session()
    value.tool = Mock(return_value={'ok':True})
    value._media_evaluate = Mock(return_value={'videos':[]})
    value.media_state = Mock(side_effect=AssertionError('No invented player'))
    result = value.execute({'action':'music_play'})
    assert not result['ok'] and not result['verified']
    assert value.tool.call_count == 1


@pytest.mark.parametrize('url',[
    'https://evil.example/watch?v=bbbbbbbbbbb',
    'https://www.youtube.com@evil.example/watch?v=bbbbbbbbbbb',
    'https://www.youtube.com:443/watch?v=bbbbbbbbbbb',
    'https://www.youtube.com/redirect?q=https://evil.example',
    'https://www.youtube.com/shorts/bbbbbbbbbbb',
    'javascript:alert(1)',
])
def test_music_result_validation_preserves_host_and_video_boundary(url):
    assert observed_music_url({'videos':[{'url':url}]}) is None


@pytest.mark.parametrize('query',[None,'', '  ', 'x'*201, 123])
def test_music_query_rejects_invalid_data_before_navigation(query):
    with pytest.raises(ValueError):
        music_query({'query':query})
    assert music_query({}) == 'music'


def test_request_cancellation_closes_only_mcp_without_sending_another_command():
    value = session()
    value._request_cancel = threading.Event()
    value._request_cancel.set()
    with pytest.raises(chrome_mcp.ChromeRequestCancelled):
        value.request('tools/call',{})
    value.close.assert_called_once()
    value.send.assert_not_called()


def test_total_deadline_prevents_command_dispatch():
    value = session()
    value._operation_deadline = 0
    with pytest.raises(chrome_mcp.ChromeRequestTimeout):
        value.request('tools/call',{})
    value.close.assert_called_once()
    value.send.assert_not_called()


def test_request_timeout_is_not_token_setup_and_is_not_retried(monkeypatch):
    value = session()
    value.process = SimpleNamespace(poll=lambda:None)
    value.token_fingerprint = hashlib.sha256(b'fixture-token').hexdigest()
    value.execute = Mock(side_effect=chrome_mcp.ChromeRequestTimeout('Inspect the tab before retrying.'))
    monkeypatch.setattr(chrome_mcp,'installed',lambda *args:True)
    monkeypatch.setattr('local_mode.chrome_credentials.load_token',lambda *args:'fixture-token')
    monkeypatch.setattr(chrome_mcp,'_sessions',{('chrome','Fixture'):value})
    result = chrome_mcp.execute('Fixture',{'action':'media_play'})
    assert result['timed_out'] and not result['verified']
    assert not result.get('requires_setup') and not result.get('setup_kind')
    value.execute.assert_called_once()


def test_stop_signals_an_initializing_connection_without_global_lock_wait(monkeypatch):
    entered = threading.Event()
    results = []

    def initialize(*args, cancel, **kwargs):
        entered.set()
        assert cancel.wait(2), 'Stop could not reach initializing MCP'
        raise chrome_mcp.ChromeRequestCancelled('Video startup cancelled.')

    monkeypatch.setattr(chrome_mcp,'installed',lambda *args:True)
    monkeypatch.setattr('local_mode.chrome_credentials.load_token',lambda *args:'fixture-token')
    monkeypatch.setattr(chrome_mcp,'_sessions',{})
    monkeypatch.setattr(chrome_mcp,'_operations',{})
    monkeypatch.setattr(chrome_mcp,'ChromeSession',initialize)
    worker = threading.Thread(target=lambda:results.append(chrome_mcp.execute('Fixture',{'action':'music_play'})))
    worker.start()
    assert entered.wait(2)
    chrome_mcp.cancel_media_waits('Fixture')
    worker.join(2)
    assert not worker.is_alive()
    assert len(results) == 1 and results[0]['cancelled'] and not results[0]['verified']


def test_inflight_request_observes_cancel_without_replaying():
    value = session()
    cancel = value._request_cancel = threading.Event()
    def receive(**kwargs):
        assert kwargs['timeout'] <= .1
        cancel.set()
        raise queue.Empty
    value.messages = SimpleNamespace(get=receive)
    with pytest.raises(chrome_mcp.ChromeRequestCancelled):
        value.request('tools/call',{'name':'browser_evaluate'})
    value.send.assert_called_once()
    value.close.assert_called_once()


def test_next_explicit_call_replaces_dead_session_once(monkeypatch):
    old, fresh = session(), session()
    old.process = SimpleNamespace(poll=lambda:1)
    fresh.execute = Mock(return_value={'ok':True})
    launch = Mock(return_value=fresh)
    monkeypatch.setattr(chrome_mcp,'installed',lambda *args:True)
    monkeypatch.setattr('local_mode.chrome_credentials.load_token',lambda *args:'fixture-token')
    monkeypatch.setattr(chrome_mcp,'_sessions',{('chrome','Fixture'):old})
    monkeypatch.setattr(chrome_mcp,'ChromeSession',launch)
    assert chrome_mcp.execute('Fixture',{'action':'snapshot'})['ok']
    launch.assert_called_once()
    old.close.assert_called_once()
    fresh.execute.assert_called_once()
    assert launch.call_args.kwargs['deadline'] == fresh.execute.call_args.kwargs['deadline']


def test_playback_rejection_returns_unverified_without_retry():
    value = session()
    value._media_evaluate = Mock(return_value={'requested':False,'play_error':'NotAllowedError'})
    value.media_state = Mock(side_effect=AssertionError('No polling after rejection'))
    result = value.execute({'action':'media_play'})
    assert not result['ok'] and not result['verified']
    assert 'NotAllowedError' in result['error']
    value._media_evaluate.assert_called_once()


def test_owned_browser_music_path_uses_same_observed_result_and_play_script():
    state = dict(url='https://www.youtube.com/watch?v=bbbbbbbbbbb',source='fixture',video=True,
                 paused=False,muted=False,volume=.5,current_time=1)
    page = SimpleNamespace(is_closed=lambda:False,set_default_timeout=Mock(),goto=AsyncMock(),
        evaluate=AsyncMock(side_effect=[{'videos':[{'url':state['url']}]},state,state,state,
            {'requested':True},{**state,'current_time':2}]),wait_for_timeout=AsyncMock())
    controller = SimpleNamespace(_launch_browser_if_needed=AsyncMock(),
        _context=SimpleNamespace(pages=[page]),_get_page=AsyncMock(return_value=page))
    result = asyncio.run(BrowserAPI().execute(controller,{'action':'music_play'}))
    assert result['verified']
    assert page.goto.call_args_list[1].args == (state['url'],)
    assert any(call.args == (PLAY_SCRIPT,) for call in page.evaluate.call_args_list)


@pytest.mark.parametrize('hydrated',[False,True],ids=['never-loaded','disappeared-after-loading'])
def test_owned_browser_music_missing_player_never_falls_back_to_unrelated_tab(hydrated):
    page = SimpleNamespace(is_closed=lambda:False,set_default_timeout=Mock(),goto=AsyncMock(),
        evaluate=AsyncMock(side_effect=[{'video':False}] if hydrated else AssertionError('No playback after selected player failed')))
    unrelated = SimpleNamespace(is_closed=lambda:False,
        evaluate=AsyncMock(side_effect=AssertionError('Unrelated media tab must never be inspected or played')))
    controller = SimpleNamespace(_launch_browser_if_needed=AsyncMock(),
        _context=SimpleNamespace(pages=[page,unrelated]),_get_page=AsyncMock(return_value=page))
    api = BrowserAPI()
    api._music_observation = AsyncMock(side_effect=[
        {'videos':[{'url':'https://www.youtube.com/watch?v=bbbbbbbbbbb'}]}, {'video':hydrated}])
    result = asyncio.run(api.execute(controller,{'action':'music_play'}))
    assert not result['ok'] and not result['verified']
    assert 'No player' in result['error']
    unrelated.evaluate.assert_not_awaited()
    assert page.evaluate.await_count == int(hydrated)
    assert page.goto.await_count == 2


@pytest.mark.parametrize('mode',['pending','rejected','synchronous-throw'])
def test_shared_play_start_returns_synchronously_without_duplicate_or_unhandled_promise(mode):
    # Node executes our fixed DOM script against a fake element. No browser,
    # networking, audio device or real media source participates.
    import shutil
    import subprocess
    node = shutil.which('node')
    assert node, 'Development Node is required for the playback script fixture'
    source = '''const script=JSON.parse(process.argv[1]);const mode=process.argv[2];
let plays=0;
const main={paused:true,muted:true,volume:0,tagName:'VIDEO',getClientRects:()=>[1],
 play(){plays++;if(mode==='synchronous-throw') throw {name:'NotAllowedError'};
 return mode==='pending'?new Promise(()=>{}):Promise.reject({name:'NotAllowedError'});}};
const wrong={...main,play(){throw Error('Wrong media element');}};
global.document={querySelectorAll:()=>[wrong,main],querySelector:s=>
 s==='#movie_player video.html5-main-video'?main:null};
const fn=eval('('+script+')');const first=fn();
if(mode==='synchronous-throw') {
 if(first.play_error!=='NotAllowedError' || fn().play_error!=='NotAllowedError' || plays!==2)
  throw Error('Synchronous rejection prevented a new explicit attempt');
} else {
const second=fn();
if(first?.then || !first.requested || !second.pending || plays!==1 || main.muted || main.volume!==.25)
 throw Error('Unbounded or repeated playback / incorrect selected element');
setImmediate(()=>{if(mode==='rejected') {
 if(fn().play_error!=='NotAllowedError' || plays!==1) throw Error('Rejection was lost or playback retried');
 if(!fn().requested || plays!==2) throw Error('A new explicit attempt cannot recover after rejection');
}});
}
console.log('pending media returned synchronously; one request; main player selected');'''
    process = subprocess.run([node,'-e',source,json.dumps(PLAY_SCRIPT),mode],capture_output=True,text=True,timeout=10)
    assert process.returncode == 0, process.stderr


def test_music_results_script_evaluates_visible_nonadvertising_anchors():
    import shutil
    import subprocess
    node = shutil.which('node')
    assert node, 'Development Node is required for the music script fixture'
    source = '''const script=JSON.parse(process.argv[1]);
const row=(url,{ad=false,hidden=false}={})=>({closest:()=>ad?{}:null,
 querySelector:s=>s==='a#video-title[href]'?{href:url,innerText:'Fixture',getClientRects:()=>hidden?[]:[1]}:null});
global.document={querySelectorAll:()=>[
 row('https://www.youtube.com/watch?v=aaaaaaaaaaa',{ad:true}),
 row('https://www.youtube.com/watch?v=hhhhhhhhhhh',{hidden:true}),
 row('https://www.youtube.com/shorts/sssssssssss'),
 row('https://www.youtube.com/watch?v=bbbbbbbbbbb')]};
const result=eval('('+script+')')();
if(result.videos.length!==2) throw Error('Ad or hidden music result was observed');
console.log(JSON.stringify(result));'''
    process = subprocess.run([node,'-e',source,json.dumps(MUSIC_RESULTS_SCRIPT)],capture_output=True,text=True,timeout=10)
    assert process.returncode == 0, process.stderr
    assert observed_music_url(json.loads(process.stdout)) == 'https://www.youtube.com/watch?v=bbbbbbbbbbb'
