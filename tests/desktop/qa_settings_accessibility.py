"""Changed settings accessibility in a private headless Chromium; all host RPC is fake."""
import json
import os
import re
from pathlib import Path
import socket
import subprocess
import time
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/release-polish2/ui-settings-accessibility'
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
            endpoint = page.get_by_role('region', name='Endpoint fixture', exact=True)
            endpoint.locator('button[aria-controls]').click()
            for label in ['Name', 'Base URL', 'Model ID', 'Context', 'API key']:
                control = endpoint.get_by_label(label, exact=True)
                expect(control).to_have_count(1)
                assert control.evaluate("input => input.labels.length === 1"), label
                label_node = endpoint.locator('label').filter(has_text=label)
                # Exact text avoids matching optional nested explanatory text.
                label_node = label_node.filter(has_text=re.compile('^' + re.escape(label) + '$'))
                label_node.click()
                expect(control).to_be_focused()
            provider = page.get_by_role('region', name='Provider key fixture', exact=True)
            expect(provider.get_by_text('Configured', exact=True)).to_have_count(1)
            expect(provider.get_by_text('Connected', exact=True)).to_have_count(0)
            provider.get_by_role('button', name='Replace', exact=True).click()
            key = provider.get_by_label('API key', exact=True)
            key.fill('sk-synthetic-only')
            expect(key).to_have_attribute('type', 'password')
            key.focus()
            page.keyboard.press('Tab')
            reveal = provider.get_by_role('button', name='Show key', exact=True)
            expect(reveal).to_be_focused()
            page.keyboard.press('Enter')
            expect(key).to_have_attribute('type', 'text')
            expect(provider.get_by_role('button', name='Hide key', exact=True)).to_have_attribute('aria-pressed', 'true')
            page.keyboard.press('Space')
            expect(key).to_have_attribute('type', 'password')
            expect(provider.get_by_role('button', name='Show key', exact=True)).to_have_attribute('aria-pressed', 'false')
            page.screenshot(path=str(EVIDENCE / 'settings-accessibility.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'settings_accessibility': 'passed', 'associated_input_labels': 5, 'keyboard_reveal': ['Enter', 'Space'], 'unverified_key_badge': 'Configured', 'rpc': 'mock only', 'prior_checks_repeated': False}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
