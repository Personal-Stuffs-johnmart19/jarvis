"""Opt-in real local llama.cpp check; never contacts an inference cloud."""
import os
os.environ["JARVIS_DESKTOP_TEST"] = "1"
import json
from pathlib import Path
import subprocess
import sys
import requests
from local_mode.local_chat import runner
from local_mode.local_embeddings import embed, _runner

def main():
    model = sys.argv[1]
    projector = sys.argv[2] if len(sys.argv) > 2 else ""
    session = requests.Session(); session.trust_env = False
    root = Path('.cache/llama-smoke').resolve(); root.mkdir(parents=True, exist_ok=True)
    try:
        status = runner.start(model, projector)
        print(json.dumps(status), flush=True)
        tools = [{"type":"function","function":{"name":"run_python","description":"Execute Python code in a test workspace","parameters":{"type":"object","properties":{"code":{"type":"string"}},"required":["code"],"additionalProperties":False}}}]
        messages = [{"role":"user","content":"Call run_python with exactly this code: print(6*7). Do not answer without calling the tool."}]
        response=session.post(status['baseURL']+'/chat/completions',json={"model":"jarvis-local","messages":messages,"tools":tools,"tool_choice":"required","max_tokens":300,"temperature":0},timeout=120)
        response.raise_for_status()
        message=response.json()['choices'][0]['message']
        call=message['tool_calls'][0]
        assert call['function']['name']=='run_python'
        code=json.loads(call['function']['arguments'])['code']
        # Model output is untrusted: only the exact fixture is approved here.
        if code.strip() != 'print(6*7)':
            raise ValueError('Model did not return the approved Python fixture')
        result=subprocess.run([sys.executable,'-c','print(6*7)'],cwd=root,capture_output=True,text=True,timeout=10,check=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        assert result.stdout.strip()=='42'
        messages += [message,{"role":"tool","tool_call_id":call['id'],"content":result.stdout}]
        response=session.post(status['baseURL']+'/chat/completions',json={"model":"jarvis-local","messages":messages,"tools":tools,"max_tokens":300,"temperature":0},timeout=120)
        response.raise_for_status()
        assert '42' in response.json()['choices'][0]['message']['content']
        print('PASS real function call, approved Python fixture, tool result roundtrip',flush=True)
        from playwright.sync_api import sync_playwright
        for name, prompt, properties, expected in [
            ("write_file", "Call write_file with path note.txt and content JARVIS_FILE_READY.", {"path":{"type":"string"},"content":{"type":"string"}}, {"path":"note.txt","content":"JARVIS_FILE_READY"}),
            ("browser_read", "Call browser_read with selector #result.", {"selector":{"type":"string"}}, {"selector":"#result"}),
        ]:
            declaration={"type":"function","function":{"name":name,"parameters":{"type":"object","properties":properties,"required":list(properties),"additionalProperties":False}}}
            response=session.post(status['baseURL']+'/chat/completions',json={"model":"jarvis-local","messages":[{"role":"user","content":prompt}],"tools":[declaration],"tool_choice":"required","max_tokens":300,"temperature":0},timeout=120)
            response.raise_for_status()
            call=response.json()['choices'][0]['message']['tool_calls'][0]
            assert call['function']['name']==name
            args=json.loads(call['function']['arguments'])
            assert args==expected, args
            if name=="write_file":
                target=root/args['path']; target.write_text(args['content'],encoding="utf8")
                assert target.read_text()=="JARVIS_FILE_READY"
            else:
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(headless=True)
                    page=browser.new_page(); page.set_content('<p id="result">JARVIS_BROWSER_READY</p>')
                    assert page.locator(args['selector']).inner_text()=="JARVIS_BROWSER_READY"
                    browser.close()
            print('PASS real tool arguments and isolated execution',name,flush=True)
        runner.stop()
        cpu = runner.start(model, "", "cpu")
        response=session.post(cpu['baseURL']+'/chat/completions',json={"model":"jarvis-local","messages":[{"role":"user","content":"Reply OK"}],"max_tokens":32},timeout=180)
        response.raise_for_status()
        assert cpu['device']=="none" and response.json()['choices']
        print('PASS chat CPU',flush=True)
        runner.stop()
        for backend in ('local','cpu'):
            result=embed('Jarvis local semantic search',backend=backend)
            assert len(result['vector'])==768
            print('PASS embedding',backend,result['device'],flush=True)
    finally:
        runner.stop(); _runner.close(); session.close()

if __name__=='__main__': main()
