"""Actual DOM checks with an owned headless browser and fake RPC; no audio or providers."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/nov12-release/ui-settings-controls'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}'
with (EVIDENCE / 'vite.log').open('w', encoding='utf8') as log:
    server = subprocess.Popen(['node', str(ROOT / 'apps/desktop/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=ROOT / 'apps/desktop', stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        for _ in range(150):
            try:
                if httpx.get(url + '/qa/settings-controls.html', timeout=.2, trust_env=False).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url + '/qa/settings-controls.html')
            speech = page.get_by_role('region', name='Speech fixture', exact=True)
            speech.get_by_label('TTS synthesis model', exact=True).select_option('silero')
            listen = speech.get_by_role('button', name='Listen', exact=True)
            listen.click()
            expect(listen).to_be_disabled()
            stop = speech.get_by_role('button', name='Stop', exact=True)
            expect(stop).to_be_enabled()
            assert stop.evaluate("button => button.closest('fieldset[disabled]') === null")
            stop.click()
            assert page.evaluate("window.__settingsControlsQA.calls.some(call => call.method === 'control' && call.params.action === 'speech_stop')")
            page.evaluate("window.__settingsControlsQA.finishPreview({ok:true})")
            expect(listen).to_be_enabled()
            endpoint = page.get_by_role('region', name='Endpoint fixture', exact=True)
            expect(endpoint.locator('button button')).to_have_count(0)
            expand = endpoint.locator('button[aria-controls]')
            remove = endpoint.get_by_role('button', name='Remove endpoint', exact=True)
            expect(expand).to_have_attribute('aria-expanded', 'false')
            expand.focus()
            page.keyboard.press('Tab')
            expect(remove).to_be_focused()
            remove.click()
            assert page.evaluate('window.__settingsControlsQA.removed') == 1
            expect(expand).to_have_attribute('aria-expanded', 'false')
            expand.click()
            expect(expand).to_have_attribute('aria-expanded', 'true')
            endpoint.get_by_role('button', name='Test', exact=True).click()
            expect(endpoint.get_by_role('status')).to_contain_text('Server responded with status 500')
            expect(expand).to_contain_text('Configured')
            expect(endpoint.get_by_text('Connected', exact=True)).to_have_count(0)
            expect(endpoint.get_by_text('Server reachable', exact=True)).to_have_count(0)
            page.screenshot(path=str(EVIDENCE / 'settings-controls.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'settings_controls': 'passed', 'stop_enabled_during_preview': True, 'endpoint_buttons': 'siblings', 'failed_endpoint_badge': 'Configured', 'rpc': 'mock only', 'audio_calls': 0}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
