"""Own hidden Jarvis WebView + synthetic local model API; no Computer Use."""

import json, os, socket, subprocess, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, Error as PlaywrightError

sys.stdout.reconfigure(line_buffering=True)
root = Path(__file__).resolve().parents[2]
mode = {"status": 200}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps(
            {"data": [{"id": "qa-local-one"}, {"id": "qa-local-two"}]}
        ).encode()
        self.send_response(mode["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}/v1"
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
url = f"http://127.0.0.1:{port}"
env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "WEBVIEW2_USER_DATA_FOLDER": str(
        root / ".cache/desktop-test" / ("connections-" + uuid.uuid4().hex)
    ),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port} --remote-debugging-address=127.0.0.1",
}
manifest = json.loads(
    (root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig")
)
log = (root / ".cache/connections-native-qa.log").open("w", encoding="utf8")
process = subprocess.Popen(
    [str(root / "desktop-bin" / manifest["file"])],
    cwd=root,
    env=env,
    stdout=log,
    stderr=log,
    creationflags=0x08000000,
)
errors = []
try:
    for _ in range(150):
        assert process.poll() is None
        try:
            if httpx.get(
                url + "/json/version", trust_env=False, timeout=0.2
            ).is_success:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(url)
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
            if page:
                break
            time.sleep(0.1)
        assert page is not None
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on(
            "console", lambda m: errors.append(m.text) if m.type == "error" else None
        )
        page.get_by_role("button", name="Рабочее пространство", exact=True).click()
        workspace = page.frame_locator(
            'iframe[title="Файлы, редактор, терминалы и AI"]'
        )
        workspace.get_by_title("Locations and environments").click()
        for label in [
            "Drive C:",
            "Drive D:",
            "Home",
            "Open path…",
            "Local environment",
        ]:
            workspace.get_by_role("menuitem", name=label, exact=False).first.wait_for()
        workspace.get_by_role("menuitem", name="Open path…", exact=True).click()
        fixture = root / ".cache/desktop-test/connections-workspace"
        fixture.mkdir(parents=True, exist_ok=True)
        workspace.get_by_role("textbox", name="Location path").fill(str(fixture))
        workspace.get_by_role("button", name="Open location", exact=True).click()
        time.sleep(1)
        print("location alerts:", workspace.get_by_role("alert").all_text_contents())
        workspace.get_by_role("dialog", name="Open location").wait_for(state="hidden")
        workspace.get_by_text("connections-workspace", exact=True).first.wait_for(
            timeout=15000
        )
        print(
            json.dumps(
                {"drives_c_d": True, "home_and_path": True, "new_location_opened": True}
            )
        )
        page.get_by_role("button", name="Модели", exact=True).click()
        settings = page.frame_locator('iframe[title="Модели"]')
        settings.get_by_text("Chat model", exact=True).locator("..").get_by_role(
            "button"
        ).click()
        settings.get_by_text("Not connected", exact=True).first.wait_for()
        settings.get_by_role("menuitem").filter(has_text="GPT-5.6 Sol").click()
        settings.locator('[data-provider="openai"]').wait_for()
        card = settings.locator('[data-provider="lmstudio"]')
        card.wait_for()
        snapshot = page.evaluate(
            """async()=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});return Object.fromEntries(await Promise.all(['lmstudioBaseURL','lmstudioModelId','defaultModelId'].map(async k=>[k,(await s.get(k))??null])));} """
        )
        try:
            textbox = (
                card.get_by_text("Base URL", exact=True)
                .locator("..")
                .get_by_role("textbox")
            )
            textbox.fill(base)
            textbox.press("Tab")
            picker = card.get_by_role("combobox", name="LM Studio detected models")
            picker.wait_for(timeout=15000)
            picker.click()
            settings.get_by_role("option", name="qa-local-two", exact=True).click()
            assert (
                card.get_by_text("Model ID", exact=True)
                .locator("..")
                .get_by_role("textbox")
                .input_value()
                == "qa-local-two"
            )
            page.get_by_role("button", name="Закрыть настройки", exact=True).click()
            page.get_by_role("button", name="Модели", exact=True).click()
            assert (
                card.get_by_text("Model ID", exact=True)
                .locator("..")
                .get_by_role("textbox")
                .input_value()
                == "qa-local-two"
            )
            mode["status"] = 401
            card.get_by_role("button", name="Discover models", exact=True).click()
            card.get_by_role("status").filter(has_text="HTTP 401").wait_for(
                timeout=10000
            )
            print(
                json.dumps(
                    {
                        "cloud_catalog": True,
                        "configure_openai": True,
                        "local_discovery": True,
                        "model_selection_persisted": True,
                        "http_401_is_failure": True,
                    }
                )
            )
            page.screenshot(path=str(root / ".cache/connections-models.png"))
        finally:
            page.evaluate(
                """async values=>{const s=await window.__JARVIS_NATIVE__.store.load('terax-settings.json',{defaults:{}});for(const [key,value] of Object.entries(values)){if(value===null)await s.delete(key);else await s.set(key,value);}await s.save();}""",
                snapshot,
            )
        assert not errors, errors
        print(json.dumps({"page_errors": errors}))
        page.evaluate("window.__TAURI_INTERNALS__.invoke('pty_close_all')")
        try:
            page.evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|close')")
        except PlaywrightError:
            pass
        browser.close()
    process.wait(timeout=20)
    assert process.returncode == 0
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    server.shutdown()
    server.server_close()
    log.close()
