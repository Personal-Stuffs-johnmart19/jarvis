"""Mounted retained-feedback and inherited-ID regressions; own headless browser, mocked host only."""
import json,os,socket,subprocess,time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'.cache/verification/live-feedback'
OUT.mkdir(parents=True,exist_ok=True)
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
url=f'http://127.0.0.1:{port}'
with (OUT/'vite.log').open('w',encoding='utf8') as log:
 server=subprocess.Popen(['node',str(ROOT/'apps/desktop/node_modules/vite/bin/vite.js'),'--config','qa/vite.live-feedback.config.ts','--host','127.0.0.1','--port',str(port),'--strictPort'],cwd=ROOT/'apps/desktop',stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
 try:
  for _ in range(150):
   try:
    if httpx.get(url+'/qa/live-feedback.html',timeout=.2,trust_env=False).is_success: break
   except httpx.HTTPError: pass
   time.sleep(.1)
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True)
   page=browser.new_page(viewport={'width':640,'height':900},color_scheme='light')
   errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(url+'/qa/live-feedback.html')
   page.wait_for_function('!!window.__liveFeedbackQA')
   catalogs={}
   for l in ['en','ru']:
    catalogs[l]={**json.loads((ROOT/f'local_mode/locales/{l}.json').read_text(encoding='utf8')),**json.loads((ROOT/f'apps/desktop/src/locales/{l}.json').read_text(encoding='utf8'))}
   def tr(l,key,*args):
    text=catalogs[l].get(key,key)
    for i,value in enumerate(args):text=text.replace('{'+str(i)+'}',str(value))
    return text
   def flow(locator,expected):
    before=page.evaluate('JSON.stringify(window.__liveFeedbackQA.calls)')
    for l in ['en','ru','en']:
     page.evaluate('l=>window.__liveFeedbackQA.locale(l)',l)
     expect(locator).to_have_text(expected(l))
     assert page.evaluate('JSON.stringify(window.__liveFeedbackQA.calls)')==before
   for name in ['constructor','toString','__proto__']:
    flow(page.get_by_role('region',name=name,exact=True).get_by_text(name,exact=True),lambda l,n=name:n)
   assert page.evaluate('window.__liveFeedbackQA.responses')==[]
   models=page.get_by_role('region',name='models',exact=True)
   models.locator('summary').click()
   expect(models.get_by_role('button',name='Activate',exact=True)).to_be_visible()
   status=models.get_by_role('status')
   rawpath=page.evaluate('window.__liveFeedbackQA.rawPath')
   models.get_by_role('button',name='Activate',exact=True).click()
   expect(status).to_contain_text(rawpath)
   flow(status,lambda l:tr(l,'Failed to activate: {0}',tr(l,'Model file missing: {0}',rawpath)))
   page.evaluate('window.__liveFeedbackQA.mode="raw"')
   models.get_by_role('button',name='Activate',exact=True).click()
   expect(status).to_contain_text('Model file missing: {0}')
   flow(status,lambda l:tr(l,'Failed to activate: {0}','Model file missing: {0}'))
   page.evaluate('window.__liveFeedbackQA.mode="pending"')
   models.get_by_role('button',name='Activate',exact=True).click()
   flow(status,lambda l:tr(l,'Activating model…'))
   page.evaluate('window.__liveFeedbackQA.resolve({running:true})')
   expect(status).to_contain_text(page.evaluate('window.__liveFeedbackQA.rawRepo'))
   flow(status,lambda l:tr(l,"Activated '{0}' successfully.",page.evaluate('window.__liveFeedbackQA.rawRepo')))
   attach=page.get_by_role('region',name='attachments',exact=True)
   details=attach.locator('[data-shared-attachments] > details:not([data-document-library])')
   details.locator('summary').click()
   page.evaluate('window.__liveFeedbackQA.mode="owned"')
   details.get_by_role('button',name='Run embedding',exact=True).click()
   embed=details.get_by_role('status')
   expect(embed).to_contain_text(rawpath)
   flow(embed,lambda l:tr(l,'Model file missing: {0}',rawpath))
   page.evaluate('window.__liveFeedbackQA.mode="raw"')
   details.get_by_role('button',name='Run embedding',exact=True).click()
   flow(embed,lambda l:'Model file missing: {0}')
   page.evaluate('window.__liveFeedbackQA.mode="success"')
   details.get_by_role('button',name='Run embedding',exact=True).click()
   expect(embed).to_contain_text('384')
   flow(embed,lambda l:tr(l,'{0}: {1} · {2} dimensions · {3} tokens','Read file{0}.txt','Read file{0}',384,12))
   library=attach.locator('[data-document-library]')
   library.locator('summary').click()
   library.get_by_role('button',name='Refresh source',exact=True).click()
   notice=library.get_by_role('status')
   flow(notice,lambda l:tr(l,'Source updated. Re-embed to refresh semantic search.'))
   expect(library.get_by_text('Read file{0}.txt',exact=False)).to_be_visible()
   assert page.evaluate('window.__liveFeedbackQA.responses')==[]
   assert not errors,errors
   calls=page.evaluate('window.__liveFeedbackQA.calls')
   assert calls.count('models.activate')==3,calls
   assert calls.count('documents.embed')==3,calls
   assert calls.count('documents.refresh')==1,calls
   report={'status':'passed','inherited_ids':['constructor','toString','__proto__'],'live_existing_states':['activation owned error/raw collision/progress/success','embedding owned error/raw collision/success','document changed-source notice'],'locale':'en-ru-en each existing state','requests':calls,'locale_replay':False,'raw_path_file_repo_model':'preserved including braces/catalog collisions','callbacks':0,'page_errors':errors,'scope':'real mounted components; composer dependency stub; native/backend mocked; no upload/inference/execution/native zoom'}
   (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
   page.screenshot(path=str(OUT/'live-feedback.png'),full_page=True)
   print(json.dumps(report,ensure_ascii=False))
   browser.close()
 finally:
  server.terminate()
  try: server.wait(timeout=10)
  except subprocess.TimeoutExpired: server.kill();server.wait(timeout=5)