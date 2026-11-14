"""Owned hidden Jarvis QA; real selected helpers require explicit provider opt-in."""
from qa_locale import select_language
from qa_suggestions import preserve_qa_suggestions

import json, os, re, socket, subprocess, time, uuid, sys, hashlib, struct, tempfile, shutil
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError
from qa_selected_helper import selected_helper_options


def wait_native_preferences(page, expected):
    # Playwright wait_for_function treats an async predicate's Promise as truthy.
    # Evaluate awaits this bounded timer poll, including in hidden WebViews.
    return page.evaluate("""async expected => {
        const store = await window.__JARVIS_NATIVE__.store.load('terax-settings.json', {defaults:{}});
        const keys = Object.keys(expected);
        const deadline = performance.now() + 10000;
        let actual;
        do {
            actual = Object.fromEntries(await Promise.all(keys.map(async key => [key, await store.get(key)])));
            if (keys.every(key => actual[key] === expected[key])) return actual;
            await new Promise(resolve => setTimeout(resolve, 25));
        } while (performance.now() < deadline);
        throw new Error('Native preference state did not settle: ' + JSON.stringify({expected, actual}));
    }""", expected)


class SmokeComplete(Exception):
    pass


def make_managed_chat_fixture(models_root):
    """Catalogue-only GGUF: format-valid metadata, intentionally no inference tensors."""
    repo = 'qa/managed-chat-native'
    revision = uuid.uuid4().hex + uuid.uuid4().hex[:8]
    directory = models_root / 'llm' / 'qa__managed-chat-native' / revision
    directory.mkdir(parents=True)
    key = b'general.architecture'
    architecture = b'llama'
    content = (b'GGUF' + struct.pack('<IQQ',3,0,1) + struct.pack('<Q',len(key)) + key
        + struct.pack('<IQ',8,len(architecture)) + architecture)
    (directory/'chat-qa.gguf').write_bytes(content)
    manifest = {'owner':'jarvis-model-downloads/1','complete':True,'repo_id':repo,'revision':revision,
        'kind':'llm','bundle_id':'llm-gguf','license':'test-fixture',
        'files':[{'path':'chat-qa.gguf','size':len(content),'sha256':hashlib.sha256(content).hexdigest()}]}
    (directory/'manifest.json').write_text(json.dumps(manifest),encoding='utf8')
    return {'directory':directory,'repo':repo,'revision':revision,
        'model_id':f'jarvis-managed:llm:{repo}@{revision}'}


sys.stdout.reconfigure(line_buffering=True)
chat_model = os.environ.get("JARVIS_QA_CHAT_MODEL", "lmstudio-local")
expected_model = "google/gemma-4-e4b" if chat_model == "lmstudio-local" else chat_model
smoke = os.environ.get("JARVIS_QA_SMOKE") == "1"
helper_options = selected_helper_options(sys.argv[1:])
selected_helper = helper_options.selected_helper_only
queued_task = "--queued-task-only" in sys.argv
pc_status = "--pc-status-only" in sys.argv
queued_plan = "--queued-plan-only" in sys.argv
if queued_plan and (not selected_helper or queued_task):
    raise SystemExit("--queued-plan-only requires --selected-helper-only and cannot combine with --queued-task-only")
if queued_task and (selected_helper or helper_options.selected_helper_provider or helper_options.live):
    raise SystemExit("--queued-task-only is synthetic and cannot be combined with live helper options")
qa_profile = uuid.uuid4().hex if selected_helper or queued_task or pc_status else None
root = Path(__file__).resolve().parents[2]
if not os.environ.get("JARVIS_QA_MODEL_BASE"):
    raise SystemExit("Use qa_services.py: this full QA suite requires a synthetic local provider.")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
url = f"http://127.0.0.1:{port}"
fixture = root / ".cache/desktop-test" / (("pc-status-" if pc_status else "selected-helper-" if selected_helper and not queued_plan else "queued-task-") + qa_profile[:8] if qa_profile else "shared-agent")
fixture.mkdir(parents=True, exist_ok=True)
(fixture / "read.txt").write_text("SHARED_READ_" + uuid.uuid4().hex, encoding="utf8")
target = fixture / ("write-" + uuid.uuid4().hex + ".txt")
env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "JARVIS_QA_ALLOW_CLOUD": "0",
    "WEBVIEW2_USER_DATA_FOLDER": str(fixture / uuid.uuid4().hex),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1",
}
if qa_profile:
    env["JARVIS_QA_PROFILE"] = qa_profile
    env["JARVIS_QA_ALLOW_CLOUD"] = "1" if helper_options.selected_helper_provider == "google" else "0"
    env["JARVIS_QA_MAX_CLOUD_CALLS"] = "1"
    env["JARVIS_AUTO_FALLBACK"] = "0"
portable_cwd = None
launch_cwd = root
if os.environ.get('JARVIS_QA_PORTABLE_ROOT') == '1':
    env.pop('JARVIS_ROOT', None)
    portable_cwd = Path(tempfile.mkdtemp(prefix='jarvis-portable-cwd-')).resolve()
    assert not portable_cwd.is_relative_to(root.resolve())
    launch_cwd = portable_cwd
    print(json.dumps({'portable_resource_root_mode':True,'explicit_root_removed':'JARVIS_ROOT' not in env,'unrelated_launch_directory':True}))
