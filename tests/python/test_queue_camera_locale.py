"""Offline queue/camera localization preserves user text and safety state."""
from unittest.mock import Mock

import pytest

from local_mode import camera_devices
from local_mode.messages import OwnedRuntimeError, OwnedText, OwnedValueError, message
from local_mode.playback_queue import PlaybackQueue, video_url
from local_mode.results import user_message

VIDEO = 'https://www.youtube.com/watch?v=abcdefghijk'


def test_playlist_name_and_saved_urls_survive_locale_changes_and_restart(tmp_path):
    queue = PlaybackQueue(tmp_path / 'queue.json')
    browser = Mock(side_effect=AssertionError('No browser access expected'))
    queue.execute({'action': 'queue_add', 'urls': [VIDEO]}, browser, {})
    name = 'Музыка {0} / Window maximized.'
    result = queue.execute({'action': 'playlist_save', 'playlist': name}, browser, {})
    assert user_message(result) == f'Playlist “{name}” saved locally in Jarvis.'
    assert user_message(result, locale='ru') == f'Плейлист «{name}» сохранён локально в Jarvis.'
    restored = PlaybackQueue(queue.path)
    assert restored.playlists == {name: [VIDEO]}
    assert restored.items == [VIDEO]
    assert user_message(restored.state()) == 'Videos in queue: 1. Playlists: 1.'
    assert user_message(restored.state(), locale='ru') == 'Видео в очереди: 1. Плейлистов: 1.'
    browser.assert_not_called()


def test_invalid_url_keeps_value_error_contract_and_separate_locale():
    with pytest.raises(ValueError) as caught:
        video_url('file:///private/file')
    error = caught.value
    assert isinstance(error, OwnedValueError)
    assert str(error) == 'A standard YouTube video URL is required.'
    assert message(error.template, *error.values, locale='ru') == 'Нужна обычная ссылка YouTube на видео.'


def test_queue_pause_report_is_owned_and_does_not_mutate_another_video(tmp_path):
    queue = PlaybackQueue(tmp_path / 'queue.json')
    queue.items = [VIDEO]
    queue.position = 0
    queue.playing = True
    queue.browser = Mock(return_value={'ok': True, 'media': {'video': True, 'url': 'https://youtu.be/lmnopqrstuv'}})
    reports = []
    queue.report = reports.append
    queue.tick()
    assert not queue.playing and queue.browser.call_count == 1
    assert isinstance(reports[0], OwnedText)
    assert user_message({'ok': True, 'reply': reports[0]}, locale='ru') == 'Очередь приостановлена: видео на вкладке изменилось.'


def test_camera_disabled_error_is_owned_without_enumeration_or_capture(monkeypatch):
    monkeypatch.setattr(camera_devices, '_selected', None)
    monkeypatch.setattr(camera_devices, 'list_cameras', Mock(side_effect=AssertionError('No device access expected')))
    with pytest.raises(RuntimeError) as caught:
        camera_devices.selected_camera()
    error = caught.value
    assert isinstance(error, OwnedRuntimeError)
    assert str(error) == 'Camera disabled. Allow access in Settings → Advanced Settings → Camera.'
    assert message(error.template, locale='ru') == 'Камера отключена. Разрешите доступ в Настройки → Дополнительные настройки → Камера.'
    device = camera_devices.CameraDevice(2, 'Камера пользователя', 0)
    monkeypatch.setattr(camera_devices, '_selected', device)
    assert camera_devices.selected_camera() is device


def test_queue_rpc_presentation_localizes_owned_fields_and_preserves_payload(tmp_path):
    import json
    from local_mode.results import present_result
    queue = PlaybackQueue(tmp_path / 'queue.json')
    result = queue.execute({'action': 'queue_add', 'urls': [VIDEO]}, Mock(), {})
    presented = present_result(result, locale='ru')
    payload = json.loads(json.dumps(presented))
    assert payload['reply'] == 'Видео в очереди: 1. Плейлистов: 0.'
    assert {key: value for key, value in payload.items() if key != 'reply'} == {key: value for key, value in result.items() if key != 'reply'}
    assert isinstance(result['reply'], OwnedText)
    raw = {'ok': False, 'error': 'Window maximized. {0}', 'reply': 'Provider text', 'result': 'Reminder.'}
    assert present_result(raw, locale='ru') == raw
    owned_failure = {'ok': False, 'error': OwnedText('Monitoring: {0}', 'Provider raw {1}')}
    assert present_result(owned_failure, locale='ru')['error'] == 'Мониторинг: Provider raw {1}'
