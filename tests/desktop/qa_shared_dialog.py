"""Own hidden Jarvis + actual local Gemma; no Computer Use, microphone or Chrome."""

import json, os, socket, subprocess, time, uuid, sys
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, Error as PlaywrightError


class SmokeComplete(Exception):
    pass


sys.stdout.reconfigure(line_buffering=True)
chat_model = os.environ.get("JARVIS_QA_CHAT_MODEL", "lmstudio-local")
expected_model = "google/gemma-4-e4b" if chat_model == "lmstudio-local" else chat_model
smoke = os.environ.get("JARVIS_QA_SMOKE") == "1"
if not smoke:
    raise SystemExit("Real-provider QA is smoke-only. Set JARVIS_QA_SMOKE=1 for one prompt; use qa_services.py for the full synthetic suite.")
root = Path(__file__).resolve().parents[2]
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
url = f"http://127.0.0.1:{port}"
fixture = root / ".cache/desktop-test/shared-agent"
fixture.mkdir(parents=True, exist_ok=True)
(fixture / "read.txt").write_text("SHARED_READ_" + uuid.uuid4().hex, encoding="utf8")
target = fixture / ("write-" + uuid.uuid4().hex + ".txt")
env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "WEBVIEW2_USER_DATA_FOLDER": str(fixture / uuid.uuid4().hex),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1",
}
exe = (
    root
    / "desktop-bin"
    / json.loads((root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig"))[
        "file"
    ]
)
log = (root / ".cache/shared-native.log").open("w", encoding="utf8")
process = subprocess.Popen(
    [str(exe)], cwd=root, env=env, stdout=log, stderr=log, creationflags=0x08000000
)
prefs_path = Path(os.environ["APPDATA"]) / "com.mrcrous.jarvis.qa/terax-settings.json"
original_prefs = prefs_path.read_bytes() if prefs_path.exists() else None
snapshot = None
try:
    for _ in range(150):
        try:
            if httpx.get(
                url + "/json/version", timeout=0.2, trust_env=False
            ).is_success:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(url)
        for _ in range(100):
            page = next(
                (
                    p
                    for c in browser.contexts
                    for p in c.pages
                    if "tauri.localhost" in p.url
                ),
                None,
            )
            if page:
                break
            time.sleep(0.1)
        assert page
        # Current local build only; native APIs remain the packaged host.
        from urllib.parse import urlsplit, unquote

        def asset(route):
            base = (root / "apps/desktop/dist").resolve()
            path = (
                base / unquote(urlsplit(route.request.url).path).lstrip("/")
            ).resolve()
            if path == base:
                path = base / "index.html"
            if path.is_relative_to(base) and path.is_file():
                route.fulfill(path=str(path))
            else:
                route.continue_()

        if "--bundled-assets" not in sys.argv:
            page.route("http://tauri.localhost/**", asset)
        page.reload()
        page.get_by_role("button",name="Chat",exact=True).wait_for()
        keys = [
            "customEndpoints",
            "lmstudioBaseURL",
            "lmstudioModelId",
            "lmstudioReasoning",
            "defaultModelId",
        ]
        snapshot = page.evaluate(
            """async keys=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return Object.fromEntries(await Promise.all(keys.map(async k=>[k,(await s.get(k))??null])));} """,
            keys,
        )
        page.evaluate(
            """async options=>{const base=options.base;const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});for(const [k,v] of Object.entries({lmstudioBaseURL:base,lmstudioModelId:'google/gemma-4-e4b',lmstudioReasoning:'off',defaultModelId:options.model}))await s.set(k,v);await s.save();}""",
            {
                "base": os.environ.get(
                    "JARVIS_QA_MODEL_BASE", "http://127.0.0.1:1234/v1"
                ),
                "model": chat_model,
            },
        )
        if chat_model == "compat-jarvis-llama":
            model_path = os.environ["JARVIS_QA_LLAMA_MODEL"]
            page.evaluate("""async model => {
              await window.__JARVIS_NATIVE__.core.invoke('backend', {method:'llama.start',params:{model,projector:'',mode:'vulkan'}});
              const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});
              await s.set('customEndpoints',[{id:'jarvis-llama',name:'llama.cpp',baseURL:'http://127.0.0.1:18081/v1',modelId:'jarvis-local',contextLimit:8192}]);await s.save();
            }""", model_path)
        page.reload()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        frame = page.frame_locator('iframe[src="workbench.html"]')
        page.get_by_role("button", name="Chat", exact=True).click()
        frame.locator('[data-unified-chat]').wait_for()
        frame.locator('[data-unified-chat] header button').first.click()
        search = frame.get_by_placeholder('Search models, providers, capabilities…',exact=True)
        search.fill('llama.cpp' if chat_model == 'compat-jarvis-llama' else 'LM Studio')
        frame.get_by_role('menuitem').filter(has_text='llama.cpp' if chat_model == 'compat-jarvis-llama' else 'LM Studio').first.click()
        search.press('Escape')
        picker = frame.locator('button[title="Switch session"]:visible')
        if picker.count():
            picker.click()
            frame.get_by_role("menuitem", name="New session", exact=True).click()
        for _ in range(150):
            state = page.evaluate(
                "()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})"
            )
            if state["active_model"] == expected_model:
                break
            page.wait_for_timeout(100)
        assert state["active_model"] == expected_model, state["active_model"]
        capabilities = page.evaluate(
            "()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'capabilities',params:{}})"
        )
        names = {item["id"] for item in capabilities}
        assert {
            "read_file",
            "data_query",
            "translation.run",
            "embedding.run",
            "suggestions.save",
        } <= names, names
        assert "api_add_key" not in names
        print(json.dumps({"shared_gui_catalogue": len(names)}))
        baseline = set()

        def request(text):
            baseline.clear()
            before = page.evaluate(
                "()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})"
            )
            baseline.update(m["id"] for m in before["messages"])
            page.evaluate(
                """text=>{window.__qaResult=null;window.__qaError=null;window.__JARVIS_NATIVE__.core.invoke('backend',{method:'chat',params:{text}}).then(r=>window.__qaResult=r).catch(e=>window.__qaError=String(e));}""",
                text,
            )

        def result(timeout=150):
            deadline = time.monotonic() + timeout
            settled = None
            settled_at = 0
            while time.monotonic() < deadline:
                value = page.evaluate(
                    "()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})"
                )
                fresh = [m for m in value["messages"] if m["id"] not in baseline]
                failure = next((m for m in fresh if m["role"] == "error"), None)
                if failure:
                    raise AssertionError(failure["text"])
                answer = next(
                    (m for m in reversed(fresh) if m["role"] == "assistant"), None
                )
                activity = page.evaluate(
                    "()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'services.read',params:{name:'runtime.status',args:{}}})"
                )
                if answer and not activity["busy"]:
                    if settled != answer["text"]:
                        settled = answer["text"]
                        settled_at = time.monotonic()
                    elif time.monotonic() - settled_at >= 0.5:
                        return {"reply": answer["text"]}
                else:
                    settled = None
                page.wait_for_timeout(200)
            raise AssertionError(
                "Shared dialog timed out: " + frame.locator("body").inner_text()[-3000:]
            )

        if os.environ.get('JARVIS_QA_UTILITIES') == '1':
            yaml = fixture / ('numbers-' + uuid.uuid4().hex + '.yaml')
            yaml.write_text('value: 6\n',encoding='utf8')
            output = fixture / ('gemma-utility-' + uuid.uuid4().hex + '.txt')
            approve = frame.get_by_role('button',name='Approve',exact=True)
            request(f"Найди в общем каталоге доступные утилиты. Используй установленный yq, чтобы прочитать YAML {yaml} и вычислить value * 7. Выполни именно yq через инструмент запуска CLI, не shell и не Python. Отчитайся только по фактическому результату. Дождись подтверждения запуска.")
            approve.first.wait_for(timeout=180000)
            pending = approve.first.locator('xpath=../..')
            assert 'utility_run' in pending.inner_text(), pending.inner_text()
            cli = json.loads(pending.locator('pre').inner_text())
            assert cli['tool'] == 'yq' and not any(a in ('-i','--inplace') for a in cli['args']), cli
            assert any(str(yaml).replace('\\','/') == a.replace('\\','/') for a in cli['args']), cli
            approve.first.click()
            reply = result(timeout=180)['reply']
            assert '42' in reply, reply
            transcript = frame.locator('[data-unified-chat]').inner_text()
            assert 'utility_catalog' in transcript and 'utility_run' in transcript, transcript[-5000:]
            request(f"Найди инструмент выполнения Python в общем каталоге. Через него создай только файл {output} с текстом GEMMA_UTILITIES_42. Не используй shell или write_file. Дождись подтверждения.")
            approve.first.wait_for(timeout=180000)
            assert not output.exists()
            pending = approve.first.locator('xpath=../..')
            assert 'run_code' in pending.inner_text(), pending.inner_text()
            code = json.loads(pending.locator('pre').inner_text())
            assert code['language'] == 'python' and 'GEMMA_UTILITIES_42' in code['code'], code
            approve.first.click()
            result(timeout=180)
            assert output.read_text(encoding='utf8').strip() == 'GEMMA_UTILITIES_42'
            transcript = frame.locator('[data-unified-chat]').inner_text()
            assert 'Input: 0' not in frame.get_by_label('Session tokens',exact=True).inner_text()
            (root/'.cache/real-utilities-transcript.txt').write_text(transcript,encoding='utf8')
            page.screenshot(path=str(root/'.cache/real-utilities-chat.png'))
            print(json.dumps({'real_local_model':expected_model,'catalogue_discovery':True,'yq_yaml_result':42,'python_file_after_approval':True}))
        else:
            request("Ответь ровно JARVIS_SHARED_READY. Не используй инструменты.")
            r = result()
            assert "JARVIS_SHARED_READY" in r["reply"], r
            print(
                json.dumps(
                    {
                        "main_chat_model_protocol": os.environ.get(
                            "JARVIS_QA_MODEL_BASE",
                            "real LM Studio"
                            if chat_model == "lmstudio-local"
                            else "llama.cpp" if chat_model == "compat-jarvis-llama" else "Jarvis Gemini broker",
                        ),
                        "reply": r["reply"],
                    }
                )
            )
            if chat_model == "compat-jarvis-llama":
                request(f"Use read_file to read {fixture / 'read.txt'} and reply with the exact file content.")
                assert (fixture / 'read.txt').read_text() in result()["reply"]
                request(f"Use write_file to create {target} with exactly LLAMA_APPROVED_FILE. Do not modify anything else. Wait for approval.")
                approve = frame.get_by_role("button", name="Approve", exact=True)
                approve.wait_for(timeout=120000)
                assert str(target).lower() in frame.locator('[data-unified-chat]').inner_text().lower()
                assert not target.exists()
                approve.click()
                result()
                assert target.read_text(encoding="utf8").strip() == "LLAMA_APPROVED_FILE"
                print(json.dumps({"real_shared_read_write_approval": True}))
                command = str(root / '.venv/Scripts/python.exe').replace('\\', '/') + ' -c "print(6*7)"'
                request(f"Use bash_run to execute exactly this command: {command}. Wait for approval and report its output.")
                approve.wait_for(timeout=120000)
                assert 'print(6*7)' in frame.locator('[data-unified-chat]').inner_text()
                approve.click()
                assert '42' in result()["reply"]
                print(json.dumps({"real_shared_python_approval": True}))
                import base64, io
                from docx import Document
                document = Document(); document.add_paragraph('DOCUMENT_LIBRARY_REAL_READY')
                stream = io.BytesIO(); document.save(stream)
                copied = page.evaluate("""data=>window.__JARVIS_NATIVE__.core.invoke('import_attachment',{name:'real-document.docx',data})""", base64.b64encode(stream.getvalue()).decode())
                registered = page.evaluate("""path=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.import',params:{path,name:'real-document.docx'}})""",copied['path'])
                request('Use jarvis_action documents.read with identifier '+registered['id']+' and report the exact text from the uploaded document. Do not read the binary DOCX with read_file.')
                assert 'DOCUMENT_LIBRARY_REAL_READY' in result()["reply"]
                print(json.dumps({"real_shared_uploaded_docx_read":True}))
        print(
            json.dumps(
                {
                    "real_provider_tools_schema_accepted": chat_model,
                    "page_errors": errors,
                }
            )
        )
        assert not errors, errors
        page.evaluate("window.__JARVIS_NATIVE__.core.invoke('pty_close_all')")
        try:
            page.evaluate(
                "window.__JARVIS_NATIVE__.core.invoke('plugin:window|close')"
            )
        except PlaywrightError:
            pass
        browser.close()
        process.wait(timeout=20)
        raise SmokeComplete()
except SmokeComplete:
    pass
finally:
    if snapshot is not None:
        # Preserve QA preferences even on a failed test.
        path = (
            root / ".cache/desktop-test"
        )  # store restoration happens while the page is alive below if possible
        try:
            page.evaluate(
                """async values=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});for(const [k,v]of Object.entries(values)){if(v===null)await s.delete(k);else await s.set(k,v);}await s.save();}""",
                snapshot,
            )
        except Exception:
            pass
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    if original_prefs is not None:
        prefs_path.write_bytes(original_prefs)
    log.close()
