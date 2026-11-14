"""Mounted approval locale/long-path regression; own headless browser, synthetic callbacks only."""
import json,os,socket,subprocess,time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'.cache/verification/approval-locale'
OUT.mkdir(parents=True,exist_ok=True)
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
url=f'http://127.0.0.1:{port}'
with (OUT/'vite.log').open('w',encoding='utf8') as log:
 server=subprocess.Popen(['node',str(ROOT/'apps/desktop/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',str(port),'--strictPort'],cwd=ROOT/'apps/desktop',stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
 try:
  for _ in range(150):
   try:
    if httpx.get(url+'/qa/approval-locale.html',timeout=.2,trust_env=False).is_success: break
   except httpx.HTTPError: pass
   time.sleep(.1)
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True)
   page=browser.new_page(viewport={'width':640,'height':900},color_scheme='light')
   errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(url+'/qa/approval-locale.html')
   names=['write_file','edit','multi_edit','create_directory','bash_run','bash_background']
   labels=['Write file','Edit file','Edit file (batch)','Create directory','Run shell command','Spawn background process']
   page.wait_for_function('!!window.__approvalLocaleQA')
   page.evaluate('window.__before={parts:window.__approvalLocaleQA.parts,callbacks:window.__approvalLocaleQA.callbacks,input:window.__approvalLocaleQA.input,serialized:JSON.stringify(window.__approvalLocaleQA.parts)}')
   catalogs={l:json.loads((ROOT/f'apps/desktop/src/locales/{l}.json').read_text(encoding='utf8')) for l in ['en','ru']}
   for locale in ['en','ru','en']:
    page.evaluate('l=>window.__approvalLocaleQA.locale(l)',locale)
    for name,label in zip(names,labels):
     expected=catalogs[locale].get(label,label)
     expect(page.get_by_role('region',name=name,exact=True).get_by_text(expected,exact=True)).to_be_visible()
    expect(page.get_by_role('region',name='unknown_raw_tool',exact=True).get_by_text('unknown_raw_tool',exact=True)).to_be_visible()
    assert page.evaluate('window.__approvalLocaleQA.parts===window.__before.parts && window.__approvalLocaleQA.callbacks===window.__before.callbacks && window.__approvalLocaleQA.input===window.__before.input && JSON.stringify(window.__approvalLocaleQA.parts)===window.__before.serialized')
    assert page.evaluate('window.__approvalLocaleQA.responses.length')==0
    for name in names:
     expect(page.get_by_role('region',name=name,exact=True).get_by_text(page.evaluate('window.__approvalLocaleQA.path'),exact=True)).to_be_visible()
   page.evaluate('window.__approvalLocaleQA.locale("ru")')
   expect(page.get_by_role('region',name='write_file',exact=True).get_by_text(catalogs['ru']['Write file'],exact=True)).to_be_visible()
   geometry=[]
   for width in [640,320]:
    page.set_viewport_size({'width':width,'height':900})
    page.screenshot(path=str(OUT/f'approvals-light-{width}.png'),full_page=True)
    geometry.append({'width':width,'document':page.evaluate('({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})'),'cards':page.get_by_role('region').evaluate_all('(nodes)=>nodes.map(n=>({name:n.getAttribute("aria-label"),client:n.clientWidth,scroll:n.scrollWidth,buttons:[...n.querySelectorAll("button")].map(b=>({text:b.innerText,right:b.getBoundingClientRect().right}))}))')})
   assert all(row['document']['scroll']<=row['document']['client'] for row in geometry), geometry
   page.get_by_role('region',name='write_file',exact=True).get_by_role('button',name=catalogs['ru']['Deny'],exact=True).click()
   page.get_by_role('region',name='bash_run',exact=True).get_by_role('button',name=catalogs['ru']['Approve'],exact=True).click()
   assert page.evaluate('window.__approvalLocaleQA.responses')==[{'name':'write_file','approved':False},{'name':'bash_run','approved':True}]
   assert not errors,errors
   report={'locale_flow':'en-ru-en passed','descriptors':6,'identity':'parts/input/callback same references and unchanged serialized payload','raw_unknown':'preserved','callbacks':'synthetic only; deny/approve delivered once','geometry':geometry,'page_errors':errors,'native_zoom':'not exercised; CSS-width proxy only','personal_browser':False}
   (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
   print(json.dumps({k:v for k,v in report.items() if k!='geometry'},ensure_ascii=False))
   browser.close()
 finally:
  server.terminate()
  try: server.wait(timeout=10)
  except subprocess.TimeoutExpired: server.kill();server.wait(timeout=5)
