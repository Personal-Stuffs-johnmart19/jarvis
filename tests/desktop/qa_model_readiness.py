"""Production discovery hooks, disposable synthetic host, own headless Chromium only."""
import json
import sys
import os
from pathlib import Path
import socket
import subprocess
import time
import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/release-polish2/readiness-ui'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}/qa/model-readiness.html'
with (EVIDENCE / 'vite.log').open('w',encoding='utf8') as log:
    server = subprocess.Popen(['node',str(ROOT/'apps/desktop/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',str(port),'--strictPort'],cwd=ROOT/'apps/desktop',stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        for _ in range(150):
            try:
                if httpx.get(url,timeout=.2,trust_env=False).is_success: break
            except httpx.HTTPError: pass
            time.sleep(.1)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True); page=browser.new_page(); errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url)
            snapshot="JSON.parse(document.querySelector('#snapshot').textContent)"
            if '--refresh-only' in sys.argv:
                page.wait_for_function(f'{snapshot}.models.length===3')
                before=page.evaluate(f'{snapshot}.revision')
                page.evaluate('window.__modelReadinessQA.hold=true')
                page.get_by_role('button',name='Refresh',exact=True).click()
                page.wait_for_function('window.__modelReadinessQA.pending.length===4')
                assert page.evaluate(f'{snapshot}.revision')==before+1, 'Three picker hooks must coalesce to one shared metadata revision'
                assert page.evaluate(f'{snapshot}.models')==[]
                page.evaluate('for(let i=0;i<4;i++)window.__modelReadinessQA.finish(i,"unloaded")')
                page.wait_for_function(f'{snapshot}.models.length===3')
                assert page.evaluate('window.__modelReadinessQA.pending.length')==4
            else:
                page.wait_for_function(f'{snapshot}.models.length===3')
                assert page.evaluate(f'{snapshot}.lmReady')
                assert page.evaluate(f'{snapshot}.context')==65536
                page.evaluate('window.__modelReadinessQA.hold=true;window.__modelReadinessQA.lifecycle()')
                page.wait_for_function('window.__modelReadinessQA.pending.length===4')
                assert page.evaluate(f'{snapshot}.models')==[]
                assert not page.evaluate(f'{snapshot}.lmReady')
                page.evaluate('for(let i=0;i<4;i++)window.__modelReadinessQA.finish(i,"empty",true)')
                page.wait_for_timeout(100)
                assert page.evaluate(f'{snapshot}.models')==[]
                page.evaluate('window.__modelReadinessQA.hold=false;window.__modelReadinessQA.mode="unloaded";window.__modelReadinessQA.lifecycle()')
                page.wait_for_function(f'{snapshot}.models.length===3')
                rows=page.evaluate(f'{snapshot}.models')
                assert any('loads on first use' in row['description'] for row in rows)
                assert page.evaluate(f'{snapshot}.lmReady')
                page.evaluate('window.__modelReadinessQA.hold=true;window.__modelReadinessQA.lifecycle()')
                page.wait_for_function('window.__modelReadinessQA.pending.length===8')
                page.evaluate('window.__modelReadinessQA.lifecycle()')
                page.wait_for_function('window.__modelReadinessQA.pending.length===12')
                page.evaluate('for(let i=8;i<12;i++)window.__modelReadinessQA.finish(i,"empty")')
                page.wait_for_timeout(100)
                page.evaluate('for(let i=4;i<8;i++)window.__modelReadinessQA.finish(i,"loaded")')
                page.wait_for_timeout(100)
                assert page.evaluate(f'{snapshot}.models')==[]
                assert not page.evaluate(f'{snapshot}.lmReady')
                assert page.evaluate(f'{snapshot}.context')==8192
            page.screenshot(path=str(EVIDENCE/('manual-shared-refresh.png' if '--refresh-only' in sys.argv else 'empty-after-expired-response.png')))
            assert not errors, errors
            assert set(page.evaluate('window.__modelReadinessQA.calls'))=={'lm_catalog','lm_models','ollama_catalog','backend'}
            browser.close()
        print(json.dumps({'case':'manual-refresh' if '--refresh-only' in sys.argv else 'lifecycle-discovery','readiness_hooks':'passed','checks':['one shared revision','four metadata calls','pending conservative state','unloaded choices return'] if '--refresh-only' in sys.argv else ['unavailable clears catalog','unloaded remains selectable','expired response ignored'],'scope':'production hooks with disposable mock host; no model/network provider calls','page_errors':errors}))
    finally:
        server.terminate()
        try: server.wait(timeout=10)
        except subprocess.TimeoutExpired: server.kill();server.wait(timeout=5)
