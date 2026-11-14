"""Focused production AgentCard keyboard check; synthetic actions, owned browser."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--imports-only', action='store_true', help='Resolve actual ThemesSection source graph without repeating card checks')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/release-polish2' / ('ui-menu-theme-import' if args.imports_only else 'ui-menu-agent-card')
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}/qa/menu-polish.html'
with (EVIDENCE / 'vite.log').open('w', encoding='utf8') as log:
    server = subprocess.Popen(['node', str(ROOT / 'apps/desktop/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(port), '--strictPort'], cwd=ROOT / 'apps/desktop', stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        for _ in range(150):
            try:
                if httpx.get(url, timeout=.2, trust_env=False).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url)
            assert page.evaluate('window.__menuPolishQA.importThemes()'), 'Actual ThemesSection module did not resolve'
            if not args.imports_only:
                card = page.get_by_role('region', name='custom agent card')
                select = card.get_by_role('button', name='Active', exact=True)
                expect(select).to_have_attribute('aria-pressed', 'true')
                select.focus()
                page.keyboard.press('Tab')
                edit = card.get_by_role('button', name='Edit Coder', exact=True)
                expect(edit).to_be_focused()
                assert edit.evaluate('(element) => {let node=element;while(node){if(getComputedStyle(node).opacity==="0")return false;node=node.parentElement;}return true;}')
                page.keyboard.press('Enter')
                page.keyboard.press('Tab')
                remove = card.get_by_role('button', name='Remove Coder', exact=True)
                expect(remove).to_be_focused()
                assert remove.evaluate('(element) => {let node=element;while(node){if(getComputedStyle(node).opacity==="0")return false;node=node.parentElement;}return true;}')
                page.keyboard.press('Space')
                assert page.evaluate('window.__menuPolishQA.actions') == ['agent-edit', 'agent-remove']
                assert card.locator('button button').count() == 0
                page.screenshot(path=str(EVIDENCE / 'agent-card-focus.png'))
            assert not errors, errors
            browser.close()
        if args.imports_only:
            print(json.dumps({'menu_source_import': 'passed', 'module': 'actual ThemesSection', 'page_errors': errors, 'native_calls': 0}))
        else:
            print(json.dumps({'agent_card_keyboard': 'passed', 'visible_focus_actions': True, 'semantic_names': True, 'exact_actions': ['edit', 'remove'], 'page_errors': errors, 'native_calls': 0}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
