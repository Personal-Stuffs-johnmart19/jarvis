"""Owned headless browser + Vite source fixture; fake RPC only, no personal browser or APIs."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/browser-token'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}'
with (EVIDENCE / 'vite.log').open('w', encoding='utf8') as log:
    # tools/use_node.ps1 pins PATH before this runner; executable overrides are not accepted.
    server = subprocess.Popen(['node', str(ROOT / 'apps/desktop/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=ROOT / 'apps/desktop', stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        for _ in range(150):
            try:
                if httpx.get(url + '/qa/browser-settings.html', timeout=.2, trust_env=False).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url + '/qa/browser-settings.html')
            mode = page.get_by_role('combobox', name='Profile Mode', exact=True)
            profile = page.get_by_role('combobox', name='Profile', exact=True)
            token = page.get_by_role('textbox', name='Playwright Extension Token', exact=True)
            save = page.get_by_role('button', name='Save token for this profile', exact=True)
            expect(profile).to_be_disabled()
            expect(save).to_be_disabled()
            # Preferences arriving after mount must hydrate before the list resolves.
            page.evaluate("window.__browserSettingsQA.setPreferences({mode:'specific',profile:'Profile 2'})")
            page.evaluate("window.__browserSettingsQA.finishProfiles({profiles:[{id:'Default',name:'Fixture One'},{id:'Profile 2',name:'Fixture Two'}],current:''})")
            expect(mode).to_have_value('specific')
            expect(profile).to_have_value('Profile 2')
            page.evaluate("window.__browserSettingsQA.setPreferences({mode:'specific',profile:'Profile 99'})")
            expect(profile).to_have_value('')
            expect(save).to_be_disabled()
            expect(token).to_be_disabled()
            page.evaluate("window.__browserSettingsQA.setPreferences({mode:'current',profile:''})")
            expect(profile).to_have_value('Default')
            expect(token).to_be_enabled()
            token.fill('synthetic_browser_token_123456789')
            expect(save).to_be_enabled()
            page.evaluate("window.__browserSettingsQA.setPreferences({mode:'specific',profile:'Profile 2'})")
            expect(profile).to_have_value('Default')
            save.click()
            expect(token).to_have_value('')
            assert page.evaluate("window.__browserSettingsQA.calls.map(call => [call.method,call.params.profile])") == [['chrome.token', 'Default']]
            expect(profile.locator('option:checked')).to_contain_text('token saved')
            token.fill('synthetic_unsaved_token_123456789')
            profile.select_option('Profile 2')
            expect(mode).to_have_value('specific')
            expect(token).to_have_value('')
            # Background snapshots cannot overwrite deliberate unsaved edits.
            page.evaluate("window.__browserSettingsQA.setPreferences({mode:'specific',profile:'Default'})")
            expect(profile).to_have_value('Profile 2')
            token.fill('synthetic_second_token_123456789')
            save.click()
            expect(token).to_have_value('')
            assert page.evaluate("window.__browserSettingsQA.calls.map(call => call.params.profile)") == ['Default', 'Profile 2']
            mode.select_option('new')
            expect(token).to_have_count(0)
            expect(profile).to_be_disabled()
            page.screenshot(path=str(EVIDENCE / 'browser-settings.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'browser_settings_ui': 'passed', 'source_fixture': True, 'rpc_calls':'mock only', 'personal_browser':False}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
