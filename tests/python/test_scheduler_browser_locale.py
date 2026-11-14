"""Owned scheduler/media text localizes without operating a real browser or timer."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from local_mode import background
from local_mode.browser_api import BrowserAPI
from local_mode.media_player import verify_control
from local_mode.messages import OwnedText, OwnedValueError, message
from local_mode.results import user_message


def render(text,locale):
    return user_message({'ok':True,'reply':text},locale=locale)


@pytest.mark.parametrize('provided',[None,'Window maximized. {1} /private/file','Напоминание'],ids=['default','user-placeholder','user-russian'])
def test_reminder_survives_restart_and_localizes_only_owned_prefix(tmp_path,monkeypatch,provided):
    monkeypatch.setattr(background.time,'time',lambda:1000)
    path=tmp_path/'schedule.json';reports=[]
    service=background.BackgroundServices(threading.Event(),reports.append,path)
    args={'action':'add','seconds':5}
    if provided is not None:args['message']=provided
    item=service.schedule(args)['schedule']
    restored=background.BackgroundServices(threading.Event(),reports.append,path)
    restored.tick(item['fire_at']+1);restored.tick(item['fire_at']+2)
    assert len(reports)==1 and isinstance(reports[0],OwnedText)
    assert render(reports[0],'en')==('Reminder.' if provided is None else 'Reminder: '+provided)
    assert render(reports[0],'ru')==('Напоминание.' if provided is None else 'Напоминание: '+provided)
    assert json.loads(path.read_text(encoding='utf-8'))[0]['message']==('Reminder' if provided is None else provided)


def test_old_reminder_text_is_not_reinterpreted_as_a_catalog_key(tmp_path):
    path=tmp_path/'schedule.json'
    path.write_text(json.dumps([{'id':1,'fire_at':0,'message':'Reminder.','fired':False}]))
    reports=[];service=background.BackgroundServices(threading.Event(),reports.append,path)
    service.tick(1)
    assert render(reports[0],'ru')=='Напоминание: Reminder.'


def test_invalid_schedule_is_owned_and_does_not_write(tmp_path,monkeypatch):
    monkeypatch.setattr(background.time,'time',lambda:1000)
    path=tmp_path/'schedule.json'
    service=background.BackgroundServices(threading.Event(),Mock(),path)
    with pytest.raises(OwnedValueError) as caught:service.schedule({'action':'add','seconds':-1})
    assert str(caught.value)=='Select a time in the future.'
    assert message(caught.value.template,locale='ru')=='Укажите время в будущем.'
    assert not path.exists()


def test_watchdog_preserves_raw_diagnostic_value(tmp_path,monkeypatch):
    reports=[]
    stop=SimpleNamespace(wait=Mock(side_effect=[False,True]))
    service=background.BackgroundServices(stop,reports.append,tmp_path/'schedule.json')
    service.tick=Mock(side_effect=ValueError('Provider raw {0}'))
    monkeypatch.setattr(background,'safe_error',str)
    service._loop()
    assert render(reports[0],'en')=='Monitoring: Provider raw {0}'
    assert render(reports[0],'ru')=='Мониторинг: Provider raw {0}'


def page_controller(states=()):
    page=SimpleNamespace(is_closed=lambda:False,set_default_timeout=Mock(),
        evaluate=AsyncMock(side_effect=list(states)),wait_for_timeout=AsyncMock(),
        close=AsyncMock(),goto=AsyncMock())
    controller=SimpleNamespace(_launch_browser_if_needed=AsyncMock(),
        _context=SimpleNamespace(pages=[page]),_get_page=AsyncMock(return_value=page))
    return page,controller


def state(**values):
    return {**dict(url='https://example.com/video',source='blob:fixture',video=True,
        paused=False,muted=False,volume=.5,current_time=2,playback_rate=1,
        title='Video paused.'),**values}


@pytest.mark.parametrize('verified',[True,False])
def test_browser_playback_reply_is_owned_and_truthful(verified):
    before=state()
    after={**before,'current_time':3 if verified else 2}
    page,controller=page_controller([before,before,None,after])
    result=asyncio.run(BrowserAPI().execute(controller,{'action':'media_resume'}))
    assert result['verified'] is verified
    assert render(result['reply'],'en')==('Video is playing.' if verified else 'The player did not confirm playback.')
    assert render(result['reply'],'ru')==('Видео воспроизводится.' if verified else 'Плеер не подтвердил воспроизведение.')
    assert result['media']['title']=='Video paused.'


def test_browser_missing_player_and_closed_tab_localize():
    page,controller=page_controller([{'video':False},{'video':False}])
    result=asyncio.run(BrowserAPI().execute(controller,{'action':'media_play'}))
    assert user_message(result,locale='ru')=='Выберите вкладку с нужным плеером.'
    result=asyncio.run(BrowserAPI().execute(controller,{'action':'close_tab'}))
    assert user_message(result)=='Tab closed.'
    assert user_message(result,locale='ru')=='Вкладка закрыта.'
    page.close.assert_awaited_once()


@pytest.mark.parametrize('args,expected',[
    ({'action':'snapshot','tab_id':'missing'},'Tab not found. Request list_tabs.'),
    ({'action':'go_to','url':'file:///private'},'Only HTTP/HTTPS pages are allowed.'),
])
def test_browser_validation_remains_owned_before_side_effect(args,expected):
    page,controller=page_controller()
    with pytest.raises(OwnedValueError) as caught:asyncio.run(BrowserAPI().execute(controller,args))
    assert str(caught.value)==expected
    assert message(caught.value.template,*caught.value.values,locale='ru')!=expected
    page.goto.assert_not_awaited()


@pytest.mark.parametrize('raw',[False,True])
def test_browser_keeps_only_owned_control_errors_translatable(raw):
    provider='No player was found in the selected tab. Open a video.'
    states=[state(),ValueError(provider)] if raw else [state(),state()]
    page,controller=page_controller(states)
    result=asyncio.run(BrowserAPI().execute(controller,{'action':'media_volume','volume':101}))
    if raw:
        assert not isinstance(result['error'],OwnedText)
        assert user_message(result,locale='ru')==provider
    else:
        assert isinstance(result['error'],OwnedText)
        assert user_message(result,locale='ru')=='Параметр volume: число от 0 до 100.'


@pytest.mark.parametrize('action,target,after,en,ru',[
    ('media_pause',{'paused':True},{'paused':True},'Video paused.','Видео на паузе.'),
    ('media_volume',{'volume':.5},{},'Video volume: 50 percent.','Громкость видео: 50 процентов.'),
    ('media_rate',{'playback_rate':1.25},{'playback_rate':1.25},'Video playback rate: 1.25.','Скорость видео: 1.25.'),
])
def test_verified_player_messages_keep_numeric_values_separate(action,target,after,en,ru):
    current=state();changed={**current,**after}
    result=verify_control(action,target,current,changed)
    assert result['verified'] and isinstance(result['reply'],OwnedText)
    assert render(result['reply'],'en')==en
    assert render(result['reply'],'ru')==ru
    assert result['media']['title']=='Video paused.'


def test_unverified_player_change_never_becomes_success():
    result=verify_control('media_pause',{'paused':True},state(),state())
    assert not result['ok'] and not result['verified']
    assert user_message(result)=='The player did not confirm the change. Check the tab and retry the command.'
    assert user_message(result,locale='ru')=='Плеер не подтвердил изменение. Проверьте вкладку и повторите команду.'


def test_raw_catalog_collisions_are_not_translated():
    for key in ('reply','result','error'):
        assert user_message({'ok':key!='error',key:'Video paused.'},locale='ru')=='Video paused.'


def test_chrome_setup_messages_localize_without_launch(monkeypatch):
    from local_mode import chrome_mcp,chrome_credentials
    launch=Mock(side_effect=AssertionError('No browser/server launch in localization tests'))
    monkeypatch.setattr(chrome_mcp,'ChromeSession',launch)
    monkeypatch.setattr(chrome_mcp,'installed',lambda *args:False)
    monkeypatch.setattr(chrome_credentials,'load_token',Mock(side_effect=AssertionError('No credential reads expected')))
    result=chrome_mcp.execute('Profile 2',{'action':'snapshot'})
    assert 'Playwright Extension' in user_message(result)
    assert 'В выбранном профиле' in user_message(result,locale='ru')
    monkeypatch.setattr(chrome_mcp,'installed',lambda *args:True)
    monkeypatch.setattr(chrome_credentials,'load_token',lambda *args:None)
    result=chrome_mcp.execute('Profile 2',{'action':'snapshot'})
    assert result['reply'].values==('Profile 2',)
    assert 'для профиля Profile 2' in user_message(result,locale='ru')
    launch.assert_not_called()


def test_chrome_owned_diagnostics_are_localizable_and_still_redacted():
    from local_mode import chrome_mcp
    session=chrome_mcp.ChromeSession.__new__(chrome_mcp.ChromeSession)
    session._secret='fixture-secret-token'
    error=OwnedValueError('Chrome player: {0}','https://example.com?token=fixture-secret-token&key=second-secret')
    result=session.error_text(error)
    assert isinstance(result,OwnedText)
    assert 'fixture-secret-token' not in result and 'second-secret' not in result
    assert '[redacted]' in result
    assert render(result,'ru').startswith('Плеер Chrome: https://example.com?token=[redacted]')
    raw=session.error_text(ValueError('Video paused.'))
    assert not isinstance(raw,OwnedText) and render(raw,'ru')=='Video paused.'


def test_chrome_control_error_keeps_owned_metadata(monkeypatch):
    from local_mode import chrome_mcp
    session=chrome_mcp.ChromeSession.__new__(chrome_mcp.ChromeSession)
    session.lock=threading.RLock();session.profile='Profile 2'
    session._ensure_media_tab=Mock(side_effect=OwnedValueError('No player was found in the selected tab. Open a video.'))
    session.tool=Mock(side_effect=AssertionError('No SDK call expected'))
    result=session.execute({'action':'media_pause'})
    assert not result['verified'] and isinstance(result['error'],OwnedText)
    assert user_message(result,locale='ru')=='На выбранной вкладке не найден плеер. Откройте видео.'
    session.tool.assert_not_called()


@pytest.mark.parametrize('marker',['false',1])
def test_non_boolean_default_marker_keeps_reminder_text(tmp_path,marker):
    path=tmp_path/'schedule.json'
    path.write_text(json.dumps([{'id':1,'fire_at':0,'message':'User text','default_message':marker,'fired':False}]))
    reports=[];background.BackgroundServices(threading.Event(),reports.append,path).tick(1)
    assert render(reports[0],'ru')=='Напоминание: User text'


@pytest.mark.parametrize('detail',[None,'Video paused.'])
def test_chrome_protocol_fallback_is_owned_but_provider_text_is_not(detail):
    import queue
    from local_mode import chrome_mcp
    from local_mode.messages import OwnedError
    session=chrome_mcp.ChromeSession.__new__(chrome_mcp.ChromeSession)
    session.lock=threading.RLock();session.sequence=0;session.send=Mock()
    session.messages=queue.Queue()
    error={'code':-32000}
    if detail is not None:error['message']=detail
    session.messages.put({'id':1,'error':error})
    with pytest.raises(RuntimeError) as caught:session.request('tools/list',{})
    if detail is None:
        assert isinstance(caught.value,OwnedError)
        assert message(caught.value.template,locale='ru')=='Ошибка MCP.'
    else:
        assert not isinstance(caught.value,OwnedError)
        assert str(caught.value)==detail


@pytest.mark.parametrize('overrides,expected',[
    ({'ad_showing':True},'Перемотка недоступна во время рекламы.'),
    ({'duration':None},'Перемотка недоступна для этого потока.'),
])
def test_seek_rejections_keep_owned_provenance(overrides,expected):
    from local_mode.media_player import control_plan
    with pytest.raises(OwnedValueError) as caught:
        control_plan('media_seek_forward',{},state(**overrides))
    assert message(caught.value.template,locale='ru')==expected


def test_missing_player_during_control_is_owned_in_both_consumers():
    from local_mode.media_player import control_player
    sentinel={'requested':False,'error_code':'player_not_found'}
    evaluate=Mock(side_effect=[state(),sentinel])
    with pytest.raises(OwnedValueError) as caught:control_player(evaluate,'media_pause',{})
    assert caught.value.template=='No player was found in the selected tab. Open a video.'
    page,controller=page_controller([state(),state(),sentinel])
    result=asyncio.run(BrowserAPI().execute(controller,{'action':'media_pause'}))
    assert not result['verified']
    assert user_message(result,locale='ru')=='На выбранной вкладке не найден плеер. Откройте видео.'


def test_ambiguous_locator_is_owned_and_never_clicked():
    page,controller=page_controller()
    locator=SimpleNamespace(count=AsyncMock(return_value=2),click=AsyncMock())
    page.locator=Mock(return_value=locator)
    with pytest.raises(OwnedValueError) as caught:
        asyncio.run(BrowserAPI().execute(controller,{'action':'click','selector':'button'}))
    assert caught.value.template=='The element was not found or is ambiguous. Refine the snapshot selection.'
    locator.click.assert_not_awaited()


def test_nested_owned_monitoring_error_localizes_without_translating_raw_values():
    nested = OwnedText('Monitoring: {0}', OwnedText('Window maximized.'))
    assert render(nested, 'ru') == 'Мониторинг: Окно развёрнуто на весь экран.'
    raw = OwnedText('Monitoring: {0}', 'Window maximized.')
    assert render(raw, 'ru') == 'Мониторинг: Window maximized.'

def test_background_report_uses_live_desktop_locale_without_rewriting_user_text(tmp_path, monkeypatch):
    from local_mode.desktop_host import DesktopRuntime
    monkeypatch.setattr(background.time, 'time', lambda: 1000)
    logs = []
    runtime = SimpleNamespace(interface_locale='ru', write_log=logs.append, silent_mode=False, speaker=SimpleNamespace(say=Mock()))
    report = lambda text: DesktopRuntime.report_background(runtime, text)
    service = background.BackgroundServices(threading.Event(), report, tmp_path / 'schedule.json')
    service.schedule({'action': 'add', 'seconds': 5, 'message': 'Window maximized. {0}'})
    service.tick(1006)
    assert logs == ['AI: Напоминание: Window maximized. {0}']
    runtime.speaker.say.assert_called_with('Напоминание: Window maximized. {0}')
    runtime.interface_locale = 'en'
    report(OwnedText('Monitoring: {0}', OwnedText('Window maximized.')))
    assert logs[-1] == 'AI: Monitoring: Window maximized.'
    report('Reminder.')
    assert logs[-1] == 'AI: Reminder.'


def test_schedule_owned_error_survives_registry_with_raw_errors_redacted(tmp_path, monkeypatch):
    from local_mode.tools import ToolRegistry
    monkeypatch.setattr(background.time, 'time', lambda: 1000)
    service = background.BackgroundServices(threading.Event(), Mock(), tmp_path / 'schedule.json')
    registry = ToolRegistry.__new__(ToolRegistry)
    registry.special = {'schedule': service.schedule}
    result = registry._invoke('schedule', {'action': 'add', 'seconds': -1})
    assert not result['ok']
    assert user_message(result, locale='ru') == 'Укажите время в будущем.'
    secret = 'synthetic-locale-secret'
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    def fail(args):
        raise OwnedValueError('Monitoring: {0}', secret + ' {1}')
    registry.special['schedule'] = fail
    result = registry._invoke('schedule', {})
    assert user_message(result, locale='ru') == 'Мониторинг: [redacted] {1}'
    def raw_fail(args):
        raise ValueError('Select a time in the future. ' + secret)
    registry.special['schedule'] = raw_fail
    assert user_message(registry._invoke('schedule', {}), locale='ru') == 'Select a time in the future. [redacted]'


@pytest.mark.parametrize('params', [{}, {'locale': 'fr'}, {'locale': []}, {'locale': 'ru', 'tool': 'run_python'}])
def test_locale_rpc_rejects_invalid_parameters_without_tool_access(params):
    from local_mode.desktop_host import DesktopRuntime
    runtime = SimpleNamespace(lock=threading.RLock(), interface_locale='en')
    with pytest.raises(ValueError):
        DesktopRuntime.dispatch(runtime, 'settings.locale', params)
    assert runtime.interface_locale == 'en'
    assert DesktopRuntime.dispatch(runtime, 'settings.locale', {'locale': 'ru'}) == {'ok': True}
    assert runtime.interface_locale == 'ru'


def test_nested_owned_error_redaction_preserves_templates_and_scalar_values(monkeypatch):
    from local_mode.gemini import safe_error
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-nested-secret')
    nested = OwnedText('Reminder: {0}', 'synthetic-nested-secret {1}')
    sanitized = safe_error(OwnedValueError('Monitoring: {0}', nested))
    assert render(sanitized, 'ru') == 'Мониторинг: Напоминание: [redacted] {1}'
    assert isinstance(sanitized.values[0], OwnedText)
    scalars = safe_error(OwnedValueError('Monitoring: {0}', 3, False, None))
    assert scalars.values == (3, False, None)


@pytest.mark.parametrize('locale, expected', [('en', 'Window maximized.'), ('ru', 'Окно развёрнуто на весь экран.')])
def test_worker_error_presents_owned_errors_and_preserves_raw_diagnostics(locale, expected, monkeypatch):
    from local_mode.desktop_host import DesktopRuntime
    logs = []
    runtime = SimpleNamespace(interface_locale=locale, write_log=logs.append)
    assert DesktopRuntime.report_worker_error(runtime, OwnedValueError('Window maximized.')) == expected
    assert logs[-1] == 'ERR: ' + expected
    monkeypatch.setenv('GEMINI_API_KEY', 'synthetic-worker-secret')
    text = DesktopRuntime.report_worker_error(runtime, ValueError('Window maximized. synthetic-worker-secret {0}'))
    assert text == 'Window maximized. [redacted] {0}'
    assert logs[-1] == 'ERR: ' + text


def test_rpc_serialization_renders_owned_fields_for_direct_dispatch(monkeypatch):
    import io
    from local_mode import desktop_host, camera_devices
    output = io.StringIO()
    requests = b'{"id":1,"method":"control","params":{"owned":true}}\n{"id":2,"method":"control","params":{}}\n'
    raw = {'ok': True, 'reply': 'Queue cleared.', 'queue': ['private raw {0}']}
    runtime = SimpleNamespace(interface_locale='ru', start=Mock(), close=Mock(), dispatch=lambda method, params: {'ok': True, 'reply': OwnedText('Queue cleared.'), 'queue': ['private raw {0}']} if params.get('owned') else raw)
    monkeypatch.setattr(desktop_host, 'DesktopRuntime', lambda emit: runtime)
    monkeypatch.setattr(camera_devices, 'select_camera', Mock())
    monkeypatch.setattr(desktop_host.sys, 'stdout', output)
    monkeypatch.setattr(desktop_host.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(requests)))
    assert desktop_host._serve() == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert responses[0]['result'] == {'ok': True, 'reply': 'Очередь очищена.', 'queue': ['private raw {0}']}
    assert responses[1]['result'] == raw
    runtime.close.assert_called_once()


@pytest.mark.parametrize('arguments', [['--command', 'status'], ['--text']])
@pytest.mark.parametrize('failure', ['none', 'owned-error', 'raw-error'])
def test_cli_json_results_render_owned_fields_without_changing_raw_data(arguments, failure, monkeypatch, capsys):
    from local_mode import cli, voice
    from local_mode.config import Settings
    monkeypatch.setenv('JARVIS_MODEL', 'synthetic-before-test')
    monkeypatch.setenv('JARVIS_AUTO_FALLBACK', 'false')
    monkeypatch.setattr(Settings, 'from_env', lambda **kwargs: Settings())
    fake = SimpleNamespace(stop=threading.Event(), tools=SimpleNamespace(special={}), background=SimpleNamespace(start=Mock()), close=Mock(), run=Mock(return_value={'ok': True, 'reply': OwnedText('Queue cleared.'), 'result': 'Queue cleared.', 'queue': ['raw user {0}']}))
    monkeypatch.setattr(cli, 'LocalAgent', lambda *args, **kwargs: fake)
    monkeypatch.setattr(voice, 'LocalSpeaker', lambda **kwargs: Mock())
    inputs = iter(['status', '/exit'])
    monkeypatch.setattr('builtins.input', lambda prompt='': next(inputs))
    if failure != 'none':
        fake.run.side_effect = OwnedValueError('Queue cleared.') if failure == 'owned-error' else ValueError('Queue cleared. {0}')
        code = cli.main([*arguments, '--language', 'ru'])
        output = capsys.readouterr().out
        expected = 'Очередь очищена.' if failure == 'owned-error' else 'Queue cleared. {0}'
        if '--text' in arguments:
            assert code == 0 and output.strip() == 'Ошибка: ' + expected
        else:
            assert code == 1 and json.loads(output)['error'] == expected
        fake.close.assert_called_once()
        return
    assert cli.main([*arguments, '--language', 'ru']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['reply'] == 'Очередь очищена.'
    assert result['result'] == 'Queue cleared.' and result['queue'] == ['raw user {0}']
    fake.run.assert_called_once_with('status')
    fake.close.assert_called_once()
