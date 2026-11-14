"""Focused synthetic explicit-Quit QA inside the hidden packaged app."""
import json, os, time


def run_quit(page, process, fixture):
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('console',lambda m:print('QUIT_QA_CONSOLE',m.text,flush=True) if m.type=='error' else None)
    page.get_by_role('button',name='Chat',exact=True).wait_for()
    frame=page.frame_locator('iframe[src="workbench.html"]')
    frame.locator('[data-unified-chat]').wait_for()
    assert page.evaluate("async()=>{try{await window.__JARVIS_NATIVE__.core.invoke('complete_quit',{id:999999,approved:true,downloads:[]});return false;}catch{return true;}}"), "Stale native authorization must be rejected"
    # Only own hidden QA host is instrumented; the native IPC object is immutable.
    # Observe actual request tokens instead of replacing protected runtime internals.
    page.evaluate("""async () => {
      const bridge=window.__JARVIS_NATIVE__, core=bridge.core, native=core.invoke;
      const qa=window.__quitQA={ids:[], failSave:false,busy:false,unknownBusy:false,saveCount:0,ptys:[],statusQueries:0};
      await bridge.event.listen('jarvis:quit-requested',event=>qa.ids.push(event.payload));
      bridge.core={...core,invoke:async(cmd,args,...rest)=>{
        if(cmd==='pty_has_foreground_process') {qa.statusQueries++;if(qa.unknownBusy)throw 'QA terminal status unavailable';return qa.busy;}
        const result=await native(cmd,args,...rest);
        if(cmd==='pty_open' || cmd==='pty_spawn')qa.ptys.push(result);
        return result;
      }};
      const prototype=bridge.store.LazyStore.prototype, save=prototype.save;
      prototype.save=async function(...args){qa.saveCount++;if(qa.failSave)throw 'QA chat metadata failure';return save.apply(this,args);};
    }""")
    def denied():
        assert process.poll() is None
        assert page.evaluate("""async()=>{try{await window.__JARVIS_NATIVE__.core.invoke('complete_quit',{id:window.__quitQA.ids.at(-1),approved:false,downloads:[]});return false;}catch{return true;}}"""), "Cancelled/failed quit must expire its token"
    # Actual WebView zoom, not CSS zoom or viewport resizing; QA profile only.
    zoom_rows=[]
    for factor in (() if os.environ.get("JARVIS_QA_SKIP_ZOOM")=="1" else (2,4)):
        page.evaluate("factor=>window.__JARVIS_NATIVE__.webview.getCurrentWebview().setZoom(factor)",factor)
        settings_button=page.get_by_role('button',name='Settings',exact=True)
        if not settings_button.is_visible():page.get_by_role('button',name='Open Navigation',exact=True).click()
        settings_button.click()
        dialog=page.locator('.global-settings[role="dialog"]')
        dialog.wait_for()
        metrics=dialog.evaluate('(el)=>({width:el.clientWidth,scroll:el.scrollWidth,viewport:innerWidth})')
        assert metrics['scroll']<=metrics['width']+2,metrics
        assert metrics['width']<=metrics['viewport'],metrics
        zoom_rows.append({'factor':factor,**metrics})
        page.get_by_role('button',name='Close Settings',exact=True).click()
    page.evaluate("window.__JARVIS_NATIVE__.webview.getCurrentWebview().setZoom(1)")
    print(json.dumps({'native_zoom_settings':zoom_rows}))
    # Open an actual editor, mutate its unsaved buffer; disk remains unchanged.
    editor_fixture=fixture/'quit-unsaved.txt';editor_fixture.write_text('QUIT_DISK',encoding='utf-8')
    page.get_by_role('button',name='Files & Tools',exact=True).click()
    page.evaluate("path=>window.__JARVIS_NATIVE__.event.emit('terax:open-file',[path])",str(editor_fixture).replace('\\','/'))
    editor=frame.locator('.cm-content').filter(has_text='QUIT_DISK');editor.wait_for();editor.fill('QUIT_UNSAVED')
    ask=lambda: page.evaluate("window.__JARVIS_NATIVE__.core.invoke('request_app_quit')")
    guard=frame.get_by_role('alertdialog').filter(has_text='Quit Jarvis?')
    # Active-download UI uses qa_download_quit.py; packaged IPC stays immutable.
    assert page.evaluate("window.__JARVIS_NATIVE__.core.invoke('model_download_quit_snapshot')")==[]
    ask();ask()
    try:guard.wait_for(timeout=10000)
    except Exception:
        print('QUIT_ROOT',page.locator('body').inner_text()[-1800:],flush=True)
        print('QUIT_FRAME',frame.locator('body').inner_text()[-1800:],flush=True)
        raise
    assert 'Unsaved files: 1' in guard.inner_text()
    guard.get_by_role('button',name='Cancel',exact=True).click()
    guard.wait_for(state='hidden');denied()
    assert editor_fixture.read_text(encoding='utf-8')=='QUIT_DISK'
    # Restored tabs are lazy: create an actual owned PTY through the UI first.
    frame.locator('button[title="New tab"]').click()
    frame.get_by_role('menuitem',name='Terminal',exact=False).first.click()
    # The packaged CSP forbids wait_for_function's expression eval. Observe via
    # CDP callFunctionOn without relaxing CSP or replacing protected runtime IPC.
    deadline=time.monotonic()+10
    while not page.evaluate('() => window.__quitQA.ptys.length>0'):
        assert time.monotonic()<deadline, 'Owned PTY did not become active'
        page.wait_for_timeout(50)
    print('QUIT_QA_ACTIVE_PTYS',page.evaluate('window.__quitQA.ptys'),flush=True)
    # Busy process and failed status queries both retain protection.
    page.evaluate('window.__quitQA.busy=true');ask();guard.wait_for()
    assert 'process' in guard.inner_text();guard.get_by_role('button',name='Cancel',exact=True).click()
    guard.wait_for(state='hidden');denied()
    page.evaluate('window.__quitQA.busy=false;window.__quitQA.unknownBusy=true');ask()
    # Fail-closed path must deny instead of approving unknown task state.
    shared=page.locator('dialog.jarvis-confirmation[open]');shared.wait_for()
    denied();shared.get_by_role('button',name='Cancel',exact=True).click()
    page.evaluate('window.__quitQA.unknownBusy=false;window.__quitQA.failSave=true');ask();guard.wait_for()
    guard.get_by_role('button',name='Quit Anyway',exact=True).click()
    shared.wait_for();assert 'Could not save chat history' in shared.inner_text()
    shared.get_by_role('button',name='Cancel',exact=True).click()
    shared.wait_for(state='hidden');denied()
    # Motion preference updates root and iframe without remounts and obeys OS reduction.
    page.emulate_media(reduced_motion='reduce')
    deadline=time.monotonic()+10
    while not page.evaluate("() => document.documentElement.dataset.reducedMotion==='true'"):
        assert time.monotonic()<deadline, 'Reduced motion did not update'
        page.wait_for_timeout(50)
    frame.locator('html[data-reduced-motion="true"]').wait_for()
    assert page.evaluate("getComputedStyle(document.querySelector('button')).transitionDuration")=='0s'
    page.emulate_media(reduced_motion='no-preference')
    # Successful explicit approval drains persistence before the native process exits.
    page.evaluate('window.__quitQA.failSave=false');ask();guard.wait_for()
    assert page.evaluate('window.__quitQA.saveCount')>0
    guard.get_by_role('button',name='Quit Anyway',exact=True).click()
    process.wait(timeout=20)
    assert process.returncode==0 and not errors,errors
    assert editor_fixture.read_text(encoding='utf-8')=='QUIT_DISK'
    print(json.dumps({'dirty_cancel':True,'busy_guard':True,'unknown_busy_denied':True,'chat_flush_failure_cancel':True,'approved_native_exit':True,'live_reduced_motion':True,'page_errors':errors}))