exe = (
    root
    / "desktop-bin"
    / json.loads((root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig"))[
        "file"
    ]
)
if os.environ.get("JARVIS_QA_EXECUTABLE"):
    assert selected_helper or queued_task or pc_status, "An explicit QA executable requires a focused helper/task/status fixture"
    requested = Path(os.environ["JARVIS_QA_EXECUTABLE"])
    allowed = root / "apps/desktop/src-tauri/target/debug/jarvis-desktop.exe"
    assert requested.is_absolute() and requested.resolve(strict=True) == allowed.resolve(strict=True)
    assert requested.resolve().is_relative_to(root.resolve()) and requested.is_file()
    # Launch the fixed repository-owned path, never the environment-derived value.
    exe = allowed.resolve(strict=True)
log = (fixture / "host.log" if pc_status else root / ".cache/shared-native.log").open("w", encoding="utf8")
prefs_path = Path(os.environ["APPDATA"]) / ("com.mrcrous.jarvis.qa" + ("." + qa_profile if qa_profile else "")) / "terax-settings.json"
original_prefs = prefs_path.read_bytes() if prefs_path.exists() else None
settings = json.loads(original_prefs or b"{}")
settings.update({"lmstudioBaseURL":os.environ["JARVIS_QA_MODEL_BASE"],"lmstudioModelId":"google/gemma-4-e4b","defaultModelId":"gemini-3.5-flash-lite","ollamaBaseURL":os.environ["JARVIS_QA_MODEL_BASE"],"ollamaModelId":"qa-loaded","whispercppBaseURL":os.environ["JARVIS_QA_MODEL_BASE"].removesuffix("/v1")+"/stt-ready"})
if selected_helper or queued_task or pc_status:
    settings["defaultModelId"] = "lmstudio-local"
prefs_path.parent.mkdir(parents=True,exist_ok=True)
prefs_path.write_text(json.dumps(settings),encoding="utf8")
managed_fixture = make_managed_chat_fixture(root/'.cache/desktop-test/models')
process = subprocess.Popen(
    [str(exe)], cwd=launch_cwd, env=env, stdout=log, stderr=log, creationflags=0x08000000
)
snapshot = None
mcp_qa = None
# Isolated format-valid TTS bundle; activation must not load these synthetic weights.
tts_selection_path=root/'.cache/desktop-test/tts-model.json'
original_tts_selection=tts_selection_path.read_bytes() if tts_selection_path.exists() else None
tts_controls_path=root/'.cache/desktop-test/tts-controls.json'
original_tts_controls=tts_controls_path.read_bytes() if tts_controls_path.exists() else None
qa_voice_path=root/'.cache/desktop-test/voice-preferences.json'
original_qa_voice=qa_voice_path.read_bytes() if qa_voice_path.exists() else None
tts_revision=uuid.uuid4().hex+'0'*8
tts_dir=root/'.cache/desktop-test/models/tts/qa__supertonic'/tts_revision
tts_dir.mkdir(parents=True)
tts_fields={'general.architecture':'audiocpp','audiocpp.model_spec.version':1,'audiocpp.model_spec.family':'supertonic'}
tts_bytes=b'GGUF'+struct.pack('<IQQ',3,0,len(tts_fields))
for key,value in tts_fields.items():
    encoded=key.encode();tts_bytes+=struct.pack('<Q',len(encoded))+encoded
    if isinstance(value,str):
        encoded=value.encode();tts_bytes+=struct.pack('<IQ',8,len(encoded))+encoded
    else:tts_bytes+=struct.pack('<II',4,value)
(tts_dir/'supertonic-qa.gguf').write_bytes(tts_bytes)
(tts_dir/'manifest.json').write_text(json.dumps({'owner':'jarvis-model-downloads/1','complete':True,'repo_id':'qa/supertonic','revision':tts_revision,'kind':'tts','bundle_id':'tts-supertonic-gguf','license':'test-fixture','files':[{'path':'supertonic-qa.gguf','size':len(tts_bytes),'sha256':hashlib.sha256(tts_bytes).hexdigest()}]}),encoding='utf8')
piper_selection_path=root/'.cache/desktop-test/piper-model.json'
original_piper_selection=piper_selection_path.read_bytes() if piper_selection_path.exists() else None
piper_revision=uuid.uuid4().hex+'0'*8
piper_dir=root/'.cache/desktop-test/models/tts/qa__piper'/piper_revision
piper_dir.mkdir(parents=True)
piper_config={'num_symbols':3,'phoneme_id_map':{'_':[0],'^':[1],'$':[2]},'num_speakers':2,'phoneme_type':'espeak','audio':{'sample_rate':22050},'espeak':{'voice':'en-us'},'speaker_id_map':{'Alice':0,'Bob':1}}
piper_files=[]
for filename,content in [('voice.onnx',b'fixture ONNX; never loaded'),('voice.onnx.json',json.dumps(piper_config).encode())]:
    (piper_dir/filename).write_bytes(content)
    piper_files.append({'path':filename,'size':len(content),'sha256':hashlib.sha256(content).hexdigest()})
(piper_dir/'manifest.json').write_text(json.dumps({'owner':'jarvis-model-downloads/1','complete':True,'repo_id':'qa/piper','revision':piper_revision,'kind':'tts','bundle_id':'tts-piper-onnx','license':'test-fixture','files':piper_files}),encoding='utf8')
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
        pages = [p for context in browser.contexts for p in context.pages]
        page = next((p for p in pages if "tauri.localhost" in p.url), None)
        if page is None:
            page = next((p for p in pages if p.url == "about:blank"), None)
        if page is not None:
            # A property-poll + time.sleep does not pump sync Playwright events;
            # a cold hidden WebView can stay cached as about:blank indefinitely.
            page.wait_for_url(lambda value: "tauri.localhost" in value, timeout=30000)
        assert page, {'qa_pages': [p.url for context in browser.contexts for p in context.pages],
                      'app_exit_code': process.poll()}
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
        page.get_by_role("button", name="Chat", exact=True).wait_for()
        if qa_profile:
            config_path = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('plugin:path|resolve_directory',{directory:13})")
            assert Path(config_path).name == "com.mrcrous.jarvis.qa." + qa_profile, "QA host lacks isolated profile support"
        if pc_status:
            from qa_pc_status import run_pc_status, builtin_qa_suggestions
            with builtin_qa_suggestions(root):
                page.reload()
                run_pc_status(page, fixture)
            browser.close()
            raise SystemExit(0)
        if queued_plan:
            from qa_queued_task import run_queued_plan
            run_queued_plan(page, fixture, helper_options)
            browser.close()
            raise SystemExit(0)
        if queued_task:
            from qa_queued_task import run_queued_task
            run_queued_task(page, fixture)
            browser.close()
            raise SystemExit(0)
        if selected_helper:
            from qa_selected_helper import run_selected_helper
            run_selected_helper(page, fixture, helper_options)
            browser.close()
            raise SystemExit(0)
        if "--fresh-context-only" in sys.argv or "--reasoning-context-only" in sys.argv:
            from qa_fresh_context import run_fresh_context
            run_fresh_context(page, fixture, reasoning_context="--reasoning-context-only" in sys.argv)
            browser.close()
            raise SystemExit(0)
        if "--layout-picker-only" in sys.argv:
            from qa_layout_picker import run_layout_picker
            run_layout_picker(page, fixture)
            browser.close()
            raise SystemExit(0)
        if "--tts-feedback-only" in sys.argv:
            from qa_tts_feedback import run_tts_feedback
            run_tts_feedback(page, fixture)
            browser.close()
            raise SystemExit(0)
        if "--palette-only" in sys.argv:
            from qa_palette import run_palette
            run_palette(page, fixture)
            browser.close()
            raise SystemExit(0)
        if "--quit-only" in sys.argv:
            from qa_quit import run_quit
            run_quit(page, process, fixture)
            browser.close()
            raise SystemExit(0)
        if "--utilities-startup-only" in sys.argv:
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            # Replace the QA document's native startup adapter before settings mounts.
            # No Windows login registration is read or changed by this fixture.
            page.evaluate("""() => {
                const qa = window.__startupQA = {readFailures:1, failChange:true, enabled:false, deferChange:false, changes:[]};
                window.__JARVIS_NATIVE__.plugins.autostart = {
                    isEnabled: async () => { if (qa.readFailures-- > 0) throw 'QA startup read {0}'; return qa.enabled; },
                    enable: async () => { qa.changes.push(true); if (qa.failChange) throw 'QA startup native {0}'; if(qa.deferChange) await new Promise(resolve=>{qa.finishChange=resolve;}); qa.enabled=true; },
                    disable: async () => { qa.changes.push(false); if (qa.failChange) throw 'QA startup native {0}'; qa.enabled=false; }
                };
            }""")
            page.get_by_role("button", name="Settings", exact=True).click()
            dialog = page.locator(".global-settings[role=dialog]")
            settings = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
            startup = settings.get_by_role("switch", name="Launch at login", exact=True)
            settings.get_by_role("alert").filter(has_text="Could not read the startup setting: QA startup read {0}").wait_for()
            expect(startup).to_be_disabled()
            settings.get_by_role("button", name="Retry startup check", exact=True).click()
            expect(startup).to_be_enabled()
            expect(startup).not_to_be_checked()
            startup.click()
            settings.get_by_role("alert").filter(has_text="Could not change the startup setting: QA startup native {0}").wait_for()
            expect(startup).not_to_be_checked()
            select_language(page, 'ru')
            settings.get_by_role("alert").filter(has_text="Не удалось изменить параметр автозапуска: QA startup native {0}").wait_for()
            select_language(page, 'en')
            settings.get_by_role("button", name="Retry startup check", exact=True).click()
            expect(startup).to_be_enabled()
            page.evaluate("window.__startupQA.failChange=false; window.__startupQA.deferChange=true")
            startup.click()
            expect(startup).to_be_disabled()
            dialog.get_by_role("button", name="Close Settings", exact=True).click()
            expect(dialog).to_have_count(0)
            page.get_by_role("button", name="Settings", exact=True).click()
            expect(startup).to_be_disabled()
            page.evaluate("window.__startupQA.finishChange()")
            expect(startup).to_be_checked()
            expect(startup).to_be_enabled()
            expect(settings.get_by_role("alert")).to_have_count(0)
            assert page.evaluate("window.__startupQA.changes") == [True, True]
            dialog.get_by_role("searchbox", name="Search settings", exact=True).fill("Utilities")
            dialog.get_by_role("button", name="Utilities", exact=True).click()
            search = dialog.get_by_role("searchbox", name="Search utilities", exact=True)
            search.fill("регулярных")
            card = dialog.locator('[data-utility="rg"]')
            expect(card).to_be_visible()
            expect(card).to_contain_text("ripgrep regex and file search")
            select_language(page, 'ru')
            expect(card).not_to_contain_text("ripgrep regex and file search")
            expect(card.locator('strong')).to_have_text('rg')
            expect(card).to_contain_text("регулярных")
            page.screenshot(path=str(root / '.cache/utilities-startup-ru.png'))
            select_language(page, 'en')
            expect(card).to_be_visible()
            expect(card).to_contain_text("ripgrep regex and file search")
            assert not errors, errors
            print(json.dumps({'utilities_bilingual_search': True, 'utilities_live_locale': True, 'startup_read_change_recovery': True, 'startup_error_live_locale': True, 'startup_remount_pending': True, 'real_startup_changes': 0, 'page_errors': errors}))
            raise SystemExit(0)
        if "--native-menus-only" in sys.argv:
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            english = ["Show / hide", "Microphone (F4)", "Pause / resume (F7)", "Stop speech (F9)", "Quit Jarvis"]
            russian = ["Показать / скрыть", "Микрофон (F4)", "Пауза / продолжить (F7)", "Остановить озвучку (F9)", "Выйти из Jarvis"]
            def expect_menu_labels(expected):
                page.evaluate("""async expected => {
                    const deadline = performance.now() + 10000;
                    let actual;
                    do {
                        actual = await window.__JARVIS_NATIVE__.core.invoke('native_menu_labels');
                        if (JSON.stringify(actual) === JSON.stringify(expected)) return;
                        await new Promise(resolve => setTimeout(resolve, 25));
                    } while (performance.now() < deadline);
                    throw new Error('Native menu locale did not settle: ' + JSON.stringify(actual));
                }""", expected)
            expect_menu_labels(english)
            page.get_by_role("button", name="Settings", exact=True).click()
            dialog = page.locator(".global-settings[role=dialog]")
            dialog.get_by_role("searchbox", name="Search settings", exact=True).fill("Shortcuts")
            dialog.get_by_role("button", name="Shortcuts", exact=True).click()
            settings = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
            for label in ["Next space", "Previous space", "Open spaces"]:
                settings.get_by_text(label, exact=True).wait_for()
            search = settings.get_by_placeholder("Search shortcuts...", exact=True)
            search.fill("Open command palette")
            settings.get_by_text("Open command palette", exact=True).wait_for()
            select_language(page, 'ru')
            settings.get_by_text("Открыть палитру команд", exact=True).wait_for()
            search = settings.get_by_placeholder("Поиск сочетаний клавиш...", exact=True)
            search.fill("пространства")
            settings.get_by_text("Открыть пространства", exact=True).wait_for()
            expect_menu_labels(russian)
            page.screenshot(path=str(root / '.cache/native-shortcuts-ru.png'))
            select_language(page, 'en')
            search = settings.get_by_placeholder("Search shortcuts...", exact=True)
            search.fill("Spaces")
            settings.get_by_text("Next space", exact=True).wait_for()
            expect_menu_labels(english)
            assert not errors, errors
            print(json.dumps({'native_menu_live_locale': True, 'shortcut_search_live_locale': True, 'spaces_controls_visible': True, 'page_errors': errors}))
            raise SystemExit(0)
        if "--about-only" in sys.argv:
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.get_by_role("button", name="Settings", exact=True).click()
            dialog = page.get_by_role("dialog", name="Settings", exact=True)
            dialog.get_by_role("searchbox", name="Search settings", exact=True).fill("About")
            dialog.get_by_role("button", name="About", exact=True).click()
            settings = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
            settings.get_by_role("heading", name="About Jarvis", exact=True).wait_for()
            expect(settings.get_by_text("Third-party license notices are included with the Jarvis package.", exact=True)).to_be_visible()
            assert not re.search(r"terax|kaizumi|zephium", settings.locator("body").inner_text(), re.I)
            page.screenshot(path=str(root / '.cache/about-menu-en.png'))
            page.keyboard.press("Escape")
            select_language(page, 'ru')
            page.get_by_role("button", name="Настройки", exact=True).click()
            dialog = page.get_by_role("dialog", name="Настройки", exact=True)
            dialog.get_by_role("searchbox").fill("О программе")
            dialog.get_by_role("button", name="О программе", exact=True).click()
            settings = dialog.frame_locator('iframe[src="workbench-settings.html?embedded=1"]')
            settings.get_by_text("Лицензии сторонних компонентов включены в пакет Jarvis.", exact=True).wait_for()
            page.screenshot(path=str(root / '.cache/about-menu-ru.png'))
            assert not errors, errors
            print(json.dumps({'shared_about_navigation': True, 'live_ru_labels': True, 'page_errors': errors}))
            raise SystemExit(0)
        if "--branding-only" in sys.argv:
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.get_by_role("button", name="Files & Tools", exact=True).click()
            frame = page.frame_locator('iframe[src="workbench.html"]')
            frame.locator('body').click(position={"x": 20, "y": 20})
            frame.locator('[data-files-surface]').wait_for()
            frame.locator('[data-files-surface] textarea:visible, button[title="Open AI agent"]:visible').first.wait_for()
            open_panel = frame.get_by_title('Open AI agent', exact=True)
            if open_panel.is_visible(): open_panel.click()
            frame.get_by_title(re.compile(r'^Open AI chat window')).click()
            mini = frame.locator('[data-ai-mini-window]')
            mini.wait_for()
            mini.get_by_title('Switch session', exact=True).click()
            frame.get_by_role('menuitem', name='New session', exact=True).click()
            mini.get_by_text('Ask Jarvis anything', exact=True).wait_for()
            expect(mini.get_by_role('img', name='Jarvis', exact=True)).to_be_visible()
            assert mini.locator('img').evaluate_all('(items) => items.every(image => image.complete && image.naturalWidth > 0)')
            assert mini.locator('button svg').count() >= 4
            assert 'Terax' not in mini.inner_text()
            page.screenshot(path=str(root / '.cache/workspace-branding-en.png'))
            mini.get_by_role('button', name='Explain the open file', exact=False).click()
            composer = frame.locator('textarea[placeholder*="Ask Jarvis anything"]:visible').first
            expect(composer).to_have_value('Explain the file open in the editor.')
            composer.fill('')
            page.get_by_role('button', name='Files & Tools', exact=True).click()
            mini.wait_for()
            select_language(page, 'ru')
            # The same-origin workspace receives the shared language change.
            mini.get_by_text('Задайте вопрос Jarvis', exact=True).wait_for()
            expect(mini.get_by_text('Использовать контекст активного редактора', exact=True)).to_be_visible()
            assert mini.locator('img').evaluate_all('(items) => items.every(image => image.complete && image.naturalWidth > 0)')
            assert 'Terax' not in mini.inner_text()
            page.screenshot(path=str(root / '.cache/workspace-branding-ru.png'))
            assert not errors, errors
            print(json.dumps({'workspace_jarvis_branding': True, 'bundled_logo_loaded': True,
                              'shared_editor_prompt': True, 'live_ru_labels': True, 'page_errors': errors}))
            raise SystemExit(0)
        # Own synthetic status only: no audio, model loading or provider request.
        status_region = page.locator('.speech-backend-status')
        assert status_region.count() == 1
        status_text = 'synthetic speech diagnostic ' * 40
        page.evaluate("text => window.__JARVIS_NATIVE__.event.emit('backend-event', {event:'speech.status',data:{text}})", status_text)
        page.wait_for_function("text => document.querySelector('.speech-backend-status')?.textContent === text", arg=status_text)
        assert status_region.evaluate("e => !e.closest('button') && e.getBoundingClientRect().height <= 38")
        assert status_region.get_attribute('role') == 'status'
        page.evaluate("() => window.__JARVIS_NATIVE__.event.emit('backend-event', {event:'speech.status',data:{text:''}})")
        page.wait_for_function("document.querySelector('.speech-backend-status')?.textContent === ''")
        assert status_region.count() == 1
        print(json.dumps({'speech_status_persistent_live_region':True,'long_status_height_bounded':True}))
        ollama_catalog = page.evaluate("base => window.__JARVIS_NATIVE__.core.invoke('ollama_catalog', {baseUrl: base})", os.environ["JARVIS_QA_MODEL_BASE"])
        assert ollama_catalog == [
            {"id": "qa-installed-unloaded", "loaded": False, "context_length": None},
            {"id": "qa-loaded", "loaded": True, "context_length": 8192},
        ], ollama_catalog
        print(json.dumps({"native_ollama_discovery": True, "unloaded_model_retained": True}))
        native_paths = page.evaluate("""async()=>{
            const invoke=window.__JARVIS_NATIVE__.core.invoke;
            const home=await invoke('plugin:path|resolve_directory',{directory:21});
            const config=await invoke('plugin:path|resolve_directory',{directory:13});
            const joined=await invoke('plugin:path|join',{paths:[home,'Jarvis QA']});
            return {home,config,joined,version:await invoke('plugin:app|version')};
        }""")
        assert Path(native_paths['home']).is_absolute() and Path(native_paths['config']).is_absolute(),native_paths
        assert native_paths['joined'].endswith('Jarvis QA') and native_paths['version'],native_paths
        print(json.dumps({'native_path_and_app_permissions':True}))
        # Exercise the real preference event -> root native webview integration.
        # No persistent preference or OS startup setting is changed.
        if '--bundled-assets' in sys.argv:
            page.evaluate("()=>window.__JARVIS_NATIVE__.webview.getCurrentWebview().setZoom(1)")
            width = page.evaluate("()=>innerWidth")
            try:
                page.evaluate("()=>window.__JARVIS_NATIVE__.event.emit('terax://prefs-changed',{key:'zoomLevel',value:1.25})")
                page.wait_for_function("width=>innerWidth < width * .9", arg=width)
                assert page.evaluate("()=>getComputedStyle(document.documentElement).getPropertyValue('--app-zoom').trim()") in ('', '1')
            finally:
                page.evaluate("()=>window.__JARVIS_NATIVE__.event.emit('terax://prefs-changed',{key:'zoomLevel',value:1})")
                page.wait_for_function("width=>Math.abs(innerWidth-width)<3", arg=width)
            print(json.dumps({'native_shared_zoom':True}))

        if '--bundled-assets' in sys.argv:
            capture_error=page.evaluate("""async()=>{try{await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'screen.capture',params:{destination:'http://127.0.0.1:1234',monitor:1}});return null}catch(error){return JSON.stringify(error)}}""")
            assert capture_error and 'Personal screen capture is disabled in QA.' in capture_error, capture_error
            print(json.dumps({'native_screen_capture_qa_guard':True}))

        prompt_catalog=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'suggestions.catalog',params:{}})")
        assert isinstance(prompt_catalog['builtin'],bool)
        assert isinstance(prompt_catalog['items'],list)
        disk_preflight=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('model_disk_preflight',{info:{repo_id:'qa/disk',revision:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',license:null,gated:false,files:[{path:'model.gguf',size:1024,sha256:'0'.repeat(64)}],total_size:1024},bundle:{descriptor_id:'llm-gguf',kind:'llm',files:['model.gguf']}})")
        assert disk_preflight['required_bytes']==1024+1024*1024 and disk_preflight['available_bytes']>0
        assert disk_preflight['published_sizes_known'] is True
        if '--bundled-assets' in sys.argv:
            hardware = disk_preflight.get('hardware')
            assert isinstance(hardware, dict), 'Bundled native host omitted hardware preflight'
            assert hardware['selected_file_bytes'] == 1024
            assert hardware['planning_bytes'] == 1024 + 1024**3
            ram = hardware['ram']
            total, available = ram['total_bytes'], ram['available_bytes']
            assert isinstance(total, int) and total > 0, ram
            assert isinstance(available, int) and 0 <= available <= total, ram
            expected_ram_status = ('files_exceed_capacity' if total < 1024 else
                                   'files_exceed_available' if available < 1024 else
                                   'limited_headroom' if available < hardware['planning_bytes'] else
                                   'planning_headroom')
            assert ram['status'] == expected_ram_status, ram
            gpus = hardware['gpus']
            assert isinstance(gpus, list) and len(gpus) <= 16, gpus
            for gpu in gpus:
                assert isinstance(gpu['name'], str) and gpu['name'], gpu
                capacity = gpu['total_bytes']
                assert capacity is None or (isinstance(capacity, int) and capacity > 0), gpu
                assert gpu['available_bytes'] is None, 'Dedicated capacity must not claim free VRAM'
                expected_gpu_status = ('unknown' if capacity is None else
                                       'files_exceed_capacity' if capacity < 1024 else 'capacity_only')
                assert gpu['status'] == expected_gpu_status, gpu
            assert hardware['gpu_telemetry'] == ('dedicated_capacity_only' if gpus else 'unavailable')
            print(json.dumps({'bundled_native_hardware_preflight': True, 'ram_status': ram['status'],
                              'gpu_count': len(gpus), 'gpu_telemetry': hardware['gpu_telemetry']}))
        print(json.dumps({'native_model_disk_preflight':True}))
        cloud_before=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'api.usage',params:{}})")["rows"]
        blocked=page.evaluate("""()=>new Promise((resolve,reject)=>{
          const channel=new window.__JARVIS_NATIVE__.core.Channel();
          channel.onmessage=payload=>{const bytes=new Uint8Array(payload);if(bytes[0]===3)resolve(new TextDecoder().decode(bytes.slice(1)));else reject(new Error('Cloud guard allowed an unexpected packet'));};
          const metadata=JSON.stringify({requestId:crypto.randomUUID(),url:'https://qa-cloud-budget.invalid/v1',method:'POST',headers:{},allowPrivateNetwork:false,onEvent:channel});
          window.__JARVIS_NATIVE__.core.invoke('ai_http_stream',new Uint8Array(),{headers:{'x-terax-ai-request':metadata}}).catch(reject);
          setTimeout(()=>reject(new Error('Cloud guard did not respond')),5000);
        })""")
        assert 'Cloud API disabled' in blocked,blocked

        page.get_by_role("button", name="Settings", exact=True).first.click()
        page.get_by_role("button", name="Models & Providers", exact=True).click()
        sf=page.frame_locator('iframe[src*="workbench-settings.html"]')
        sf.get_by_role("heading",name="Models",exact=True).wait_for()
        assert sf.get_by_role("tab").count()==0
        managed_catalogue=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'managed_chat.list',params:{}})")
        managed_record=next((item for item in managed_catalogue['models'] if item['model_id']==managed_fixture['model_id']),None)
        assert managed_record and managed_record['loaded'] is False and managed_record['status']=='unloaded',managed_catalogue
        assert managed_record['repo_id']==managed_fixture['repo'] and managed_record['revision']==managed_fixture['revision']
        if portable_cwd is not None:
            native_installed=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('model_list_installed')")
            assert any(item['repo_id']==managed_fixture['repo'] and item['revision']==managed_fixture['revision'] and item['kind']=='llm' for item in native_installed),native_installed
            assert not any(portable_cwd.iterdir()), 'Portable launch wrote app data into the unrelated working directory'
            print(json.dumps({'portable_native_python_managed_roots_aligned':True,'resource_root_catalogue_identity':managed_fixture['model_id']}))
        managed_name=managed_record['name']
        # Installed but unloaded models are choices without an inference/load call.
        default_picker = sf.get_by_role("button", name=re.compile(r"Gemini 3.5 Flash-Lite"))
        default_picker.click()
        sf.get_by_role("menuitem", name=re.compile(r"QA Unloaded Chat")).wait_for()
        assert 'Not connected' not in sf.get_by_role("menu").inner_text()
        sf.get_by_role("menuitem", name=re.compile(r"QA Unloaded Chat")).click()
        sf.get_by_role("button", name=re.compile(r"QA Unloaded Chat")).click()
        sf.get_by_role("menuitem", name=re.compile(r"QA Gemma")).click()
        sf.get_by_role("button", name="Select an available model", exact=True).click()
        sf.get_by_role("menuitem", name=re.compile(r"QA Unloaded Chat")).click()
        sf.get_by_role("button", name=re.compile(r"QA Gemma")).wait_for()
        sf.get_by_role("button", name=re.compile(r"QA Unloaded Chat")).wait_for()
        print(json.dumps({"settings_unloaded_local_models":True,"independent_autocomplete_selection":True}))
        sf.get_by_role("button",name=re.compile(r"QA Gemma")).click()
        sf.get_by_role("menuitem",name=re.compile(r"qa-installed-unloaded")).click()
        sf.get_by_role("button",name=re.compile(r"qa-installed-unloaded")).wait_for()
        sf.get_by_role("button",name=re.compile(r"QA Unloaded Chat")).click()
        sf.get_by_role("menuitem",name=re.compile(r"qa-loaded")).click()
        sf.get_by_role("button",name=re.compile(r"qa-loaded")).wait_for()
        # Autocomplete selection must leave the default pointing at the unloaded model.
        sf.get_by_role("button",name=re.compile(r"qa-installed-unloaded")).click()
        sf.get_by_role("menuitem",name=re.compile(r"QA Gemma")).click()
        print(json.dumps({"settings_ollama_installed_choices":True,"ollama_independent_autocomplete":True}))
        # Both settings selectors consume the real native managed catalogue.
        wait_native_preferences(page, {'defaultModelId': 'lmstudio-local', 'autocompleteModelId': 'qa-loaded', 'autocompleteProvider': 'ollama'})
        managed_settings_before=page.evaluate("""async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return {defaultModelId:await store.get('defaultModelId'),autocompleteModelId:await store.get('autocompleteModelId'),autocompleteProvider:await store.get('autocompleteProvider')};}""")
        sf.get_by_role('button',name=re.compile(r'QA Gemma')).click()
        sf.get_by_role('menuitem').filter(has_text=managed_name).click()
        wait_native_preferences(page, {'defaultModelId': managed_fixture['model_id']})
        managed_settings_selected=page.evaluate("""async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return {defaultModelId:await store.get('defaultModelId'),autocompleteModelId:await store.get('autocompleteModelId'),autocompleteProvider:await store.get('autocompleteProvider')};}""")
        assert managed_settings_selected['defaultModelId']==managed_fixture['model_id'],managed_settings_selected
        assert managed_settings_selected['autocompleteModelId']==managed_settings_before['autocompleteModelId']
        sf.get_by_role('button',name=re.compile(r'qa-loaded')).click()
        sf.get_by_role('menuitem').filter(has_text=managed_name).click()
        wait_native_preferences(page, {'autocompleteModelId': managed_fixture['model_id'], 'autocompleteProvider': 'openai-compatible'})
        managed_settings_selected=page.evaluate("""async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return {defaultModelId:await store.get('defaultModelId'),autocompleteModelId:await store.get('autocompleteModelId'),autocompleteProvider:await store.get('autocompleteProvider')};}""")
        assert managed_settings_selected=={'defaultModelId':managed_fixture['model_id'],'autocompleteModelId':managed_fixture['model_id'],'autocompleteProvider':'openai-compatible'},managed_settings_selected
        sf.get_by_role('button').filter(has_text=managed_name).first.click()
        sf.get_by_role('menuitem',name=re.compile(r'QA Gemma')).click()
        sf.get_by_role('button').filter(has_text=managed_name).click()
        sf.get_by_role('menuitem',name=re.compile(r'qa-loaded')).click()
        wait_native_preferences(page, managed_settings_before)
        managed_settings_restored=page.evaluate("""async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return {defaultModelId:await store.get('defaultModelId'),autocompleteModelId:await store.get('autocompleteModelId'),autocompleteProvider:await store.get('autocompleteProvider')};}""")
        assert managed_settings_restored==managed_settings_before,managed_settings_restored
        managed_after_settings=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'managed_chat.list',params:{}})")
        assert next(item for item in managed_after_settings['models'] if item['model_id']==managed_fixture['model_id'])['loaded'] is False
        print(json.dumps({'native_managed_catalogue_settings_choices':True,'managed_stable_identity':managed_fixture['model_id'],'no_catalogue_inference':True}))
        assert sf.get_by_role("button",name="Add provider",exact=True).count() == 0
        sf.get_by_role("button",name="Jarvis — Speech Recognition",exact=True).click()
        voice_menu = sf.get_by_role("menu")
        voice_menu.get_by_role("menuitem",name="Whisper.cpp (local)",exact=True).wait_for()
        assert voice_menu.get_by_role("menuitem",name="OpenAI Whisper",exact=True).count() == 0
        assert voice_menu.get_by_role("menuitem",name="Groq Whisper",exact=True).count() == 0
        voice_menu.get_by_role("menuitem",name="Jarvis — Speech Recognition",exact=True).click()
        page.get_by_role("button",name="Integrations",exact=True).click()
        sf.get_by_role("heading",name="Model providers",exact=True).wait_for()
        sf.get_by_role("button",name="Add provider",exact=True).wait_for()
        sf.get_by_label("Whisper.cpp base URL",exact=True).fill(os.environ["JARVIS_QA_MODEL_BASE"].removesuffix("/v1")+"/stt-loading")
        sf.get_by_role("button",name="Check Whisper.cpp connection",exact=True).click()
        sf.get_by_text("Whisper.cpp is unavailable or loading",exact=True).wait_for()
        page.get_by_role("button",name="Models & Providers",exact=True).click()
        sf.get_by_role("button",name="Jarvis — Speech Recognition",exact=True).click()
        assert sf.get_by_role("menu").get_by_role("menuitem",name="Whisper.cpp (local)",exact=True).count() == 0
        sf.get_by_role("menuitem",name="Jarvis — Speech Recognition",exact=True).click()
        print(json.dumps({"provider_setup_only_in_integrations":True,"speech_choices_filter_credentials_and_health":True}))
        page.get_by_role("button",name="Documents",exact=True).click()
        page.get_by_role("heading",name="Documents",exact=True).wait_for()
        page.get_by_role("button",name="Close Settings",exact=True).click()
        # Passive audit of every settings section: opening must not run an AI,
        # start recording, launch media, or require changing the user's prefs.
        page.get_by_role("button", name="Settings", exact=True).first.click()
        audit = []
        dialog = page.get_by_role("dialog", name="Settings", exact=True)
        sections = ['General','Models & Providers','Integrations','Agents','Themes','Speech & Voice','Browser','Documents','API Usage','Queue','Editor','Shortcuts','Utilities','Network & Proxy','Camera','Prompts','Features']
        advanced_sections = {'Editor','Shortcuts','Utilities','Network & Proxy','Camera','Prompts','Features'}
        settings_document = page.frame_locator('iframe[src*="workbench-settings.html"]')
        settings_document.locator('main').wait_for()
        settings_document.locator('html').evaluate("e=>window.__qaDocument='persistent-settings'")
        for label in sections:
            if label in advanced_sections:
                details = dialog.locator('details.settings-advanced')
                if details.get_attribute('open') is None:
                    details.locator('summary').click()
            dialog.locator('.settings-navigation').get_by_role('button',name=label,exact=True).click()
            page.wait_for_timeout(150)
            if label in ['General','Models & Providers','Integrations','Agents','Themes','Editor','Shortcuts']:
                surface = page.frame_locator('iframe[src*="workbench-settings.html"]').locator('main')
                surface.locator('h1').wait_for()
            else:
                surface = dialog.locator('.global-settings-scroll')
            assert surface.inner_text().strip(), label
            assert surface.evaluate('(e)=>e.scrollWidth <= e.clientWidth + 1'), label
            audit.append({'section':label,'controls':surface.locator('button,input,select,textarea').count()})
            assert settings_document.locator('html').evaluate("()=>window.__qaDocument")=='persistent-settings',label
        # Real read-only autostart permission; mutation handlers are synthetic.
        startup_state = page.evaluate("()=>window.__JARVIS_NATIVE__.plugins.autostart.isEnabled()")
        assert isinstance(startup_state, bool)
        page.evaluate("()=>{const plugins=window.__JARVIS_NATIVE__.plugins;window.__qaAutostart=plugins.autostart;window.__qaStartupCalls=[];plugins.autostart={isEnabled:async()=>false,enable:async()=>{window.__qaStartupCalls.push('enable')},disable:async()=>{window.__qaStartupCalls.push('disable')}};}")
        try:
            dialog.locator('.settings-navigation').get_by_role('button',name='Models & Providers',exact=True).click()
            dialog.locator('.settings-navigation').get_by_role('button',name='General',exact=True).click()
            startup = settings_document.get_by_text('Launch at login',exact=True).locator('../..').get_by_role('switch')
            startup.wait_for()
            page.wait_for_timeout(150)
            assert startup.get_attribute('aria-checked') == 'false'
            startup.click()
            page.wait_for_function("()=>window.__qaStartupCalls.join(',')==='enable'")
            wait_native_preferences(page, {'autostart': True})
            startup.click()
            page.wait_for_function("()=>window.__qaStartupCalls.join(',')==='enable,disable'")
            wait_native_preferences(page, {'autostart': False})
        finally:
            page.evaluate("()=>{window.__JARVIS_NATIVE__.plugins.autostart=window.__qaAutostart;delete window.__qaAutostart;}")
        print(json.dumps({'embedded_autostart_parent_bridge': True, 'real_startup_mutations': False}))
        # One saved shortcut binding works in the native shell and both frames.
        details = dialog.locator('details.settings-advanced')
        if details.get_attribute('open') is None:
            details.locator('summary').click()
        previous_shortcuts=page.evaluate("async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});const previous=(await store.get('shortcuts'))||{};await store.set('shortcuts',{...previous,'settings.open':[{ctrl:true,alt:true,key:'l'}]});await store.save();return previous;}")
        dialog.locator('.settings-navigation').get_by_role('button',name='Shortcuts',exact=True).click()
        settings_document.get_by_text('L',exact=True).first.wait_for()
        dialog.get_by_role('searchbox',name='Search settings',exact=True).focus()
        page.keyboard.press('Control+Alt+l')
        settings_document.get_by_role('heading',name='General',exact=True).wait_for()
        settings_document.get_by_role('button').first.focus()
        page.keyboard.press('Control+Alt+l')
        settings_document.get_by_role('heading',name='General',exact=True).wait_for()
        dialog.get_by_role('button',name='Close Settings',exact=True).click()
        page.frame_locator('iframe[src="workbench.html"]').locator('[data-shared-composer] textarea').focus()
        page.keyboard.press('Control+Alt+l')
        settings_document.get_by_role('heading',name='General',exact=True).wait_for()
        page.evaluate("async previous=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});await store.set('shortcuts',previous);await store.save();}",previous_shortcuts)
        page.evaluate("()=>{window.__qaConfirm=window.__JARVIS_NATIVE__.confirm(window,'Jarvis confirmation QA');}")
        page.get_by_role('dialog',name='Jarvis',exact=True).get_by_role('button',name='Cancel',exact=True).click()
        assert page.evaluate('()=>window.__qaConfirm') is False
        page.evaluate("()=>{window.__qaConfirm=window.__JARVIS_NATIVE__.confirm(window,'Jarvis Escape QA');}")
        page.get_by_role('dialog',name='Jarvis',exact=True).wait_for()
        page.keyboard.press('Escape')
        page.get_by_role('dialog',name='Jarvis',exact=True).wait_for(state='hidden')
        assert page.evaluate('()=>window.__qaConfirm') is False
        assert dialog.is_visible(), 'Confirmation Escape must not close settings'
        print(json.dumps({'settings_document_persistent':True,'shared_shortcut_override_three_surfaces':True,'jarvis_confirmation_cancelled':True}))
        details = dialog.locator('details.settings-advanced')
        if details.get_attribute('open') is None:
            details.locator('summary').click()
        dialog.locator('.settings-navigation').get_by_role('button',name='Utilities',exact=True).click()
        utilities = dialog.get_by_role('region',name='Utilities and runtimes',exact=True)
        utilities.locator('[data-utility="python"]').wait_for()
        assert 'jarvis-venv' in utilities.locator('[data-utility="python"]').inner_text()
        # Settings installation is explicit; ordinary QA never downloads packages.
        yq = utilities.locator('[data-utility="yq"]')
        if yq.get_by_role('button',name='Install…',exact=True).count():
            yq.get_by_role('button',name='Install…',exact=True).click()
            plan = utilities.locator('fieldset.utility-install')
            assert 'MikeFarah.yq' in plan.inner_text()
            page.wait_for_function("e => { const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }", arg=plan.element_handle(), timeout=5000)
            assert plan.evaluate('e => e === document.activeElement')
            assert plan.get_by_role('button',name='Install package',exact=True).is_disabled()
            plan.get_by_role('checkbox').check()
            if os.environ.get('JARVIS_QA_INSTALL') == '1':
                plan.get_by_role('button',name='Install package',exact=True).click()
                utilities.get_by_text('Installing…',exact=True).last.wait_for()
                plan.wait_for(state='hidden',timeout=180000)
                assert 'Package installation completed' in utilities.inner_text()
                assert 'Not installed' not in yq.inner_text()
                print(json.dumps({'real_settings_yq_install':True}))
            else:
                plan.get_by_role('button',name='Cancel',exact=True).click()
        inventory = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('utility_inventory')")
        agy = next(item for item in inventory if item['id'] == 'agy')
        assert agy['category'] == 'AI' and agy['install_package'] is None
        if agy['available']:
            assert agy['direct'] and Path(agy['path']).is_file()
        (root/'.cache/utilities-inventory.json').write_text(json.dumps(inventory,indent=2),encoding='utf8')
        page.screenshot(path=str(root/'.cache/utilities-dark.png'))
        utilities.get_by_role('searchbox',name='Search utilities',exact=True).fill('no-such-tool-qa')
        utilities.get_by_text('No matching utilities',exact=True).wait_for()
        assert page.locator('html').evaluate('(e)=>getComputedStyle(e).overflow') == 'hidden'
        dialog.locator('.settings-navigation').get_by_role('button',name='Documents',exact=True).click()
        embedding = dialog.get_by_role('region',name='Multimodal embeddings',exact=True)
        with page.expect_file_chooser() as chooser:
            embedding.get_by_role('button',name='Open file…',exact=True).click()
        chooser.value.set_files(str(fixture/'read.txt'))
        embedding.get_by_text('read.txt',exact=True).wait_for()
        chosen_path = embedding.get_by_label('Path to embedding file',exact=True).input_value()
        assert '.cache' in chosen_path and 'attachments' in chosen_path,chosen_path
        indexed = page.evaluate("""q=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.search',params:{query:q,limit:10}})""",(fixture/'read.txt').read_text())
        assert indexed['items'] and not indexed['items'][0]['indexed']
        search = dialog.get_by_role('searchbox',name='Search settings',exact=True)
        search.fill('Models')
        assert dialog.get_by_role('button',name='Models & Providers',exact=True).is_visible()
        dialog.locator('.settings-navigation').get_by_role('button',name='Browser',exact=True).wait_for(state='hidden')
        search.fill('no-such-settings-qa')
        assert dialog.get_by_text('No matching settings',exact=True).is_visible()
        search.fill('')
        dialog.get_by_role('button',name='Integrations',exact=True).click()
        settings = page.frame_locator('iframe[src*="workbench-settings.html"]')
        local = settings.locator('[data-llama-settings]')
        local.locator('summary').click()
        local.get_by_label('Chat GGUF path',exact=True).wait_for()
        local.get_by_label('Chat GGUF path',exact=True).fill('invalid.txt')
        assert local.get_by_role('button',name='Start & use model',exact=True).is_disabled()
        assert local.get_by_role('button',name='Unload model',exact=True).is_disabled()
        # Editing here is a draft: do not start a server or save a model setting.
        dialog.get_by_role('button',name='Close Settings',exact=True).click()
        page.set_viewport_size({'width':480,'height':720})
        if not page.get_by_role('button',name='Settings',exact=True).first.is_visible(): page.get_by_role('button',name='Open Navigation',exact=True).click()
        page.get_by_role('button',name='Settings',exact=True).first.click()
        dialog = page.get_by_role('dialog',name='Settings',exact=True)
        chooser = dialog.locator('.settings-mobile-select select')
        chooser.select_option('Documents')
        dialog.get_by_role('heading',name='Documents',exact=True).wait_for()
        assert dialog.evaluate('(e)=>e.scrollWidth <= e.clientWidth+1')
        for field in dialog.locator('.document-panel input,.document-panel textarea,.document-panel select').all():
            assert field.evaluate('(e)=>getComputedStyle(e).borderRadius !== "0px"')
        page.screenshot(path=str(root/'.cache/ui-audit-mobile.png'))
        dialog.get_by_role('button',name='Close Settings',exact=True).click()
        (root/'.cache/ui-audit-sections.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8')
        for width,height in [(2560,1440),(1920,1080),(1440,900),(1024,768),(800,600),(640,480)]:
            page.set_viewport_size({'width':width,'height':height})
            if not page.get_by_role('button',name='Settings',exact=True).first.is_visible():page.get_by_role('button',name='Open Navigation',exact=True).click()
            page.get_by_role('button',name='Settings',exact=True).first.click()
            dialog=page.get_by_role('dialog',name='Settings',exact=True)
            box=dialog.bounding_box()
            assert box and box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1,(width,height,box)
            for label in ['Models & Providers','Integrations','General','Agents','Themes','Documents','Speech & Voice']:
                dialog.locator('.settings-navigation').get_by_role('button',name=label,exact=True).click()
                page.wait_for_timeout(100)
                assert dialog.evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),(width,height,label)
                if label in ['Models & Providers','Integrations','General','Agents','Themes']:
                    settings=page.frame_locator('iframe[src*="workbench-settings.html"]')
                    settings.locator('main h1').wait_for()
                    assert settings.locator('main').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'),(width,height,label)
            dialog.get_by_role('button',name='Models & Providers',exact=True).click()
            page.frame_locator('iframe[src*="workbench-settings.html"]').get_by_role('heading',name='Models',exact=True).wait_for()
            if width in [640,1440,2560]:
                page.screenshot(path=str(root/f'.cache/settings-polish-{width}.png'))
            dialog.get_by_role('button',name='Close Settings',exact=True).click()
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('button',name='Settings',exact=True).first.click()
        menu=page.get_by_role('dialog',name='Settings',exact=True)
        assert menu.get_by_role('button',name='Google API',exact=True).count()==0
        menu.locator('summary').filter(has_text='Advanced Settings').click()
        menu.get_by_role('button',name='Features',exact=True).click()
        menu.get_by_role('textbox',name='Search tools',exact=True).fill('data.query')
        tool=menu.locator('[data-capability="data.query"]')
        tool.wait_for()
        tool.get_by_role('button',name='Use in chat',exact=True).click()
        menu.wait_for(state='hidden')
        draft=page.frame_locator('iframe[src="workbench.html"]').locator('[data-shared-composer] textarea')
        assert 'data.query' in draft.input_value()
        draft.fill('')
        page.get_by_role('button',name='Settings',exact=True).first.click()
        menu.get_by_role('button',name='Queue',exact=True).click()
        assert menu.get_by_role('button',name='Add',exact=True).is_disabled()
        menu.get_by_role('button',name='Close Settings',exact=True).click()
        snapshot = page.evaluate("""async()=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});const out={};for(const k of ['lmstudioBaseURL','lmstudioModelId','lmstudioReasoning','defaultModelId'])out[k]=(await s.get(k))??null;return out;}""")
        page.evaluate("""async base=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});for(const[k,v]of Object.entries({lmstudioBaseURL:base,lmstudioModelId:'google/gemma-4-e4b',lmstudioReasoning:'off',defaultModelId:'gemini-3.5-flash-lite'}))await s.set(k,v);await s.save();}""",os.environ.get('JARVIS_QA_MODEL_BASE','http://127.0.0.1:1234/v1'))
        page.reload()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        frame=page.frame_locator('iframe[src="workbench.html"]')
        frame.locator('[data-unified-chat]').wait_for()
        frame.locator('[data-unified-chat] header button').first.click()
        search=frame.get_by_placeholder('Search models, providers, capabilities…',exact=True)
        search.fill('gemma')
        assert frame.get_by_role('menuitem').count()>0
        search.fill('LM Studio')
        frame.get_by_role('menuitem').filter(has_text='LM Studio').first.click()
        # Model picking awaits preference/session writes. Let that complete and
        # close its menu before opening the next one; an early reopen can be
        # closed by the preceding asynchronous selection's final state update.
        wait_native_preferences(page, {'defaultModelId': 'lmstudio-local'})
        frame.get_by_title('Model: QA Gemma',exact=True).wait_for()
        if search.is_visible():
            search.press('Escape')
        search.wait_for(state='hidden')
        managed_main_before=page.evaluate("async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return store.get('defaultModelId');}")
        frame.get_by_title('Model: QA Gemma',exact=True).click()
        search.wait_for(state='visible')
        search.fill(managed_fixture['repo'])
        frame.get_by_role('menuitem').filter(has_text=managed_name).click()
        wait_native_preferences(page, {'defaultModelId': managed_fixture['model_id']})
        managed_main_selected=page.evaluate("async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return store.get('defaultModelId');}")
        assert managed_main_selected==managed_fixture['model_id'],managed_main_selected
        frame.get_by_title('Model: '+managed_name,exact=True).wait_for()
        if search.is_visible():
            search.press('Escape')
        search.wait_for(state='hidden')
        managed_main_catalogue=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'managed_chat.list',params:{}})")
        assert next(item for item in managed_main_catalogue['models'] if item['model_id']==managed_fixture['model_id'])['loaded'] is False
        # Restore the same prior LM Studio choice before any chat request.
        frame.get_by_title('Model: '+managed_name,exact=True).click()
        search.wait_for(state='visible')
        search.fill('LM Studio')
        frame.get_by_role('menuitem').filter(has_text='LM Studio').first.click()
        wait_native_preferences(page, {'defaultModelId': managed_main_before})
        managed_main_restored=page.evaluate("async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return store.get('defaultModelId');}")
        assert managed_main_restored==managed_main_before,managed_main_restored
        frame.get_by_title('Model: QA Gemma',exact=True).wait_for()
        if search.is_visible():
            search.press('Escape')
        search.wait_for(state='hidden')
        managed_unloaded=page.evaluate("id=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'managed_chat.unload',params:{model_id:id}})",managed_fixture['model_id'])
        assert managed_unloaded['model_id']==managed_fixture['model_id'] and managed_unloaded['loaded'] is False and managed_unloaded['status']=='unloaded',managed_unloaded
        print(json.dumps({'native_managed_main_picker':True,'managed_unload_without_start':True,'previous_model_restored':True}))
        picker=frame.get_by_title('Switch session',exact=True)
        if picker.count():
            picker.click()
            frame.get_by_role('menuitem',name='New session',exact=True).click()
        for _ in range(150):
            state=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})")
            if state['active_model']==expected_model:break
            page.wait_for_timeout(100)
        assert state['active_model']==expected_model,state['active_model']
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

        editor_fixture = fixture / "qa-editor.xml"
        editor_fixture.write_text('<resources><string name="disk">XML_DISK_VALUE</string></resources>',encoding="utf8")
        page.get_by_role('button',name='Files & Tools',exact=True).click()
        page.evaluate("path=>window.__JARVIS_NATIVE__.event.emit('terax:open-file',[path])",str(editor_fixture).replace('\\','/'))
        editor = frame.locator('.cm-content').filter(has_text='XML_DISK_VALUE')
        editor.wait_for()
        editor.fill('<resources><string name="unsaved">XML_EDITOR_UNSAVED_MARKER</string></resources>')
        request("QA_EDITOR_CONTEXT: What's in this xml that I opened?")
        assert 'EDITOR_CONTEXT_OK XML_EDITOR_UNSAVED_MARKER' in result()['reply']
        assert 'XML_DISK_VALUE' in editor_fixture.read_text(encoding='utf8')
        print(json.dumps({'live_unsaved_editor_reaches_local_model':True,'simple_editor_query_no_tools':True}))
        if "--bundled-assets" in sys.argv:
            host_path = str(editor_fixture).replace('\\','/')
            linux_path = '/mnt/' + host_path[0].lower() + host_path[2:]
            canonical = page.evaluate("args=>window.__JARVIS_NATIVE__.core.invoke('fs_canonicalize',args)", {'path':linux_path,'workspace':{'kind':'wsl','distro':'Ubuntu'}})
            assert canonical.startswith('/mnt/'), canonical
            read_back = page.evaluate("args=>window.__JARVIS_NATIVE__.core.invoke('fs_read_file',args)", {'path':canonical,'workspace':{'kind':'wsl','distro':'Ubuntu'}})
            assert 'XML_DISK_VALUE' in read_back['content']
            print(json.dumps({'packaged_wsl_canonical_read_roundtrip':True}))
            distros = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('wsl_list_distros')")
            if any(distro['name'] == 'Ubuntu' and distro['running'] for distro in distros):
                unc_dir = Path(r'\\wsl.localhost\Ubuntu\tmp') / ('jarvis-editor-qa-' + uuid.uuid4().hex)
                unc_file = unc_dir / 'editor.xml'
                unc_dir.mkdir()
                try:
                    unc_file.write_text('<resources>REAL_WSL_UNC_XML</resources>',encoding='utf8')
                    linux_file = '/tmp/' + unc_dir.name + '/editor.xml'
                    resolved = page.evaluate("args=>window.__JARVIS_NATIVE__.core.invoke('fs_canonicalize',args)", {'path':linux_file,'workspace':{'kind':'wsl','distro':'Ubuntu'}})
                    assert resolved.startswith('/tmp/'), resolved
                    xml = page.evaluate("args=>window.__JARVIS_NATIVE__.core.invoke('fs_read_file',args)", {'path':resolved,'workspace':{'kind':'wsl','distro':'Ubuntu'}})
                    assert xml['content'] == '<resources>REAL_WSL_UNC_XML</resources>'
                    print(json.dumps({'real_running_wsl_unc_canonical_read':True}))
                finally:
                    unc_file.unlink(missing_ok=True)
                    unc_dir.rmdir()
            else:
                print(json.dumps({'real_running_wsl_unc_canonical_read':'skipped: Ubuntu is not running; no distro was started'}))
        page.get_by_role('button',name='Chat',exact=True).click()
        request("Ответь ровно JARVIS_SHARED_READY. Не используй инструменты.")
        assert 'JARVIS_SHARED_READY' in result()['reply']
        frame.get_by_text('JARVIS_SHARED_READY',exact=True).first.wait_for()
        request("REPEATED_READ_FAILURE_QA: read the missing XML file and retry the same path.")
        assert 'READ_LOOP_BOUNDED_QA' in result()['reply']
        print(json.dumps({'failed_read_loop_bounded':True,'final_explanation_without_tools':True}))
        request("RAG_WORKSPACE_IMPORT_QA: index the explicitly selected workspace read.txt file. Wait for approval.")
        frame.get_by_role('button',name='Approve',exact=True).first.click()
        assert 'RAG_WORKSPACE_IMPORT_OK' in result()['reply']
        indexed_workspace=page.evaluate("q=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.search',params:{query:q,limit:20}})",(fixture/'read.txt').read_text())
        assert any(item.get('citation',{}).get('source_kind')=='workspace' for item in indexed_workspace['items']),indexed_workspace
        print(json.dumps({'approved_workspace_rag_common_chat':True}))
        workspace_item = next(item for item in indexed_workspace['items'] if item.get('citation',{}).get('source_kind')=='workspace')
        library = frame.locator('[data-document-library]')
        library_was_open = library.get_attribute('open') is not None
        if not library_was_open:
            library.locator('summary').click()
        library.get_by_role('textbox',name='Search uploaded files',exact=True).fill('')
        library.get_by_role('button',name='Search',exact=True).click()
        source_row = library.locator(f'[data-document-id="{workspace_item["id"]}"]')
        source_row.get_by_role('button',name='Refresh source',exact=True).click()
        library.get_by_text('Source is unchanged; existing embeddings were kept.',exact=True).wait_for()
        original_workspace_text = (fixture/'read.txt').read_text()
        try:
            (fixture/'read.txt').write_text(original_workspace_text+'\nRAG_REFRESH_NATIVE_OK',encoding='utf-8')
            source_row.get_by_role('button',name='Refresh source',exact=True).click()
            library.get_by_text('Source updated. Re-embed to refresh semantic search.',exact=True).wait_for()
            refreshed_source = page.evaluate("identifier=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.read',params:{identifier}})",workspace_item['id'])
            assert 'RAG_REFRESH_NATIVE_OK' in refreshed_source['text'] and not refreshed_source['indexed']
        finally:
            (fixture/'read.txt').write_text(original_workspace_text,encoding='utf-8')
            page.evaluate("identifier=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.refresh',params:{identifier}})",workspace_item['id'])
            if not library_was_open:
                library.locator('summary').click()
        print(json.dumps({'source_refresh_native_ui':True,'refresh_unchanged_and_changed':True}))

        request("SUBAGENT_PROVIDER_QA: use run_subagent explore, then summarize.")
        assert 'SUBAGENT_PROVIDER_QA_OK' in result()['reply']
        request("CANCEL_PARENT_QA: delegate a slow read-only task.")
        cancel_url = os.environ['JARVIS_QA_MODEL_BASE'].removesuffix('/v1') + '/qa/cancel'
        deadline = time.monotonic() + 15
        while not httpx.get(cancel_url, trust_env=False).json()['started']:
            assert time.monotonic() < deadline, 'child request did not start'
            page.wait_for_timeout(100)
        frame.get_by_role('button',name='Stop',exact=True).click()
        deadline = time.monotonic() + 3
        while not httpx.get(cancel_url, trust_env=False).json()['cancelled']:
            assert time.monotonic() < deadline, 'child request survived parent Stop'
            page.wait_for_timeout(100)
        frame.get_by_role('button',name='Send',exact=True).wait_for()
        request("Reply JARVIS_SHARED_READY after cancellation.")
        assert 'JARVIS_SHARED_READY' in result()['reply']
        print(json.dumps({'subagent_cancel_socket_closed':True,'chat_reusable_after_stop':True}))
        composer=frame.locator('[data-shared-composer] textarea')
        composer.fill('Ответь ровно WORKBENCH_MIRROR_READY. Не используй инструменты.')
        before=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}})")
        baseline.clear();baseline.update(m['id'] for m in before['messages'])
        frame.get_by_role('button',name='Send',exact=True).click()
        assert 'WORKBENCH_MIRROR_READY' in result()['reply']
        frame.get_by_label('Session tokens',exact=True).wait_for()
        assert 'Input: 0' not in frame.get_by_label('Session tokens',exact=True).inner_text()
        page.get_by_role('button',name='Files & Tools',exact=True).click()
        frame.locator('[data-files-surface]').wait_for(state='visible')
        page.get_by_role('button',name='Chat',exact=True).click()
        frame.get_by_text('WORKBENCH_MIRROR_READY',exact=True).first.wait_for()
        imported=fixture/'attach.txt';imported.write_text('ATTACHMENT_ECOSYSTEM_QA',encoding='utf8')
        frame.locator('input[type=file]').first.set_input_files(str(imported))
        frame.get_by_text('attach.txt',exact=True).first.wait_for()
        frame.locator('[data-shared-attachments] summary').first.click()
        frame.get_by_role('button',name='Run embedding',exact=True).wait_for()
        # Selecting a file must create an actual bounded local copy, without sending it.
        copies=list((root/'.cache/attachments').glob('*.txt'))
        assert any(p.read_bytes()==b'ATTACHMENT_ECOSYSTEM_QA' for p in copies)
        frame.locator('[data-shared-attachments]').get_by_role('button',name='Remove',exact=True).click()
        library = frame.locator('[data-document-library]')
        library.locator('summary').click()
        library.get_by_label('Search uploaded files',exact=True).fill('ATTACHMENT_ECOSYSTEM_QA')
        library.get_by_role('button',name='Search',exact=True).click()
        library.get_by_text('attach.txt',exact=False).first.wait_for()
        library.get_by_role('button',name='Use in chat',exact=True).first.click()
        assert 'Read uploaded document' in composer.input_value()
        composer.fill('')
        library.locator('summary').click()

        request(f"Прочитай файл {fixture / 'read.txt'} инструментом read_file и ответь только его содержимым. Не меняй файлы.")
        assert (fixture/'read.txt').read_text() in result()['reply']
        request(f"Создай через write_file файл {target} с содержимым SHARED_APPROVED_WRITE. Никаких других изменений. Дождись подтверждения.")
        approve=frame.get_by_role('button',name='Approve',exact=True)
        approve.first.wait_for(timeout=120000)
        assert not target.exists()
        assert page.get_by_role('button',name='Chat',exact=True).is_visible()
        approve.first.click()
        result()
        assert target.read_text(encoding='utf8').strip()=='SHARED_APPROVED_WRITE'
        # Optional utilities use the same catalogue/approval bridge as every model.
        def utility(name, args, approval=False, before=None):
            request('UTILITY_QA:' + json.dumps({'name':name,'args':args}))
            if approval:
                approve.first.wait_for(timeout=15000)
                if before: before()
                approve.first.click()
            assert 'UTILITY_QA_OK' in result()['reply']
        utility('utility_catalog', {})
        if os.environ.get('JARVIS_QA_ARGON_CLI') == '1':
            assert agy['available'], 'Antigravity CLI smoke explicitly requested but agy is missing'
            utility('utility_run', {'tool':'agy','args':['--model','gemini-4-argon-low','--mode','plan','--print-timeout','60s','-p','Reply JARVIS_ARGON_OK only. Do not use tools, files or commands.'],'cwd':str(fixture),'timeout_secs':90}, True)
            print(json.dumps({'real_argon_cli_via_shared_chat': True}))
        utility('run_code', {'language':'python','code':'print(6*7)','cwd':str(fixture)}, True)
        # Import the actual execution above only after its original SDK result
        # reaches native durable conversation storage. No fabricated result or
        # caller-provided transcript is used as an ingestion fixture.
        terminal_saved = None
        terminal_deadline = time.monotonic() + 15
        while time.monotonic() < terminal_deadline:
            terminal_saved = page.evaluate("""async()=>{
                const store=await window.__JARVIS_NATIVE__.store.load('terax-ai-sessions.json',{defaults:{}});
                const session_id=await store.get('activeId');
                if(!session_id)return null;
                const raw=await window.__JARVIS_NATIVE__.core.invoke('ai_session_load',{id:session_id});
                if(!raw.byteLength)return null;
                const messages=JSON.parse(new TextDecoder().decode(raw));
                for(const message of [...messages].reverse())for(const part of [...(message.parts??[])].reverse()){
                    if(message.role==='assistant'&&part.type==='tool-jarvis_action'&&part.state==='output-available'
                       &&part.input?.name==='run_code'&&part.input?.args?.code==='print(6*7)'
                       &&part.output?.stdout?.trim()==='42')
                        return {session_id,task_id:part.toolCallId,turn_id:message.id,stdout:part.output.stdout};
                }
                return null;
            }""")
            if terminal_saved:
                break
            page.wait_for_timeout(200)
        assert terminal_saved, 'Executed terminal task was not persisted by native session storage'
        terminal_args = {key:terminal_saved[key] for key in ('session_id','task_id')}
        terminal_args['stream'] = 'stdout'
        terminal_read_denied = page.evaluate("""async args=>{
            try{await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'services.read',params:{name:'documents.import_terminal',args}});return '';}
            catch(error){return String(error);}
        }""",terminal_args)
        assert 'approved action' in terminal_read_denied, terminal_read_denied
        request('RAG_TERMINAL_IMPORT_QA:' + json.dumps(terminal_args))
        approve.first.wait_for(timeout=15000)
        approve.first.click()
        assert 'RAG_TERMINAL_IMPORT_OK' in result()['reply']
        terminal_index = page.evaluate("q=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.search',params:{query:q,limit:20}})",terminal_saved['task_id'])
        terminal_hit = next((item for item in terminal_index['items']
            if item.get('citation',{}).get('source_kind')=='terminal'
            and item['citation'].get('session_id')==terminal_saved['session_id']
            and item['citation'].get('task_id')==terminal_saved['task_id']
            and item['citation'].get('stream')=='stdout'),None)
        assert terminal_hit, terminal_index
        terminal_citation=terminal_hit['citation']
        assert terminal_citation['turn_id']==terminal_saved['turn_id']
        assert terminal_hit['excerpt']==terminal_saved['stdout'][terminal_citation['start']:terminal_citation['end']]
        print(json.dumps({'approved_saved_terminal_rag_common_chat':True,'task_id':terminal_saved['task_id'],'stream':'stdout'}))
        utility('run_code', {'language':'javascript','code':'console.log(6*7)','cwd':str(fixture)}, True)
        # Real Python/Node descendants must be gone after foreground timeout.
        for runtime in ['python', 'node']:
            executable = next(i['path'] for i in inventory if i['id'] == runtime and i['available'])
            ready = fixture / (runtime + '-child-ready.txt')
            late = fixture / (runtime + '-orphan.txt')
            ready.unlink(missing_ok=True); late.unlink(missing_ok=True)
            script = fixture / ('lifecycle-' + runtime + ('.py' if runtime == 'python' else '.js'))
            if runtime == 'python':
                child = f"import pathlib,time;pathlib.Path({str(ready)!r}).write_text('ready');time.sleep(4);pathlib.Path({str(late)!r}).write_text('orphan')"
                code = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}],creationflags=0x08000000 if sys.platform=='win32' else 0);time.sleep(30)"
            else:
                child = "const fs=require('fs');fs.writeFileSync(" + json.dumps(str(ready)) + ",'ready');setTimeout(()=>fs.writeFileSync(" + json.dumps(str(late)) + ",'orphan'),4000);"
                code = "require('child_process').spawn(process.execPath,['-e'," + json.dumps(child) + "],{windowsHide:true});setInterval(()=>{},1000);"
            script.write_text(code, encoding='utf8')
            command = "& '" + str(executable).replace("'", "''") + "' '" + str(script).replace("'", "''") + "'"
            utility('bash_run', {'command':command,'timeout_secs':2}, True)
            assert ready.exists(), runtime + ' child did not start'
            page.wait_for_timeout(4500)
            assert not late.exists(), runtime + ' child survived timeout'
        print(json.dumps({'real_python_node_tree_cleanup': True}))
        # Stop must cancel live work promptly, not just hide it until timeout.
        import psutil
        npm_manifest = root / '.cache/qa-npm/manifest.json'
        npm_cli = os.environ.get('JARVIS_QA_NPM_CLI') or (json.loads(npm_manifest.read_text(encoding='utf8')).get('cli') if npm_manifest.is_file() else None)
        stop_cases = [('python','bash_run'),('python','run_code'),('node','utility_run')] + ([('npm','bash_run')] if npm_cli else [])
        for runtime, tool_name in stop_cases:
            case = fixture / ('stop-' + runtime + '-' + uuid.uuid4().hex)
            case.mkdir()
            ready = case / 'child.pid'; parent_pid = case / 'parent.pid'; late = case / 'late.txt'
            executable = next(i['path'] for i in inventory if i['id'] == ('python' if runtime == 'python' else 'node') and i['available'])
            if runtime == 'python':
                child = f"import os,pathlib,time;pathlib.Path({str(ready)!r}).write_text(str(os.getpid()));time.sleep(4);pathlib.Path({str(late)!r}).write_text('orphan');time.sleep(30)"
                code = f"import os,pathlib,subprocess,sys,time;pathlib.Path({str(parent_pid)!r}).write_text(str(os.getpid()));subprocess.Popen([sys.executable,'-c',{child!r}],creationflags=0x08000000 if sys.platform=='win32' else 0);time.sleep(30)"
                script = case / 'parent.py'; script.write_text(code,encoding='utf8')
                argv = [executable, str(script)]
            else:
                child = "const fs=require('fs');fs.writeFileSync("+json.dumps(str(ready))+",String(process.pid));setTimeout(()=>fs.writeFileSync("+json.dumps(str(late))+",'orphan'),4000);setInterval(()=>{},1000);"
                code = "require('fs').writeFileSync("+json.dumps(str(parent_pid))+",String(process.pid));require('child_process').spawn(process.execPath,['-e',"+json.dumps(child)+"],{stdio:'inherit',windowsHide:true});setInterval(()=>{},1000);"
                script = case / 'parent.cjs'; script.write_text(code,encoding='utf8')
                if runtime == 'npm':
                    assert Path(npm_cli).is_file(), 'Explicit npm fixture is missing'
                    (case/'package.json').write_text(json.dumps({'name':'jarvis-stop-qa','version':'1.0.0','private':True,'scripts':{'hold':'node parent.cjs'}}),encoding='utf8')
                    (case/'empty.npmrc').write_text('',encoding='utf8')
                    argv = [executable,npm_cli,'--prefix',str(case),'--userconfig',str(case/'empty.npmrc'),'--globalconfig',str(case/'global.npmrc'),'--offline','--no-audit','--no-fund','--no-update-notifier','run','hold']
                else:
                    argv = [executable,str(script)]
            command = '& ' + ' '.join("'"+str(arg).replace("'","''")+"'" for arg in argv)
            owned = []
            try:
                args = {'command':command,'timeout_secs':60}
                if tool_name == 'run_code': args = {'language':'python','code':code,'cwd':str(case),'timeout_secs':60}
                if tool_name == 'utility_run': args = {'tool':'node','args':[str(script)],'cwd':str(case),'timeout_secs':60}
                request('STOP_PROCESS_QA:' + json.dumps({'name':tool_name,'args':args}))
                approve.first.wait_for(timeout=15000); approve.first.click()
                deadline = time.monotonic() + 12
                while not ready.exists():
                    assert time.monotonic() < deadline, runtime + ' child did not start'
                    page.wait_for_timeout(100)
                parent = psutil.Process(int(parent_pid.read_text()))
                lineage = parent.parents()
                assert process.pid in [p.pid for p in lineage], 'Fixture is not owned by QA Jarvis'
                owned = [parent, psutil.Process(int(ready.read_text()))]
                for ancestor in lineage:
                    if ancestor.pid == process.pid: break
                    owned.append(ancestor)
                frame.get_by_role('button',name='Stop',exact=True).click()
                deadline = time.monotonic() + 3
                while any(p.is_running() for p in owned):
                    assert time.monotonic() < deadline, runtime + ' process tree survived Stop'
                    page.wait_for_timeout(100)
                page.wait_for_timeout(4500)
                assert not late.exists(), runtime + ' child performed work after Stop'
                frame.get_by_role('button',name='Send',exact=True).wait_for()
                request('Reply JARVIS_SHARED_READY after process cancellation.')
                assert 'JARVIS_SHARED_READY' in result()['reply']
            finally:
                # Only fixture processes whose ancestry and creation identity were captured.
                for child_process in owned:
                    try:
                        if child_process.is_running(): child_process.kill()
                    except psutil.NoSuchProcess: pass
            print(json.dumps({'stop_process_tree':runtime,'tool':tool_name,'owned_pids_gone':True,'no_late_write':True}))

        request('RECOVER_TOOL_RESULT_QA')
        assert 'RECOVER_TOOL_RESULT_QA_OK' in result()['reply']
        archive = fixture / ('utility-' + uuid.uuid4().hex + '.zip')
        extracted = fixture / ('extracted-' + uuid.uuid4().hex)
        def no_archive(): assert not archive.exists()
        def no_extracted(): assert not extracted.exists()
        utility('archive_create', {'sources':[str(fixture/'read.txt')],'destination':str(archive)}, True, no_archive)
        assert archive.is_file()
        utility('archive_list', {'path':str(archive)})
        utility('archive_extract', {'path':str(archive),'destination':str(extracted)}, True, no_extracted)
        assert (extracted/'read.txt').read_bytes() == (fixture/'read.txt').read_bytes()
        suggestion_items = [{"label": "SUGGESTIONS_SERVICE_QA", "prompt": "Show capabilities"}]
        with preserve_qa_suggestions(root, suggestion_items):
            request('Use jarvis_action suggestions.save with items [["SUGGESTIONS_SERVICE_QA","Show capabilities"]]. Wait for approval.')
            approve.first.wait_for(timeout=120000)
            approve.first.click()
            assert 'SUGGESTIONS_SERVICE_QA' in result()['reply']
            frame.get_by_role('button',name='SUGGESTIONS_SERVICE_QA',exact=True).wait_for()
        page.evaluate("()=>window.dispatchEvent(new Event('jarvis-suggestions'))")
        request('Call data_query source suggestions limit 2 and report SHARED_DATA_READY')
        assert 'SHARED_DATA_READY' in result()['reply']
        request('Call jarvis_catalog query translation.run')
        assert 'translation.run' in result()['reply']
        for width,height in [(2560,1440),(1920,1080),(1440,900),(1024,768),(800,600)]:
            page.set_viewport_size({'width':width,'height':height})
            page.wait_for_timeout(150)
            box=frame.locator('[data-shared-composer]').bounding_box()
            assert box and box['y']+box['height']<=height+1,(width,height,box)
            assert frame.locator('[data-unified-chat]').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), (width,height,frame.locator('[data-unified-chat]').evaluate('(e)=>({width:e.clientWidth,scroll:e.scrollWidth,wide:[...e.querySelectorAll("*")].filter(n=>n.getBoundingClientRect().right>e.getBoundingClientRect().right+1).slice(0,8).map(n=>({tag:n.tagName,cls:n.className,width:n.getBoundingClientRect().width}))})'))
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_role('button',name='Toggle Light and Dark Theme',exact=True).click()
        for _ in range(50):
            if frame.locator('html').get_attribute('class')=='light':break
            page.wait_for_timeout(100)
        assert 'dark' not in (frame.locator('html').get_attribute('class') or '')
        frame.get_by_role('button',name='Settings',exact=True).click()
        light_settings=page.frame_locator('iframe[src*="workbench-settings.html"]')
        light_settings.get_by_role('heading',name='Models',exact=True).wait_for()
        assert page.locator('.ecosystem-content').evaluate('(e)=>e.inert')
        page.screenshot(path=str(root/'.cache/settings-polish-light.png'))
        menu.get_by_role('button',name='Documents',exact=True).click()
        page.screenshot(path=str(root/'.cache/ui-audit-documents-light.png'))
        menu.get_by_label('Local embedding model',exact=True).wait_for()
        assert menu.get_by_label('Local embedding model',exact=True).locator('option[value="default"]').count() == 1
        chunk_before = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'embedding.settings',params:{}})")
        chunk_size = menu.get_by_label('Chunk size (characters)',exact=True)
        chunk_overlap = menu.get_by_label('Chunk overlap (characters)',exact=True)
        chunk_save = menu.get_by_role('button',name='Save chunk settings',exact=True)
        try:
            chunk_size.fill('512')
            chunk_overlap.fill('512')
            chunk_save.click()
            menu.get_by_role('status').filter(has_text='Enter a chunk size from 256 to 8192').wait_for()
            invalid_state = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'embedding.settings',params:{}})")
            assert invalid_state == chunk_before
            chunk_overlap.fill('64')
            chunk_save.click()
            menu.get_by_role('status').filter(has_text='Chunk settings saved.').wait_for()
            saved_chunks = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'embedding.settings',params:{}})")
            assert saved_chunks['chunk_size'] == 512 and saved_chunks['chunk_overlap'] == 64
            menu.get_by_role('button',name='General',exact=True).click()
            menu.get_by_role('button',name='Documents',exact=True).click()
            expect(chunk_size).to_have_value('512')
            expect(chunk_overlap).to_have_value('64')
        finally:
            page.evaluate("value=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'embedding.settings',params:{chunk_size:value.chunk_size,chunk_overlap:value.chunk_overlap}})",chunk_before)
            menu.get_by_role('button',name='General',exact=True).click()
            menu.get_by_role('button',name='Documents',exact=True).click()
        print(json.dumps({'shared_chunk_settings_persistent':True,'invalid_chunk_settings_do_not_mutate':True}))
        rebuild_button = menu.get_by_role('button',name='Rebuild document indexes',exact=True)
        rebuild_button.click()
        rebuild_confirmation = menu.get_by_role('group',name='Confirm index rebuild',exact=True)
        rebuild_confirmation.wait_for()
        rebuild_confirmation.get_by_role('button',name='Cancel',exact=True).click()
        rebuild_confirmation.wait_for(state='hidden')
        assert rebuild_button.is_enabled()
        if '--bundled-assets' in sys.argv:
            # An absent exact ID proves native allowlist/service routing without
            # processing any real indexed documents or invoking a provider.
            rebuild_probe = page.evaluate("identifier=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.rebuild',params:{identifier,backend:'cpu'}})", 'qa-no-document-' + uuid.uuid4().hex)
            assert rebuild_probe == {'rebuilt': [], 'failed': []}, rebuild_probe
        print(json.dumps({'document_rebuild_confirmation_cancel':True,'document_rebuild_empty_native_route':'--bundled-assets' in sys.argv}))
        if '--bundled-assets' in sys.argv:
            cancel_id = uuid.uuid4().hex
            cancelled = page.evaluate("id=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'embedding.cancel',params:{request_id:id}})",cancel_id)
            assert cancelled['ok'] is True
            early_result = page.evaluate("id=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'documents.rebuild',params:{request_id:id,backend:'cpu'}}).then(()=>({unexpected:true}),error=>({error:String(error)}))",cancel_id)
            assert 'Embedding operation cancelled' in early_result.get('error',''), early_result
            print(json.dumps({'rebuild_cancel_native_route':True,'early_cancel_prevents_provider':True}))
        menu.get_by_role('button',name='Speech & Voice',exact=True).click()
        synthesis = menu.get_by_label('TTS synthesis model',exact=True)
        synthesis.wait_for()
        synthesis.select_option('supertonic')
        speaker = menu.get_by_label('Speaker voice',exact=True)
        assert speaker.locator('option').count() == 10
        menu.get_by_label('Speech language',exact=True).select_option('en')
        assert 'Hello' in menu.get_by_label('Voice preview text',exact=True).input_value()
        menu.get_by_label('Speech language',exact=True).select_option('uk')
        assert menu.get_by_label('Voice preview text',exact=True).input_value()==''
        assert menu.get_by_role('button',name='Listen',exact=True).is_disabled()
        menu.get_by_label('Speech language',exact=True).select_option('en')
        menu.get_by_label('Automatic speech speed',exact=True).uncheck()
        menu.get_by_label('Speech speed multiplier',exact=True).fill('1.15')
        menu.get_by_label('Inference steps',exact=True).fill('10')
        menu.get_by_role('button',name='Save',exact=True).click()
        controls_deadline=time.monotonic()+5
        while True:
            saved_controls=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'services.read',params:{name:'tts.controls',args:{}}})")['values']
            if saved_controls['language']=='en' and saved_controls['speed']==1.15 and saved_controls['inference_steps']==10:break
            assert time.monotonic()<controls_deadline,saved_controls
            page.wait_for_timeout(100)
        synthesis.select_option('gemini:lite')
        assert menu.get_by_label('Speech speed multiplier',exact=True).count()==0
        assert menu.get_by_label('Speaker voice',exact=True).input_value() == 'Kore'
        resources = menu.get_by_role('region',name='TTS models and voices',exact=True)
        assert resources.get_by_role('link',name='Browse audio.cpp model packages',exact=True).get_attribute('href') == 'https://huggingface.co/audio-cpp/audio.cpp-gguf'
        assert resources.get_by_role('link',name='Supertonic voices and model card',exact=True).get_attribute('href') == 'https://huggingface.co/Supertone/supertonic-3'
        resources.get_by_role('button',name='Manage TTS downloads',exact=True).click()
        tts_library = page.frame_locator('iframe[src*="workbench-settings.html"]').locator('[data-model-library]')
        tts_library.wait_for()
        assert tts_library.get_attribute('open') is not None
        assert tts_library.get_by_label('Kind',exact=True).input_value() == 'tts'
        assert tts_library.evaluate('(element)=>element === document.activeElement')
        tts_library.get_by_label('Activation device',exact=True).select_option('cpu')
        tts_library.locator(f'[data-installed-model="tts:qa/supertonic:{tts_revision}"]').get_by_role('button',name='Activate',exact=True).click()
        tts_library.get_by_role('button',name='Activating…',exact=True).wait_for(state='hidden')
        activation_text=tts_library.get_by_role('status').inner_text()
        assert 'TTS weights selected.' in activation_text,activation_text
        tts_state=page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'services.read',params:{name:'tts.model.status',args:{}}})")
        assert tts_state['repo_id']=='qa/supertonic' and tts_state['revision']==tts_revision and tts_state['mode']=='cpu'
        menu.get_by_role('button',name='Speech & Voice',exact=True).click()
        menu.get_by_label('Selected Supertonic weights',exact=True).filter(has_text='qa/supertonic').wait_for()
        print(json.dumps({'tts_resources_and_direct_library_navigation':True,'tts_library_lazy_activation_shared_status':True}))
        resources=menu.get_by_role('region',name='TTS models and voices',exact=True)
        resources.get_by_role('button',name='Manage TTS downloads',exact=True).click()
        piper_library=page.frame_locator('iframe[src*="workbench-settings.html"]').locator('[data-model-library]')
        piper_row=piper_library.locator(f'[data-installed-model="tts:qa/piper:{piper_revision}"]')
        piper_library.get_by_label('Activation device',exact=True).select_option('vulkan')
        assert piper_row.get_by_role('button',name='Activate',exact=True).is_disabled()
        assert 'CPU' in piper_row.inner_text()
        piper_library.get_by_label('Activation device',exact=True).select_option('cpu')
        piper_row.get_by_role('button',name='Activate',exact=True).click()
        piper_library.get_by_role('button',name='Activating…',exact=True).wait_for(state='hidden')
        assert 'Piper weights selected.' in piper_library.get_by_role('status').inner_text()
        menu.get_by_role('button',name='Speech & Voice',exact=True).click()
        menu.get_by_label('Selected Piper weights',exact=True).filter(has_text='qa/piper').wait_for()
        menu.get_by_label('TTS synthesis model',exact=True).select_option('piper')
        piper_speakers=menu.get_by_label('Speaker voice',exact=True)
        piper_speakers.locator('option').filter(has_text='Bob').wait_for(state='attached')
        bob=piper_speakers.locator('option').filter(has_text='Bob').get_attribute('value')
        assert bob.startswith('piper:managed:')
        piper_speakers.select_option(bob)
        menu.get_by_role('button',name='Save',exact=True).click()
        piper_saved=page.evaluate("""async voice=>{const deadline=performance.now()+5000;while(performance.now()<deadline){const value=await window.__JARVIS_NATIVE__.core.invoke('backend',{method:'snapshot',params:{}});if(value.voice===voice)return value;await new Promise(resolve=>setTimeout(resolve,25));}throw new Error('Piper voice save did not settle');}""",bob)
        assert piper_saved['voice']==bob,piper_saved['voice']
        assert any(voice['id']==bob for voice in piper_saved['voices'])
        print(json.dumps({'managed_piper_cpu_activation_native_ui':True,'shared_speaker_selection':True,'no_synthesis':True}))

        menu.get_by_role('button',name='Integrations',exact=True).click()
        integrations = page.frame_locator('iframe[src*="workbench-settings.html"]')
        integrations.get_by_role('heading',name='Integrations',exact=True).wait_for()
        saved_mcp = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('mcp_config_get')")
        try:
            integrations.get_by_role('button',name='Add MCP server',exact=True).click()
            integrations.get_by_label('Name',exact=True).fill('OFFLINE_UI_DISABLED')
            integrations.get_by_label('Executable',exact=True).fill('python')
            integrations.get_by_label('Arguments (JSON array)',exact=True).fill('[1]')
            integrations.get_by_role('button',name='Save',exact=True).click()
            integrations.get_by_role('alert').filter(has_text='Arguments must be a JSON array of strings').wait_for()
            integrations.get_by_label('Arguments (JSON array)',exact=True).fill('[]')
            integrations.get_by_role('button',name='Save',exact=True).click()
            integrations.get_by_role('heading',name='OFFLINE_UI_DISABLED',exact=True).wait_for()
            card = integrations.locator('article').filter(has_text='OFFLINE_UI_DISABLED')
            assert card.get_by_role('button',name='Connect / Discover',exact=True).is_disabled()
            current = page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('mcp_config_get')")
            assert not next(item for item in current['servers'] if item.get('name') == 'OFFLINE_UI_DISABLED')['enabled']
        finally:
            page.evaluate("config=>window.__JARVIS_NATIVE__.core.invoke('mcp_config_save',{config})",saved_mcp)
        print(json.dumps({'shared_mcp_settings_validation':True,'disabled_server_not_started':True,'separate_tts_model_voice':True,'embedding_model_selector':True}))
        from qa_mcp_settings import McpSettingsQA
        mcp_qa = McpSettingsQA(root, page, integrations, process.pid)
        mcp_qa.run(request, result, frame)
        menu.get_by_role('button',name='Integrations',exact=True).click()
        lifecycle_settings = page.frame_locator('iframe[src*="workbench-settings.html"]')
        # Earlier discovery coverage deliberately selected the unloaded Ollama model.
        # Select the loaded fixture before testing unload and snapshot its preferences.
        ollama_card = lifecycle_settings.locator('[data-provider="ollama"]')
        ollama_card.get_by_role('combobox',name='Ollama detected models',exact=True).click()
        lifecycle_settings.get_by_role('option',name='qa-loaded',exact=True).click()
        wait_native_preferences(page, {'ollamaModelId': 'qa-loaded'})
        prefs_before_unload = page.evaluate("async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});const result={};for(const key of ['defaultModelId','autocompleteModelId','autocompleteProvider','lmstudioModelId','ollamaModelId'])result[key]=(await store.get(key))??null;return result;}")
        for provider in ['lmstudio', 'ollama']:
            card = lifecycle_settings.locator('[data-provider="'+provider+'"]')
            unload_button = card.get_by_role('button',name='Unload',exact=True)
            unload_button.wait_for()
            assert unload_button.is_enabled(), provider
            unload_button.click()
            confirmation = page.get_by_role('dialog',name='Jarvis',exact=True)
            confirmation.get_by_role('button',name='Cancel',exact=True).click()
            for _ in range(100):
                if unload_button.is_enabled():
                    break
                page.wait_for_timeout(50)
            assert unload_button.is_enabled(), provider
            unload_button.click()
            confirmation.get_by_role('button',name='Confirm',exact=True).click()
            for _ in range(100):
                if unload_button.is_disabled() and 'Downloaded · loads on first use' in card.inner_text():
                    break
                page.wait_for_timeout(50)
            assert unload_button.is_disabled(), provider
            assert 'Downloaded · loads on first use' in card.inner_text(), provider
        prefs_after_unload = page.evaluate("async()=>{const store=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});const result={};for(const key of ['defaultModelId','autocompleteModelId','autocompleteProvider','lmstudioModelId','ollamaModelId'])result[key]=(await store.get(key))??null;return result;}")
        assert prefs_after_unload == prefs_before_unload
        print(json.dumps({'shared_local_model_unload':True, 'cancel_prevents_unload':True, 'model_selections_preserved':True}))
        advanced=menu.locator('details.settings-advanced')
        if advanced.get_attribute('open') is None: advanced.locator('summary').click()
        menu.get_by_role('button',name='Utilities',exact=True).click()
        menu.get_by_role('region',name='Utilities and runtimes',exact=True).locator('[data-utility="python"]').wait_for()
        page.screenshot(path=str(root/'.cache/utilities-light.png'))
        menu.get_by_role('button',name='Documents',exact=True).click()
        translation = menu.get_by_role('region',name='Text and Document Translation',exact=True)
        mode = translation.get_by_label('Translation Mode',exact=True)
        previous_mode = mode.input_value()
        try:
            mode.select_option('auto')
            translation.get_by_label('Source Translation Language',exact=True).select_option('ja')
            mode.select_option('local')
            from playwright.sync_api import expect
            expect(translation.get_by_label('Source Translation Language',exact=True)).to_have_value('auto')
            expect(translation.locator('option[value="ja"]').first).to_have_attribute('disabled','')
        finally:
            mode.select_option(previous_mode)
        menu.get_by_role('button',name='Models & Providers',exact=True).click()
        light_settings.get_by_role('heading',name='Models',exact=True).wait_for()
        light_settings.locator('body').press('Escape')
        page.get_by_role('dialog',name='Settings',exact=True).wait_for(state='hidden')
        assert not page.locator('.ecosystem-content').evaluate('(e)=>e.inert')
        page.get_by_role('button',name='Settings',exact=True).first.click()
        menu.get_by_role('button',name='API Usage',exact=True).click()
        usage=menu.get_by_role('region',name='Conversation usage',exact=True)
        usage.get_by_role('button',name='Refresh counters',exact=True).first.click()
        usage.locator('tbody tr').first.wait_for()
        page.screenshot(path=str(root/'.cache/menus-usage.png'))
        menu.get_by_role('button',name='Close Settings',exact=True).click()
        select_language(page, 'ru')
        page.get_by_role('button',name='Чат',exact=True).wait_for()
        page.wait_for_function("document.title === 'Jarvis — голосовой помощник'")
        frame.get_by_role('button',name='Прикрепить файлы',exact=True).wait_for()
        page.reload()
        page.get_by_role('button',name='Чат',exact=True).wait_for()
        page.wait_for_function("document.title === 'Jarvis — голосовой помощник'")
        frame.get_by_role('button',name='Прикрепить файлы',exact=True).wait_for()
        frame.get_by_text('translation.run requires approval',exact=True).first.wait_for()
        print(json.dumps({'russian_locale_survives_reload':True,'saved_chat_survives_locale_reload':True}))
        select_language(page, 'en')
        page.wait_for_function("document.title === 'Jarvis — voice assistant'")
        page.reload()
        frame.locator('[data-unified-chat]').wait_for()
        frame.get_by_text('translation.run requires approval',exact=True).first.wait_for()
        assert 'Input: 0' not in frame.get_by_label('Session tokens',exact=True).inner_text()
        stored=page.evaluate("async()=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return s.get('defaultModelId');}")
        assert stored=='lmstudio-local'

        page.screenshot(path=str(root/'.cache/ecosystem-main.png'))
        assert page.evaluate("()=>window.__JARVIS_NATIVE__.core.invoke('backend',{method:'api.usage',params:{}})")["rows"]==cloud_before,"QA must not consume cloud API calls"
        assert not errors,errors
        print(json.dumps({'one_chat_two_views':True,'common_settings':True,'main_usage':True,'native_attachment_copy':True,'shared_tools_and_approval':True,'responsive_sizes':5,'page_errors':errors}))
        page.evaluate("window.__JARVIS_NATIVE__.core.invoke('pty_close_all')")
        try:page.evaluate("window.__JARVIS_NATIVE__.core.invoke('plugin:window|close')")
        except PlaywrightError:pass
        browser.close()
    process.wait(timeout=20)
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
    if original_tts_controls is None:tts_controls_path.unlink(missing_ok=True)
    else:tts_controls_path.write_bytes(original_tts_controls)
    if original_qa_voice is None:qa_voice_path.unlink(missing_ok=True)
    else:qa_voice_path.write_bytes(original_qa_voice)
    if original_tts_selection is None:tts_selection_path.unlink(missing_ok=True)
    else:tts_selection_path.write_bytes(original_tts_selection)
    if original_piper_selection is None:piper_selection_path.unlink(missing_ok=True)
    else:piper_selection_path.write_bytes(original_piper_selection)
    for name in ['voice.onnx','voice.onnx.json','manifest.json']:(piper_dir/name).unlink(missing_ok=True)
    piper_dir.rmdir()
    (tts_dir/'supertonic-qa.gguf').unlink(missing_ok=True)
    (tts_dir/'manifest.json').unlink(missing_ok=True)
    tts_dir.rmdir()
    (managed_fixture['directory']/'chat-qa.gguf').unlink(missing_ok=True)
    (managed_fixture['directory']/'manifest.json').unlink(missing_ok=True)
    managed_fixture['directory'].rmdir()
    if portable_cwd is not None:
        # Only this newly created QA directory is removed. Resolve and check the
        # exact temporary parent/name before recursive cleanup on Windows.
        cleanup_cwd=portable_cwd.resolve(strict=True)
        assert cleanup_cwd.parent==Path(tempfile.gettempdir()).resolve() and cleanup_cwd.name.startswith('jarvis-portable-cwd-')
        shutil.rmtree(cleanup_cwd)
    log.close()
    if mcp_qa is not None:
        mcp_qa.after_exit()
