"""Browser failures retain translated provenance without launching anything."""
import json
from unittest.mock import Mock

import pytest
from local_mode import browser_profiles as browser
from local_mode.messages import OwnedValueError, message


def test_invalid_saved_browser_mode_has_translatable_provenance(tmp_path, monkeypatch):
    prefs=tmp_path/'preferences.json'
    prefs.write_text(json.dumps({'mode':'invalid'}))
    monkeypatch.setattr(browser,'PREFERENCES',prefs)
    with pytest.raises(OwnedValueError) as caught:
        browser.read_preferences()
    payload=caught.value.payload()
    assert payload=={'source':'jarvis','message':'Unknown browser profile mode.','values':[]}
    assert message(payload['message'],locale='ru')=='Неизвестный режим профиля браузера.'


@pytest.mark.parametrize('url',['file:///private','https://user:secret@example.com','https://example.com/\n'])
def test_invalid_browser_url_rejects_before_browser_lookup(url,monkeypatch):
    lookup=Mock(side_effect=AssertionError('browser must not be touched'))
    monkeypatch.setattr(browser,'executable',lookup)
    with pytest.raises(OwnedValueError,match='Only valid HTTP/HTTPS'):
        browser.launch_arguments(url)
    lookup.assert_not_called()


def test_ambiguous_personal_profiles_reject_before_cdp_connection(monkeypatch):
    monkeypatch.setattr(browser,'read_preferences',lambda:{'mode':'current','profile':''})
    monkeypatch.setattr(browser,'profiles',lambda channel:([{'id':'Default'},{'id':'Profile 1'}],'Default'))
    with pytest.raises(OwnedValueError,match='CDP session') as caught:
        browser.control_endpoint()
    assert caught.value.payload()['source']=='jarvis'
