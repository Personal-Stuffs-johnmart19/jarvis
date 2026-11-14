"""Настоящий скрытый Tauri WebView2; управление исключительно через CDP API."""

import os, subprocess, time, json, socket, uuid
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, Error as PlaywrightError

root = Path(__file__).resolve().parents[2]
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
qa_url = f"http://127.0.0.1:{port}"
env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "WEBVIEW2_USER_DATA_FOLDER": str(
        root / ".cache/desktop-test" / ("layout-" + uuid.uuid4().hex)
    ),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1",
}
log = (root / ".cache/tauri-native-test.log").open("w", encoding="utf-8")
process = subprocess.Popen(
    [
        str(
            root
            / "desktop-bin"
            / json.loads(
                (root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig")
            ).get("file", "jarvis-desktop.exe")
        )
    ],
    cwd=root,
    env=env,
    stdout=log,
    stderr=log,
    creationflags=0x08000000,
)

# The native store is QA-only. Restore its metadata after using a unique fixture session.
metadata=Path(os.environ['APPDATA'])/'com.mrcrous.jarvis.qa/terax-ai-sessions.json'
original=metadata.read_bytes() if metadata.exists() else None
fixture_id='layout-'+uuid.uuid4().hex
try:
    for _ in range(150):
        try:
            if httpx.get(qa_url+'/json/version',timeout=.3,trust_env=False).is_success:break
        except httpx.HTTPError:pass
        time.sleep(.1)
    with sync_playwright() as pw:
        browser=pw.chromium.connect_over_cdp(qa_url)
        page=next(p for c in browser.contexts for p in c.pages if 'tauri.localhost' in p.url)
        page.get_by_role('button',name='Chat',exact=True).wait_for()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        link_text='[Source](https://example.test/news) https://example.test/plain [Nested](https://example.test/a_(b)) [Blocked](javascript:alert(1))'
        results=[]
        for kind in ['short','long']:
            text='Short answer.' if kind=='short' else ('Paragraph with preserved wrapping. '*40+'\n\n')*24+'https://example.test/'+('long_segment_'*300)
            messages=[{'id':kind+'-user','role':'user','parts':[{'type':'text','text':text}]},{'id':kind+'-assistant','role':'assistant','parts':[{'type':'text','text':link_text+'\n\n'+text}]}]
            page.evaluate("""async value=>{
              await window.__JARVIS_NATIVE__.core.invoke('ai_session_save',new TextEncoder().encode(JSON.stringify(value.messages)),{headers:{'x-terax-session':JSON.stringify({id:value.id,durable:true})}});
              const s=await window.__JARVIS_NATIVE__.store.load('terax-ai-sessions.json',{defaults:{}});
              const sessions=(await s.get('sessions'))??[];
              await s.set('sessions',[{id:value.id,title:'Layout fixture',createdAt:Date.now(),updatedAt:Date.now()},...sessions.filter(x=>x.id!==value.id)]);
              await s.set('activeId',value.id);await s.save();
            }""",{'id':fixture_id,'messages':messages})
            page.reload()
            frame=page.frame_locator('iframe[src="workbench.html"]')
            frame.get_by_role('link',name='Source',exact=True).wait_for()
            assert not frame.locator('a[href^="javascript:"]').count()
            assert frame.get_by_role('link',name='Source',exact=True).get_attribute('href')=='https://example.test/news'
            for width,height in [(640,480),(800,600),(1000,661),(1280,720),(1440,900),(1920,1080),(2560,1440)]:
                page.set_viewport_size({'width':width,'height':height});page.wait_for_timeout(100)
                chat=frame.locator('[data-unified-chat]')
                assert chat.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),(kind,width,height)
                box=frame.locator('[data-shared-composer]').bounding_box()
                assert box and box['y']+box['height']<=height+1,(kind,width,height,box)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(kind,width,height)
                results.append({'kind':kind,'width':width,'height':height,'wrapped':True})
        assert not errors,errors
        (root/'.cache/message-layout-result.json').write_text(json.dumps(results,indent=2),encoding='utf8')
        print('PASS: canonical chat short/long output and safe links at 7 sizes; composer visible; no horizontal overflow.')
        page.evaluate("id=>window.__JARVIS_NATIVE__.core.invoke('ai_session_delete',{id})",fixture_id)
        page.evaluate("window.__JARVIS_NATIVE__.core.invoke('pty_close_all')")
        try:page.evaluate("window.__JARVIS_NATIVE__.core.invoke('plugin:window|close')")
        except PlaywrightError:pass
        browser.close()
    process.wait(timeout=20)
finally:
    if process.poll() is None:process.terminate();process.wait(timeout=15)
    if original is not None:metadata.write_bytes(original)
    log.close()
