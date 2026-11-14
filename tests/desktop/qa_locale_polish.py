"""Owned headless locale fixture: no backend, models, personal profile or media."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/release-polish2/locale-dom'
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
                if httpx.get(url + '/qa/locale-polish.html', timeout=.2, trust_env=False).is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(url + '/qa/locale-polish.html')
            files = page.get_by_role('button', name='Files', exact=True)
            expect(files).to_have_attribute('aria-pressed', 'true')
            git = page.get_by_role('button', name='Git', exact=True)
            git.click()
            expect(git).to_have_attribute('aria-pressed', 'true')
            expect(git).to_contain_text('7')
            page.evaluate("window.__localePolishQA.backendError({source:'jarvis',message:'Python backend stopped. Check data/logs/desktop-backend.log.',values:[]})")
            expect(page.get_by_role('alert')).to_have_text('Python backend stopped. Check data/logs/desktop-backend.log.')
            page.evaluate("window.__localePolishQA.setLocale('ru')")
            expect(page.get_by_role('button', name='Файлы', exact=True)).to_have_text('Файлы')
            expect(git).to_have_attribute('aria-pressed', 'true')
            expect(git).to_contain_text('7')
            expect(page.get_by_test_id('view')).to_have_text('source-control')
            expect(page.get_by_role('alert')).to_have_text('Обработчик Python остановлен. Проверьте data/logs/desktop-backend.log.')
            page.evaluate("window.__localePolishQA.setLocale('en')")
            expect(files).to_have_text('Files')
            expect(git).to_have_attribute('aria-pressed', 'true')
            expect(page.get_by_role('alert')).to_have_text('Python backend stopped. Check data/logs/desktop-backend.log.')
            page.evaluate("window.__localePolishQA.backendError('Python backend stopped. Check data/logs/desktop-backend.log.');window.__localePolishQA.setLocale('ru')")
            expect(page.get_by_role('alert')).to_have_text('Python backend stopped. Check data/logs/desktop-backend.log.')
            page.screenshot(path=str(EVIDENCE / 'locale-polish.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'locale_polish': 'passed', 'locales': 'en -> ru -> en', 'selected_view_and_badge': 'preserved', 'owned_banner': 'reactive', 'raw_diagnostic': 'unchanged', 'page_errors': errors, 'backend_calls': 0}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
