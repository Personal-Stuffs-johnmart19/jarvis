"""Actual agent/transport/toolset at 4K JIT or 8K reasoning; synthetic provider only."""
import json
import os
import httpx
from playwright.sync_api import expect
from qa_locale import select_language


def run_fresh_context(page, fixture, reasoning_context=False):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.evaluate("""async base=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});for(const[k,v]of Object.entries({lmstudioBaseURL:base,lmstudioModelId:'google/gemma-4-e4b',lmstudioReasoning:'off',defaultModelId:'lmstudio-local'}))await s.set(k,v);await s.save();}""", os.environ['JARVIS_QA_MODEL_BASE'])
    page.reload()
    select_language(page, 'en')
    page.get_by_role('button', name='Chat', exact=True).click()
    frame = page.frame_locator('iframe[src="workbench.html"]')
    frame.locator('[data-unified-chat]').wait_for()
    picker = frame.locator('button[title="Switch session"]:visible')
    if picker.count():
        picker.click()
        frame.get_by_role('menuitem', name='New session', exact=True).click()
    composer = frame.locator('[data-shared-composer] textarea')
    # Mirrors explicitly opening this owned workspace, not an agent authorization bypass.
    assert fixture.resolve().parent.name == 'desktop-test'
    page.evaluate("path=>window.__JARVIS_NATIVE__.core.invoke('workspace_authorize',{path,workspace:{kind:'local'}})", str(fixture))
    composer.fill('123')
    frame.get_by_role('button', name='Send', exact=True).click()
    frame.get_by_text('JARVIS_SHARED_READY', exact=True).first.wait_for(timeout=15000)
    frame.get_by_role('button', name='Send', exact=True).wait_for()
    composer.fill('UTILITY_QA:' + json.dumps({'name':'run_code','args':{'language':'python','code':'print(6*7)','cwd':str(fixture)}}))
    frame.get_by_role('button', name='Send', exact=True).click()
    approve = frame.get_by_role('button', name='Approve', exact=True).first
    approve.wait_for(timeout=15000)
    approve.click()
    frame.get_by_text('UTILITY_QA_OK', exact=True).first.wait_for(timeout=15000)
    frame.get_by_role('button', name='Send', exact=True).wait_for()
    activity = frame.get_by_role('button', name='Activity', exact=True).last
    expect(activity).to_have_attribute('aria-expanded', 'false')
    activity.click()
    expect(activity).to_have_attribute('aria-expanded', 'true')
    trigger = frame.locator('[data-slot="collapsible-trigger"]').filter(has_text='jarvis_action').first
    expect(trigger).to_have_attribute('aria-expanded', 'false')
    activity.click()
    expect(activity).to_have_attribute('aria-expanded', 'false')
    endpoint = os.environ['JARVIS_QA_MODEL_BASE'].removesuffix('/v1') + '/qa/fresh-context'
    stats = httpx.get(endpoint, timeout=3, trust_env=False).json()
    assert stats['requests'] == (5 if reasoning_context else 3), stats
    assert not errors, errors
    print(json.dumps({'actual_app_4k_unloaded_and_loaded':not reasoning_context,'actual_app_8k_reasoning_tool_continuation':reasoning_context,'plain_123':True,'approved_python_42':True,'stats':stats,'page_errors':errors}))
