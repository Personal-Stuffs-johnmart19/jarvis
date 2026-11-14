"""Production menu controls in owned headless Chromium; stores/actions are synthetic."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/release-polish2/ui-menu-dom'
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
            editor = page.locator('[data-custom-instructions] textarea')
            save = page.locator('[data-custom-instructions] button')
            expect(editor).to_have_value('')
            page.evaluate("window.__menuPolishQA.update('Hydrated instructions')")
            expect(editor).to_have_value('Hydrated instructions')
            editor.fill('Unsaved draft')
            page.evaluate("window.__menuPolishQA.update('External update')")
            expect(editor).to_have_value('Unsaved draft')
            page.evaluate("window.__menuPolishQA.locale('ru')")
            expect(editor).to_have_value('Unsaved draft')
            editor.fill('')
            expect(save).to_be_enabled()
            save.click()
            expect(save).to_be_disabled()
            assert page.evaluate('window.__menuPolishQA.saves') == ['']
            page.evaluate('window.__menuPolishQA.fail=true')
            editor.fill('Keep failed draft')
            save.click()
            expect(page.get_by_role('alert')).to_contain_text('Synthetic save failure')
            expect(editor).to_have_value('Keep failed draft')
            expect(save).to_be_enabled()
            page.evaluate("window.__menuPolishQA.locale('en')")
            card = page.locator('[data-theme-card="fixture:theme"]')
            assert card.locator('button button').count() == 0
            select = card.locator('button').nth(0)
            expect(select).to_have_attribute('aria-pressed', 'true')
            select.focus()
            page.keyboard.press('Tab')
            edit = card.get_by_role('button', name='Edit Fixture theme', exact=True)
            expect(edit).to_be_focused()
            expect(edit).to_have_css('opacity', '1')
            page.keyboard.press('Enter')
            page.keyboard.press('Tab')
            remove = card.get_by_role('button', name='Remove Fixture theme', exact=True)
            expect(remove).to_be_focused()
            page.keyboard.press('Space')
            assert page.evaluate('window.__menuPolishQA.actions') == ['edit', 'remove']
            expect(page.locator('[data-theme-card="builtin:theme"] button')).to_have_count(1)
            # User text deliberately matches catalogue keys: ownership must keep it literal.
            description = 'Clean neutral greys with a full-color terminal.'
            page.evaluate("window.__menuPolishQA.locale('ru')")
            expect(card).to_contain_text(description)
            expect(page.locator('[data-theme-card="builtin:theme"]')).not_to_contain_text(description)
            builtin = page.locator('[data-agent="builtin:coder"]')
            expect(builtin).not_to_contain_text('General-purpose coding assistant. Writes, edits, and runs.')
            switcher = page.get_by_role('region', name='agent switcher')
            switcher.get_by_role('button').click()
            custom_item = page.get_by_role('menuitem').filter(has_text='General-purpose coding assistant. Writes, edits, and runs.')
            expect(custom_item).to_have_count(1)
            expect(custom_item).to_contain_text('Coder')
            custom_item.click()
            assert page.evaluate('window.__menuPolishQA.selections') == ['fixture:custom']
            expect(switcher.get_by_role('button')).to_contain_text('Coder')
            page.evaluate("window.__menuPolishQA.locale('en')")
            expect(editor).to_have_value('Keep failed draft')
            page.screenshot(path=str(EVIDENCE / 'menu-polish.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'menu_polish_ui': 'passed', 'clear_instructions': True, 'clean_hydration': True, 'dirty_draft_preserved': True, 'failed_save_preserved': True, 'theme_keyboard_actions': True, 'builtin_localization_custom_literals': True, 'page_errors': errors, 'scope': 'production components with synthetic save/actions; no native IPC/models'}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
