"""Настоящий скрытый Tauri WebView2; управление исключительно через CDP API."""

import os, subprocess, time, json
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError

root = Path(__file__).resolve().parents[2]
env = {
    **os.environ,
    "JARVIS_ROOT": str(root),
    "JARVIS_DESKTOP_TEST": "1",
    "WEBVIEW2_USER_DATA_FOLDER": str(root / ".cache/desktop-test/webview2"),
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=9337 --remote-debugging-address=127.0.0.1",
}
log = (root / ".cache/tauri-native-test.log").open("w", encoding="utf-8")
process = subprocess.Popen(
    [
        str(
            root
            / "desktop-bin"
            / json.loads(
                (root / "desktop-bin/manifest.json").read_text(encoding="utf-8-sig")
            ).get("file", "jarvis-desktop.exe")
        )
    ],
    cwd=root,
    env=env,
    stdout=log,
    stderr=log,
    creationflags=0x08000000,
)

try:
    for _ in range(100):
        if process.poll() is not None:
            raise AssertionError("Native app exited early")
        try:
            if (
                httpx.get("http://127.0.0.1:9337/json/version", timeout=0.4).status_code
                == 200
            ):
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9337")
        context = browser.contexts[0]
        page = next(page for page in context.pages if "tauri.localhost" in page.url)
        page.get_by_role("textbox", name="Команда для Jarvis").wait_for(timeout=30000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.get_by_role("navigation", name="Основная навигация").get_by_role("button", name="Gemini", exact=True).click()
        dialog = page.get_by_role("dialog", name="Gemini", exact=True)
        mode = dialog.get_by_role("combobox", name="Режим embedding")
        mode.wait_for()
        expect(mode).to_be_enabled(timeout=30000)
        mode.select_option("local")
        expect(mode).to_be_enabled(timeout=30000)
        assert mode.input_value() == "local"
        dialog.get_by_role("textbox", name="Текст для embedding", exact=True).fill("A red square")
        dialog.get_by_role("button", name="Создать embedding / сравнить с предыдущим").click()
        dialog.get_by_role("status").filter(has_text="embeddinggemma-2:Q8_0").wait_for(timeout=120000)
        first = dialog.get_by_role("status").filter(has_text="embeddinggemma-2:Q8_0").inner_text()
        assert "768" in first and "Vulkan" in first, first
        dialog.get_by_role("textbox", name="Текст для embedding", exact=True).fill("A blue square")
        dialog.get_by_role("button", name="Создать embedding / сравнить с предыдущим").click()
        dialog.get_by_role("status").filter(has_text="Косинусное сходство").wait_for(timeout=120000)
        mode.select_option("cloud")
        expect(mode).to_be_enabled(timeout=30000)
        dialog.get_by_role("button", name="Создать embedding / сравнить с предыдущим").click()
        dialog.get_by_role("status").filter(has_text="gemini-embedding-2:").wait_for(timeout=120000)
        assert dialog.get_by_role("status").filter(has_text="Косинусное сходство").count() == 0
        dialog.get_by_role("button", name="Обновить счётчики").click()
        dialog.get_by_role("button", name="Проверить доступные модели").click()
        dialog.get_by_role("heading", name="Модели, видимые текущему ключу").wait_for(timeout=60000)
        mode.select_option("cpu")
        expect(mode).to_be_enabled(timeout=30000)
        assert dialog.get_by_role("status").filter(has_text="embeddinggemma").count() == 0
        page.reload()
        page.get_by_role("textbox", name="Команда для Jarvis").wait_for(timeout=30000)
        page.get_by_role("navigation", name="Основная навигация").get_by_role("button", name="Gemini", exact=True).click()
        dialog = page.get_by_role("dialog", name="Gemini", exact=True)
        mode = dialog.get_by_role("combobox", name="Режим embedding")
        expect(mode).to_be_enabled(timeout=30000)
        assert mode.input_value() == "cpu"
        translation = dialog.get_by_role("region", name="Перевод текста и документов")
        translation_mode = translation.get_by_role("combobox", name="Режим перевода", exact=True)
        expect(translation_mode).to_be_enabled(timeout=30000)
        translation_mode.select_option("local")
        expect(translation_mode).to_be_enabled(timeout=30000)
        translation.get_by_role("textbox", name="Текст для перевода", exact=True).fill("Открой документ, пожалуйста.")
        translation.get_by_role("button", name="Перевести", exact=True).click()
        translation.get_by_role("status").filter(has_text="локально, CPU").wait_for(timeout=120000)
        assert "document" in translation.get_by_role("textbox", name="Результат перевода", exact=True).input_value().lower()
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new("RGB", (1200,500), "white")
        ImageDraw.Draw(image).text((40,100), "Invoice number 12345. Total 250 dollars.", fill="black", font=ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 42))
        fixture=root/".cache/desktop-test/scan.pdf"
        image.save(fixture, "PDF", resolution=144)
        translation.get_by_role("textbox", name="Текст для перевода", exact=True).fill("")
        translation.get_by_role("textbox", name="Документ для перевода", exact=True).fill(str(fixture))
        translation.get_by_role("combobox", name="Исходный язык перевода", exact=True).select_option("en")
        translation.get_by_role("combobox", name="Язык перевода", exact=True).select_option("ru")
        translation.get_by_role("button", name="Перевести", exact=True).click()
        translation.get_by_role("status").filter(has_text="локально, CPU").wait_for(timeout=120000)
        assert "12345" in translation.get_by_role("textbox", name="Результат перевода", exact=True).input_value()
        translation_mode.select_option("cloud")
        expect(translation_mode).to_be_enabled(timeout=30000)
        translation.get_by_role("button", name="Перевести", exact=True).click()
        translation.get_by_role("status").filter(has_text="Gemini").wait_for(timeout=120000)
        assert "250" in translation.get_by_role("textbox", name="Результат перевода", exact=True).input_value()
        translation_mode.select_option("auto")
        expect(translation_mode).to_be_enabled(timeout=30000)
        for width,height in [(640,480),(1920,1080),(2560,1440)]:
            page.set_viewport_size({"width":width,"height":height})
            assert dialog.evaluate("el=>el.getBoundingClientRect().right<=innerWidth+1")
            assert dialog.evaluate("el=>el.scrollWidth<=el.clientWidth+1")
        assert not errors, errors
        print("PASS: native GUI embeddings/cloud API, persisted modes, local and cloud scan translation, isolated CPU worker, responsive settings")
        try:
            page.evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|close')")
        except PlaywrightError:
            pass
        browser.close()
    process.wait(timeout=20)
    assert process.returncode == 0, process.returncode
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=15)
    log.close()
