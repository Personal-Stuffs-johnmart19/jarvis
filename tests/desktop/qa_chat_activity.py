"""Actual chat rendering, synthetic parts, owned headless browser only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import httpx
import psutil
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = Path(os.environ.get('JARVIS_QA_EVIDENCE_DIR', str(ROOT / '.cache/verification/chat-activity'))).resolve()
if not EVIDENCE.is_relative_to((ROOT / '.cache/verification').resolve()):
    raise SystemExit('Chat QA evidence must stay within the checkout verification cache')
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}/qa/chat-activity.html'
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
            activity = page.get_by_role('button', name='Activity', exact=True)
            expect(activity).to_have_count(1)
            expect(activity).to_have_attribute('aria-expanded', 'false')
            expect(page.get_by_text('The weather answer remains visible.', exact=True)).to_be_visible()
            expect(page.get_by_text('Synthetic visible failure', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='Approve', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='Deny', exact=True)).to_be_visible()
            expect(page.get_by_text('mcp_catalog', exact=True)).not_to_be_visible()
            expect(page.get_by_text('Private reasoning fixture', exact=True)).not_to_be_visible()
            # The shell hides the shared chat when showing Files & Tools.
            # Its iframe can remain unfocused when brought back. Finite
            # entrances must finish; only decorative loops may remain paused.
            page.evaluate("document.documentElement.setAttribute('data-window-inactive', '')")
            for deferred in (False, True):
                page.evaluate('(value) => window.__chatActivityQA.deferred(value)', deferred)
                for _ in range(2):
                    page.evaluate('window.__chatActivityQA.surface(false)')
                    expect(page.locator('main')).not_to_be_visible()
                    page.evaluate('window.__chatActivityQA.remount()')
                    page.evaluate('window.__chatActivityQA.surface(true)')
                    expect(page.locator('main')).to_be_visible()
                    page.wait_for_function("[...document.querySelectorAll('.is-assistant .animate-in')].length > 0 && [...document.querySelectorAll('.is-assistant .animate-in')].every(n => Number(getComputedStyle(n).opacity) > .99)")
                    expect(page.get_by_text('The weather answer remains visible.', exact=True)).to_be_visible()
                    expect(page.get_by_text('Synthetic visible failure', exact=True)).to_be_visible()
                    expect(page.get_by_role('button', name='Approve', exact=True)).to_be_visible()
                    assert page.locator('[data-loop-fixture]').evaluate('n => getComputedStyle(n).animationPlayState') == 'paused'
            page.screenshot(path=str(EVIDENCE / 'returned-chat-inactive.png'))
            # Expanding activity while unfocused must expose its contents too.
            activity = page.get_by_role('button', name='Activity', exact=True)
            activity.focus()
            page.keyboard.press('Enter')
            expect(activity).to_have_attribute('aria-expanded', 'true')
            catalog = page.get_by_role('button', name='mcp_catalog', exact=False)
            expect(catalog).to_be_visible()
            expect(page.get_by_text('jarvis_catalog', exact=True)).to_be_visible()
            expect(page.get_by_text('raw catalog fixture', exact=False)).not_to_be_visible()
            catalog.click()
            expect(page.get_by_text('raw catalog fixture', exact=False)).to_be_visible()
            page.evaluate('window.__chatActivityQA.surface(false)')
            expect(page.locator('main')).not_to_be_visible()
            page.evaluate('window.__chatActivityQA.surface(true)')
            expect(page.get_by_text('raw catalog fixture', exact=False)).to_be_visible()
            page.wait_for_function("[...document.querySelectorAll('.terax-collapsible-content[data-state=open]')].every(n => +getComputedStyle(n).opacity > .99 && n.getBoundingClientRect().height > 0)")
            page.get_by_role('button', name='Approve', exact=True).click()
            page.get_by_role('button', name='Deny', exact=True).click()
            assert page.evaluate('window.__chatActivityQA.responses') == [['approval-fixture', True], ['approval-fixture', False]]
            activity.click()
            page.evaluate('window.__chatActivityQA.locale("ru")')
            expect(page.get_by_role('button', name='Действия', exact=True)).to_have_attribute('aria-expanded', 'false')
            expect(page.get_by_role('button', name='Подтвердить', exact=True)).to_be_visible()
            expect(page.get_by_text('mcp_catalog', exact=True)).not_to_be_visible()
            expect(page.get_by_text('raw catalog fixture', exact=False)).not_to_be_visible()
            page.screenshot(path=str(EVIDENCE / 'collapsed-ru.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'chat_activity': 'passed', 'default_collapsed': True, 'surface_switch_inactive': True, 'deferred_history': True, 'background_loops_paused': True, 'keyboard_expand': True, 'approvals_and_failure_visible': True, 'locales': ['en', 'ru'], 'page_errors': errors, 'native_calls': 0}))
    finally:
        try:
            descendants = psutil.Process(server.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            descendants = []
        for child in descendants:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        _, alive = psutil.wait_procs(descendants, timeout=3)
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
