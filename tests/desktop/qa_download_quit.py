"""Production Quit coordinator/dialog in owned headless Chromium; task deps synthetic."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/nov12-release/ui-download-quit'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}/qa/download-quit.html'
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
            request = page.get_by_role('button', name='Request Quit', exact=True)
            dialog = page.locator('dialog.jarvis-confirmation[open]')
            request.click()
            expect(dialog).to_contain_text('Model downloads are still running.')
            expect(dialog).to_contain_text('Partial downloads can resume later.')
            expect(dialog.get_by_role('button', name='Cancel', exact=True)).to_be_focused()
            page.screenshot(path=str(EVIDENCE / 'download-prompt.png'))
            dialog.get_by_role('button', name='Cancel', exact=True).click()
            expect(page.locator('#status')).to_have_text('Cancelled')
            assert page.evaluate('window.__downloadQuitQA.completions') == [{'id': 1, 'approved': False, 'downloads': []}]
            assert page.evaluate('window.__downloadQuitQA.workspaceVisits') == 0
            request.click()
            expect(dialog).to_contain_text('Model downloads are still running.')
            dialog.get_by_role('button', name='Confirm', exact=True).click()
            expect(dialog).to_contain_text('Unsaved files: 1.')
            dialog.get_by_role('button', name='Cancel', exact=True).click()
            expect(page.locator('#status')).to_have_text('Cancelled')
            assert page.evaluate('window.__downloadQuitQA.completions.at(-1)') == {'id': 2, 'approved': False, 'downloads': []}
            page.evaluate('window.__downloadQuitQA.dirty=false')
            request.click()
            expect(dialog).to_contain_text('Model downloads are still running.')
            dialog.get_by_role('button', name='Confirm', exact=True).click()
            expect(page.locator('#status')).to_have_text('Approved')
            assert page.evaluate('window.__downloadQuitQA.completions.at(-1)') == {'id': 3, 'approved': True, 'downloads': [910001, 910002]}
            assert page.evaluate('window.__downloadQuitQA.failures') == []
            assert not errors, errors
            browser.close()
        print(json.dumps({'download_quit_ui': 'passed', 'cancel': True, 'confirm_then_dirty_cancel': True, 'acknowledged_task_ids': True, 'scope': 'production coordinator/dialog; synthetic task and guard deps; no native IPC or download', 'page_errors': errors}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
