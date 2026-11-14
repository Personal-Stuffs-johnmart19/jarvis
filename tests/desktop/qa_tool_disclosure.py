"""Actual shared tool cards, synthetic props, isolated headless browser; no model/service calls."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / '.cache/verification/tool-disclosure'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
url = f'http://127.0.0.1:{port}/qa/tool-disclosure.html'
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
            for name in ['catalog', 'action', 'list', 'reasoning']:
                expect(page.get_by_role('region', name=name).get_by_role('button')).to_have_attribute('aria-expanded', 'false')
            expect(page.get_by_text('catalog-item-99', exact=False)).not_to_be_visible()
            catalog = page.get_by_role('region', name='catalog').get_by_role('button')
            catalog.focus()
            page.keyboard.press('Enter')
            expect(catalog).to_have_attribute('aria-expanded', 'true')
            expect(page.get_by_text('catalog-item-99', exact=False)).to_be_visible()
            page.keyboard.press('Enter')
            expect(catalog).to_have_attribute('aria-expanded', 'false')
            page.evaluate('window.__toolDisclosureQA.update({failed:true,streaming:true})')
            expect(page.get_by_role('alert')).to_contain_text('Synthetic tool failure')
            expect(catalog).to_have_attribute('aria-expanded', 'false')
            reasoning = page.get_by_role('region', name='reasoning').get_by_role('button')
            reasoning.click()
            page.evaluate('window.__toolDisclosureQA.update({failed:true,streaming:false})')
            page.wait_for_timeout(1200)
            expect(reasoning).to_have_attribute('aria-expanded', 'true')
            for locale in ['en', 'ru']:
                page.evaluate('locale => window.__toolDisclosureQA.locale(locale)', locale)
                action = page.get_by_role('region', name='action')
                reply = 'Set up the document service first.' if locale == 'en' else 'Сначала настройте службу документов.'
                for output in [{'error': 'Unknown command'}, {'error': {'message': '<b>Unsafe command</b>'}}, {'ok': False, 'reply': reply}]:
                    page.evaluate('output => window.__toolDisclosureQA.update({failed:true,streaming:false,actionOutput:output})', output)
                    expected = output.get('reply') or (output['error'].get('message') if isinstance(output['error'], dict) else output['error'])
                    expect(action.get_by_role('alert')).to_contain_text(expected)
                    expect(action.get_by_role('button')).to_have_attribute('aria-expanded', 'false')
                    expect(action.locator('[aria-label="error"]')).to_be_visible()
                    expect(action.locator('b')).to_have_count(0)
                page.evaluate('window.__toolDisclosureQA.update({failed:true,streaming:false,actionOutput:{error:"x".repeat(2000),raw:"FULL_ERROR_PAYLOAD"}})')
                assert len(action.get_by_role('alert').inner_text()) < 1100
                action.get_by_role('button').click()
                expect(action.get_by_text('FULL_ERROR_PAYLOAD', exact=False)).to_be_visible()
                action.get_by_role('button').click()
                approval = page.get_by_role('region', name='approval')
                expect(approval.get_by_role('button')).to_have_count(2)
                expect(approval.get_by_role('button').nth(0)).to_be_visible()
                expect(approval.get_by_role('button').nth(1)).to_be_visible()
                expect(page.get_by_text('User message remains visible')).to_be_visible()
            page.get_by_role('region', name='approval').get_by_role('button').nth(1).click()
            assert page.evaluate('window.__toolDisclosureQA.responses') == [True]
            gateway = page.get_by_role('region', name='gateway')
            for index, locale in enumerate(['en', 'ru']):
                page.evaluate('locale => window.__toolDisclosureQA.locale(locale)', locale)
                part = page.evaluate('window.__toolDisclosureQA.suggest()')
                assert part['type'] == 'tool-jarvis_action'
                assert part['input']['name'] == 'suggest_command'
                assert part['output']['command'] == 'echo GATEWAY_INSERT'
                assert page.evaluate('window.__toolDisclosureQA.insertions.length') == index
                assert page.evaluate('window.__toolDisclosureQA.automaticInsertions') == []
                trigger = gateway.get_by_role('button').first
                expect(trigger).to_have_attribute('aria-expanded', 'false')
                trigger.click()
                insert_label = 'Insert into active terminal' if locale == 'en' else 'Вставить в активный терминал'
                insert = gateway.get_by_role('button', name=insert_label, exact=True)
                expect(insert).to_be_visible()
                assert page.evaluate('window.__toolDisclosureQA.insertions.length') == index
                insert.click()
                expect(insert).to_be_disabled()
                assert page.evaluate('window.__toolDisclosureQA.insertions') == ['echo GATEWAY_INSERT'] * (index + 1)
                # A late error must not retain a usable command card from its old output.
                page.evaluate('part => window.__toolDisclosureQA.showGateway({...part,state:"output-error",errorText:"Owned suggestion failed"})', part)
                expect(gateway.get_by_role('alert')).to_contain_text('Owned suggestion failed')
                expect(gateway.get_by_role('button', name=insert_label, exact=True)).to_have_count(0)
                assert page.evaluate('window.__toolDisclosureQA.gatewayPart.toolCallId') == part['toolCallId']
            page.evaluate('window.__toolDisclosureQA.locale("en")')
            for name, args, output, visible in [
                ('read_file', {'path':'owned.txt'}, {'path':'owned.txt','content':'one\ntwo','size':7,'total_lines':2}, '2 lines'),
                ('list_directory', {'path':'owned'}, {'entries':[{'name':'fixture.txt','kind':'file'}]}, 'fixture.txt'),
                ('grep', {'pattern':'MARKER'}, {'hits':[{'path':'owned.txt','line':2,'text':'MARKER'}]}, 'owned.txt:2'),
                ('glob', {'pattern':'*.txt'}, {'matches':['owned.txt']}, 'owned.txt'),
                ('bash_run', {'command':'echo COMMAND_OUTPUT'}, {'stdout':'COMMAND_OUTPUT','stderr':'COMMAND_ERROR','exit_code':0,'cwd_after':'owned'}, 'COMMAND_OUTPUT'),
            ]:
                page.evaluate('part => window.__toolDisclosureQA.showGateway(part)', {'type':'tool-jarvis_action','toolCallId':'shape-'+name,'state':'output-available','input':{'name':name,'args':args},'output':output})
                gateway.get_by_role('button').first.click()
                expect(gateway).to_contain_text(visible)
                expect(gateway.locator('pre')).to_have_count(2 if name == 'bash_run' else 1 if name == 'glob' else 0)
                if name == 'bash_run':
                    gateway.get_by_role('button', name='stderr', exact=False).click()
                    expect(gateway).to_contain_text('COMMAND_ERROR')
                    expect(gateway).to_contain_text('exit 0')
            # Errors with a command payload remain visible data, never an Insert affordance.
            page.evaluate('window.__toolDisclosureQA.showGateway({type:"tool-jarvis_action",toolCallId:"failed-command",state:"output-available",input:{name:"suggest_command",args:{command:"echo blocked"}},output:{command:"echo blocked",error:"Rejected suggestion"}})')
            gateway.get_by_role('button').first.click()
            expect(gateway.get_by_role('alert')).to_contain_text('Rejected suggestion')
            expect(gateway.get_by_role('button', name='Insert into active terminal', exact=True)).to_have_count(0)
            for state, input in [
                ('output-denied', {'name':'suggest_command','args':{'command':'echo blocked'}}),
                ('output-available', {'name':'suggest_command','args':[]}),
                ('output-available', {'name':'constructor','args':{}}),
            ]:
                page.evaluate('part => window.__toolDisclosureQA.showGateway(part)', {'type':'tool-jarvis_action','toolCallId':'guard-'+state+input['name'],'state':state,'input':input,'output':{'command':'echo blocked'}})
                gateway.get_by_role('button').first.click()
                expect(gateway.get_by_role('button', name='Insert into active terminal', exact=True)).to_have_count(0)
            assert page.evaluate('window.__toolDisclosureQA.automaticInsertions') == []
            for index, command in enumerate(['echo one\necho two', 'echo one\recho two', 'echo \x1b[31mred']):
                page.evaluate('part => window.__toolDisclosureQA.showGateway(part)', {'type':'tool-jarvis_action','toolCallId':'control-byte-'+str(index),'state':'output-available','input':{'name':'suggest_command','args':{'command':command}},'output':{'command':command}})
                gateway.get_by_role('button').first.click()
                expect(gateway.get_by_role('button', name='Insert into active terminal', exact=True)).to_have_count(0)
            assert page.evaluate('window.__toolDisclosureQA.insertions') == ['echo GATEWAY_INSERT'] * 2
            page.screenshot(path=str(EVIDENCE / 'tool-disclosure.png'))
            assert not errors, errors
            browser.close()
        print(json.dumps({'tool_disclosure_ui': 'passed', 'generic_tools': ['jarvis_catalog', 'jarvis_action'], 'gateway_outputs': ['suggest_command','read_file','list_directory','grep','glob','bash_run'], 'explicit_insert_only':True, 'control_bytes_refused':True, 'locales': ['en', 'ru'], 'personal_browser': False}))
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
