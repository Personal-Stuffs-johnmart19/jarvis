"""Native Jarvis workbench QA: own hidden WebView, workspace and API only."""

import json, os, subprocess, time, uuid, sys, socket
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, Error as PlaywrightError

sys.stdout.reconfigure(line_buffering=True)
root = Path(__file__).resolve().parents[2]
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    qa_port = listener.getsockname()[1]
qa_url = f"http://127.0.0.1:{qa_port}"

env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "WEBVIEW2_USER_DATA_FOLDER": str(
        root / ".cache/desktop-test" / ("workbench-" + uuid.uuid4().hex)
    ),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={qa_port} --remote-debugging-address=127.0.0.1",
}
fixture = root / ".cache/desktop-test/native-workspace"
fixture.mkdir(parents=True, exist_ok=True)
(fixture / "example.py").write_text('print("JARVIS_NATIVE_READ")\n', encoding="utf8")
log = (root / ".cache/workbench-native-qa.log").open("w", encoding="utf8")
process = subprocess.Popen(
    [
        str(
            root
            / "desktop-bin"
            / json.loads(
                (root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig")
            )["file"]
        )
    ],
    cwd=root,
    env=env,
    stdout=log,
    stderr=log,
    creationflags=0x08000000,
)
errors = []
try:
    for _ in range(150):
        assert process.poll() is None, "Jarvis exited"
        try:
            if (
                httpx.get(
                    qa_url + "/json/version", trust_env=False, timeout=0.2
                ).status_code
                == 200
            ):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(qa_url)
        page = None
        for _ in range(150):
            page = next(
                (
                    p
                    for c in browser.contexts
                    for p in c.pages
                    if "tauri.localhost" in p.url
                ),
                None,
            )
            if page is not None:
                break
            time.sleep(0.1)
        assert page is not None, "QA WebView did not navigate"
        # Serve current compiled local assets in our own QA page only, without a native rebuild.
        from urllib.parse import urlsplit, unquote

        def asset(route):
            path = (
                root
                / "apps/desktop/dist"
                / unquote(urlsplit(route.request.url).path).lstrip("/")
            ).resolve()
            base = (root / "apps/desktop/dist").resolve()
            if path == base:
                path = base / "index.html"
            if path.is_relative_to(base) and path.is_file():
                route.fulfill(path=str(path))
            else:
                route.continue_()

        if "--bundled-assets" not in sys.argv:
            page.route("http://tauri.localhost/**", asset)
            page.reload()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: (
                errors.append(message.type + ": " + message.text)
                if message.type == "error"
                else None
            ),
        )
        page.get_by_role("button", name="Рабочее пространство", exact=True).click()
        frame = page.frame_locator('iframe[title="Файлы, редактор, терминалы и AI"]')
        frame.locator("body").get_by_text("Files", exact=True).or_(
            frame.locator("body").get_by_text("Файлы", exact=True)
        ).first.wait_for(timeout=15000)
        own = next(f for f in page.frames if f.url.endswith("workbench.html"))
        result = own.evaluate(
            "async()=>({cwd:await window.__TAURI_INTERNALS__.invoke('workspace_current_dir'),bridge:!!(await window.__TAURI_INTERNALS__.invoke('backend',{method:'workbench.bootstrap',params:{}})).base})"
        )
        print(json.dumps({"native": result}, ensure_ascii=False))
        frame.get_by_title("New tab", exact=True).click()
        frame.get_by_role("menuitem", name="Terminal", exact=False).first.click()
        frame.locator("canvas").first.wait_for(state="attached", timeout=20000)
        diagnostics = own.evaluate(
            "window.__TAURI_INTERNALS__.invoke('pty_diagnostics')"
        )
        assert diagnostics["sessions"], diagnostics
        print(
            json.dumps(
                {"pty_sessions": len(diagnostics["sessions"]), "terminal_canvas": True}
            )
        )
        page.screenshot(path=str(root / ".cache/workbench-integrated.png"))
        if "--ai" in sys.argv:
            # The actual shared agent performs a file read, an approved terminal action and edit.
            frame.get_by_title("Open AI agent", exact=True).or_(
                frame.get_by_title("Открыть AI-агента", exact=True)
            ).click()
            session_picker = frame.get_by_title("Switch session", exact=True)
            if session_picker.count():
                session_picker.click()
                frame.get_by_role("menuitem", name="New session", exact=True).click()
            composer = frame.get_by_placeholder("Ask Terax anything", exact=False)
            composer.fill(
                "Прочитай example.py инструментом read_file. Ответь строкой из print. Не запускай команды и не меняй файлы."
            )
            composer.press("Enter")
            try:
                frame.get_by_role("button", name="Send", exact=True).wait_for(
                    timeout=180000
                )
            except PlaywrightError:
                print(
                    json.dumps(
                        {"ai_diagnostic": own.locator("body").inner_text()[-4000:]},
                        ensure_ascii=False,
                    )
                )
                raise
            frame.get_by_text("JARVIS_NATIVE_READ", exact=True).last.wait_for(
                timeout=180000
            )
            composer.fill(
                "Выполни через bash PowerShell команду: Write-Output ('JARVIS_'+'NATIVE_SHELL'). Дождись моего подтверждения. Файлы не меняй."
            )
            composer.press("Enter")
            approval = frame.get_by_role("button", name="Approve", exact=True)
            approval.wait_for(timeout=180000)
            assert "JARVIS_NATIVE_SHELL" not in frame.locator("body").inner_text()
            approval.click()
            frame.get_by_text("JARVIS_NATIVE_SHELL", exact=True).last.wait_for(
                timeout=180000
            )
            frame.get_by_role("button", name="Send", exact=True).wait_for(timeout=180000)
            composer.fill(
                "Через edit замени JARVIS_NATIVE_READ на JARVIS_NATIVE_EDIT в example.py. Дождись подтверждения. Другие файлы не меняй."
            )
            composer.press("Enter")
            approval.wait_for(timeout=180000)
            assert "JARVIS_NATIVE_EDIT" not in (fixture / "example.py").read_text()
            approval.click()
            for _ in range(200):
                if "JARVIS_NATIVE_EDIT" in (fixture / "example.py").read_text():
                    break
                page.wait_for_timeout(100)
            assert "JARVIS_NATIVE_EDIT" in (fixture / "example.py").read_text()
            frame.get_by_role("button", name="Send", exact=True).wait_for(timeout=180000)
            print(
                json.dumps(
                    {
                        "agent_read": True,
                        "native_terminal_approval": True,
                        "native_edit_approval": True,
                        "file_saved": True,
                    }
                )
            )
        page.get_by_role("button", name="Модели", exact=True).click()
        settings = page.frame_locator('iframe[title="Модели"]')
        settings.get_by_text("Providers", exact=True).or_(
            settings.get_by_text("Провайдеры", exact=True)
        ).first.wait_for(timeout=20000)
        page.screenshot(path=str(root / ".cache/workbench-models.png"))
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        if page.locator("html").get_attribute("data-theme") != "light":
            page.get_by_role(
                "button", name="Переключить светлую и тёмную тему", exact=True
            ).click()
        page.locator('html[data-theme="light"]').wait_for()
        for width, height in [(640, 480), (1280, 720), (1920, 1080), (2560, 1440)]:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), (width, height)
            page.screenshot(path=str(root / f".cache/workbench-light-{width}.png"))
        page.get_by_role("button", name="Главная", exact=True).click()
        for width, height in [(640, 480), (1280, 720), (1920, 1080), (2560, 1440)]:
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), (width, height)
            page.screenshot(path=str(root / f".cache/jarvis-main-light-{width}.png"))
        page.get_by_role("button", name="Рабочее пространство", exact=True).click()
        page.get_by_role("button", name="Темы", exact=True).click()
        themes = page.frame_locator('iframe[title="Темы"]')
        themes.get_by_text("Jarvis Glass", exact=True).wait_for(timeout=15000)
        assert (
            themes.locator("html").evaluate("el=>getComputedStyle(el).colorScheme")
            == "light"
        )
        page.screenshot(path=str(root / ".cache/workbench-light-settings.png"))
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        for section in ["Агенты", "Шорткаты", "Редактор", "Модели"] * 2:
            page.get_by_role("button", name=section, exact=True).click()
            page.frame_locator('iframe[title="' + section + '"]').locator(
                "main"
            ).wait_for()
            page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        page.get_by_role(
            "button", name="Переключить светлую и тёмную тему", exact=True
        ).click()
        page.locator('html[data-theme="dark"]').wait_for()
        print(
            json.dumps({"light_layouts": 4, "settings_cycles": 8, "theme_sync": True})
        )
        page.get_by_role("button", name="Редактор", exact=True).click()
        editor_settings = page.frame_locator('iframe[title="Редактор"]')
        wrap = (
            editor_settings.get_by_text("Word wrap", exact=True)
            .locator("../..")
            .get_by_role("switch")
        )
        old_wrap = wrap.get_attribute("aria-checked")
        wrap.click()
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        page.get_by_role("button", name="Редактор", exact=True).click()
        wrap.wait_for()
        assert wrap.get_attribute("aria-checked") != old_wrap
        wrap.click()
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        page.get_by_role("button", name="Шорткаты", exact=True).click()
        shortcut_settings = page.frame_locator('iframe[title="Шорткаты"]')
        row = shortcut_settings.get_by_text("Open command palette", exact=True).locator(
            "../.."
        )
        row.locator(".cursor-pointer").click()
        shortcut_settings.locator("body").press("Control+Alt+y")
        row.get_by_title("Reset to default").wait_for()
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        page.get_by_role("button", name="Шорткаты", exact=True).click()
        row.get_by_title("Reset to default").wait_for()
        assert "Y" in row.inner_text().upper()
        row.get_by_title("Reset to default").click()
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        print(
            json.dumps(
                {
                    "editor_preferences_persist": True,
                    "shortcut_edit_persist_reset": True,
                }
            )
        )
        page.get_by_role("button", name="Агенты", exact=True).click()
        agents = page.frame_locator('iframe[title="Агенты"]')
        previous_agent = root / ".cache/desktop-test/last-workbench-agent.txt"
        if previous_agent.exists():
            agents.get_by_text(previous_agent.read_text(), exact=True).wait_for(
                timeout=15000
            )
            print(json.dumps({"agent_persisted_across_restart": True}))
        agents.get_by_role("button", name="New agent", exact=True).click()
        dialog = agents.get_by_role("dialog")
        agent_name = "QA Agent " + uuid.uuid4().hex[:8]
        dialog.get_by_placeholder("e.g. Test Engineer").fill(agent_name)
        dialog.locator("textarea").fill(
            "Read only the selected workspace. Ask before editing."
        )
        dialog.get_by_role("button", name="Save", exact=True).click()
        agents.get_by_text(agent_name, exact=True).wait_for()
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        page.get_by_role("button", name="Агенты", exact=True).click()
        agents.get_by_text(agent_name, exact=True).wait_for(timeout=15000)
        previous_agent.write_text(agent_name)
        print(json.dumps({"custom_agent_saved_and_reloaded": True}))
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        close_chat = frame.get_by_role("button", name="Close", exact=True)
        if close_chat.count() and close_chat.first.is_visible():
            close_chat.first.click()
        frame.get_by_text("example.py", exact=True).first.dblclick()
        frame.locator(".cm-content").filter(has_text="JARVIS_NATIVE_").first.wait_for(
            timeout=15000
        )
        print(json.dumps({"native_editor_open": True}))
        page.get_by_role("button", name="Модели", exact=True).click()
        assert not errors, errors
        print(json.dumps({"models": True, "page_errors": errors}, ensure_ascii=False))
        page.get_by_role("button", name="Закрыть настройки", exact=True).click()
        assert process.poll() is None
        page.evaluate("window.__TAURI_INTERNALS__.invoke('pty_close_all')")
        try:
            page.evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|close')")
        except PlaywrightError:
            pass
        browser.close()
    process.wait(timeout=20)
    assert process.returncode == 0, process.returncode
except Exception:
    print(json.dumps({"errors": errors}, ensure_ascii=False))
    try:
        print(
            json.dumps(
                {
                    "errors": errors,
                    "frames": [
                        (f.url, f.locator("body").inner_text()[:1200])
                        for f in page.frames
                    ],
                },
                ensure_ascii=False,
            )
        )
        page.screenshot(path=str(root / ".cache/workbench-error.png"))
    except Exception:
        pass
    raise
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    log.close()
