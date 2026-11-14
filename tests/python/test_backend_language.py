import json
import threading
from collections import deque
from types import SimpleNamespace
from pathlib import Path
from local_mode.messages import message, state_label, tool_label
from local_mode.results import user_message, spoken_message


def test_backend_messages_default_to_english_and_support_explicit_russian():
    assert user_message({'ok': True, 'action': 'maximize_window', 'verified': True}) == 'Window maximized.'
    assert 'развёрнуто' in user_message({'ok': True, 'action': 'maximize_window', 'verified': True}, locale='ru')
    assert 'Could not verify' in user_message({'ok': True, 'action': 'maximize_window', 'verified': False})
    assert user_message({'ok': True, 'action': 'wake_word', 'enabled': False}) == 'Wake-word activation is disabled.'
    assert state_label('LISTENING') == 'Listening'
    assert state_label('LISTENING', locale='ru') == 'СЛУШАЮ'
    assert tool_label('open_app') == 'Open app'
    assert tool_label('open_app', locale='ru') == 'открытие приложения'
    assert message('Window maximized.', locale='../../private') == 'Window maximized.'


def test_unknown_and_user_model_provider_values_are_never_translated():
    for text in ['English provider error', 'Доступ запрещён', 'Window maximized.', '{0} /private/file']:
        assert user_message({'ok': True, 'reply': text}, locale='ru') == text
        assert user_message({'ok': False, 'error': text}, locale='ru') == text
        assert user_message({'ok': True, 'result': text}, locale='ru') == text
    assert message('Active reminders: {0}.', '{1} /private/file', locale='ru') == 'Активных напоминаний: {1} /private/file.'


def test_confirmation_and_speech_remain_truthful_in_each_language():
    pending = {'requires_confirmation': True, 'action': 'game_updater'}
    assert 'Confirmation required' in spoken_message(pending)
    assert 'Нужно подтверждение' in spoken_message(pending, locale='ru')
    assert 'game_updater' not in spoken_message(pending)
    assert 'structured result' in spoken_message({'ok': True, 'reply': '{"secret":"value"}'})
    assert 'secret' not in spoken_message({'ok': True, 'reply': '{"secret":"value"}'})


def test_desktop_logs_keep_raw_language_neutral_payloads():
    from local_mode.desktop_host import DesktopRuntime
    events=[]
    runtime=SimpleNamespace(lock=threading.RLock(), messages=deque(), event=lambda *args:events.append(args))
    for text in ['You: пользователь /private/file', 'AI: Window maximized.', 'SYS: Backend ready.']:
        DesktopRuntime.write_log(runtime, text)
        assert runtime.messages[-1]['text'] == text
    assert [item['role'] for item in runtime.messages] == ['user', 'assistant', 'system']


def test_backend_resource_keys_and_placeholders_match():
    root=Path(__file__).parents[2]/'local_mode/locales'
    en=json.loads((root/'en.json').read_text(encoding='utf-8'))
    ru=json.loads((root/'ru.json').read_text(encoding='utf-8'))
    assert en.keys()==ru.keys()
    import re
    for key in en:
        assert en[key]==key
        assert sorted(re.findall(r'\{\d+\}',en[key]))==sorted(re.findall(r'\{\d+\}',ru[key]))
