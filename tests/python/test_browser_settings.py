"""Synthetic profile discovery and temporary DPAPI credentials only; no browser launch."""
import json
import sys
import pytest
from local_mode import browser_profiles as browser, chrome_credentials as credentials

@pytest.mark.parametrize('current', ['', 'Profile 99', 'Default', 'Profile 2'])
def test_profile_current_is_available(tmp_path, monkeypatch, current):
    monkeypatch.setattr(browser, 'user_data', lambda channel: tmp_path)
    (tmp_path / 'Local State').write_text(json.dumps({'profile': {'last_used': current, 'info_cache': {'Default': {'name': 'One'}, 'Profile 2': {'name': 'Two'}, '../bad': {}}}}))
    profiles, selected = browser.profiles()
    assert [item['id'] for item in profiles] == ['Default', 'Profile 2']
    assert selected == (current if current in {'Default', 'Profile 2'} else 'Default')

def test_empty_profiles_never_invents_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, 'user_data', lambda channel: tmp_path)
    (tmp_path / 'Local State').write_text('{"profile":{"last_used":"Default"}}')
    assert browser.profiles() == ([], '')

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows DPAPI')
def test_token_is_encrypted_and_bound_to_profile_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, 'DIRECTORY', tmp_path)
    token = 'synthetic_playwright_token_123456789'
    credentials.save_token('Default', 'PLAYWRIGHT_MCP_EXTENSION_TOKEN="' + token + '"')
    stored = credentials.token_path('Default')
    assert token.encode() not in stored.read_bytes()
    assert credentials.load_token('Default') == token
    assert credentials.has_token('Default')
    assert credentials.load_token('Profile 2') is None
    assert credentials.load_token('Default', 'msedge') is None
    assert list(tmp_path.glob('*.tmp')) == []
    credentials.delete_token('Default')
    assert credentials.load_token('Default') is None

def test_invalid_token_never_writes_or_echoes_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, 'DIRECTORY', tmp_path)
    invalid = 'synthetic secret with whitespace'
    with pytest.raises(ValueError) as caught:
        credentials.save_token('Default', invalid)
    assert invalid not in str(caught.value)
    assert list(tmp_path.iterdir()) == []
