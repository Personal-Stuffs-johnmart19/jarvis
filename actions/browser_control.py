import asyncio
import threading
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

class _BrowserThread:

    def __init__(self):
        self._loop          = None
        self._thread        = None
        self._ready         = threading.Event()
        self._playwright    = None
        self._browser       = None
        self._context       = None       # Normal context
        self._incog_context = None       # Incognito/private context
        self._page          = None       # Normal page
        self._incog_page    = None       # Incognito page
        self._incog_browser = None       # Separate incognito browser instance
        self._engine_name   = "chromium" # Bilgi için sakla
        self._exe_path      = None
        self._channel       = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BrowserThread"
        )
        self._thread.start()
        self._ready.wait(timeout=15)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self):
        self._playwright = await async_playwright().start()

    def run(self, coro, timeout: int = 30):
        if not self._loop:
            raise RuntimeError("BrowserThread not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    # ── Tarayıcı başlatma ────────────────────────────────────────────────────

    async def _launch_browser_if_needed(self):
        """Ana tarayıcıyı başlat (henüz başlatılmadıysa)."""
        from local_mode.browser_profiles import control_endpoint
        from local_mode.config import Settings
        endpoint = await asyncio.to_thread(control_endpoint, Settings.from_env().browser_channel)
        if self._browser and self._browser.is_connected():
            if getattr(self, "_endpoint", None) == endpoint:
                return
            await self._browser.close()
            self._browser = self._context = self._page = None
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        self._endpoint = endpoint
        self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
        self._context = self._browser.contexts[0]

    async def _get_page(self, incognito: bool = False):
        """
        Sayfa döndürür.
        incognito=True → özel/gizli sekme
        incognito=False → normal sekme
        """
        await self._launch_browser_if_needed()

        if incognito:
            return await self._get_incognito_page()
        else:
            return await self._get_normal_page()

    async def _get_normal_page(self):
        if self._page is None or self._page.is_closed():
            if self._context is None:
                self._context = await self._browser.new_context(
                    viewport=None,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                )
            self._page = await self._context.new_page()
        return self._page

    async def _get_incognito_page(self):
        """
        Gizli/özel sekme açar.
        Chromium → new_context(no_viewport, storage_state temiz)
        Firefox  → new_context(incognito)  [Playwright'ta firefox private]
        """
        # Mevcut gizli sayfa varsa kapat, yenisini aç
        if self._incog_page and not self._incog_page.is_closed():
            return self._incog_page

        # Önceki gizli context'i kapat
        if self._incog_context:
            try:
                await self._incog_context.close()
            except Exception:
                pass

        print("[Browser] 🕵️  Opening private/incognito context...")

        if self._engine_name == "firefox":
            # Firefox: firefox_user_prefs ile private browsing
            self._incog_context = await self._browser.new_context(
                viewport=None,
                firefox_user_prefs={
                    "browser.privatebrowsing.autostart": True
                }
            )
        else:
            # Chromium tabanlı: --incognito argümanı ile ayrı tarayıcı aç
            # (Playwright'ta context-level incognito için browser.new_context yeterli
            #  çünkü her context zaten izole storage'a sahip.
            #  Gerçek incognito görünümü için ayrı browser instance gerekir.)
            engine = getattr(self._playwright, self._engine_name)
            launch_kwargs = {
                "headless": False,
                "args": ["--incognito", "--start-maximized"],
            }
            if self._exe_path:
                launch_kwargs["executable_path"] = self._exe_path
            elif self._channel:
                launch_kwargs["channel"] = self._channel

            try:
                incog_browser = await engine.launch(**launch_kwargs)
            except Exception:
                incog_browser = await self._playwright.chromium.launch(
                    headless=False,
                    args=["--incognito", "--start-maximized"]
                )

            self._incog_browser = incog_browser
            self._incog_context = await incog_browser.new_context(
                viewport=None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )

        self._incog_page = await self._incog_context.new_page()
        print("[Browser] ✅ Private/incognito page ready.")
        return self._incog_page

    # ── Eylemler ─────────────────────────────────────────────────────────────

    async def _go_to(self, url: str, incognito: bool = False) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        page = await self._get_page(incognito=incognito)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            mode = " [private]" if incognito else ""
            return f"Opened{mode}: {page.url}"
        except PlaywrightTimeout:
            return f"Timeout loading: {url}"
        except Exception as e:
            return f"Navigation error: {e}"

    async def _search(self, query: str, engine: str = "google", incognito: bool = False) -> str:
        from urllib.parse import quote_plus
        q = quote_plus(query)
        engines = {
            "google":     f"https://www.google.com/search?q={q}",
            "bing":       f"https://www.bing.com/search?q={q}",
            "duckduckgo": f"https://duckduckgo.com/?q={q}",
        }
        url = engines.get(engine.lower(), engines["google"])
        return await self._go_to(url, incognito=incognito)

    async def _click(self, selector=None, text=None, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8000)
                return f"Clicked: '{text}'"
            elif selector:
                await page.click(selector, timeout=8000)
                return f"Clicked: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found or not clickable."
        except Exception as e:
            return f"Click error: {e}"

    async def _type(self, selector=None, text: str = "", clear_first: bool = True, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            element = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await element.clear()
            await element.type(text, delay=50)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def _scroll(self, direction: str = "down", amount: int = 500, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def _press(self, key: str, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def _get_text(self, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)
        try:
            text = await page.inner_text("body")
            return text[:4000] if len(text) > 4000 else text
        except Exception as e:
            return f"Could not get page text: {e}"

    async def _fill_form(self, fields: dict, incognito: bool = False) -> str:
        page    = await self._get_page(incognito=incognito)
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                await el.type(str(value), delay=40)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def _smart_click(self, description: str, incognito: bool = False) -> str:
        page       = await self._get_page(incognito=incognito)
        desc_lower = description.lower()

        role_hints = {
            "button":    ["button", "buton", "btn"],
            "link":      ["link", "bağlantı"],
            "searchbox": ["search", "arama"],
            "textbox":   ["input", "field", "alan"],
        }
        for role, keywords in role_hints.items():
            if any(k in desc_lower for k in keywords):
                try:
                    await page.get_by_role(role).first.click(timeout=5000)
                    return f"Clicked ({role}): '{description}'"
                except Exception:
                    pass

        try:
            await page.get_by_text(description, exact=False).first.click(timeout=5000)
            return f"Clicked (text): '{description}'"
        except Exception:
            pass

        try:
            await page.get_by_placeholder(description, exact=False).first.click(timeout=5000)
            return f"Clicked (placeholder): '{description}'"
        except Exception:
            pass

        return f"Could not find: '{description}'"

    async def _smart_type(self, description: str, text: str, incognito: bool = False) -> str:
        page = await self._get_page(incognito=incognito)

        for method, locator in [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox")),
        ]:
            try:
                el = locator.first
                await el.clear()
                await el.type(text, delay=50)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue

        return f"Could not find input: '{description}'"

    async def _close_browser(self) -> str:
        if self._incog_context:
            try:
                await self._incog_context.close()
            except Exception:
                pass
            self._incog_context = None
            self._incog_page    = None

        if self._incog_browser:
            try:
                await self._incog_browser.close()
            except Exception:
                pass
            self._incog_browser = None

        if self._browser:
            if self._page and not self._page.is_closed():
                await self._page.close()
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page    = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        return "Browser closed."


# ── Singleton browser thread ─────────────────────────────────────────────────

_bt         = _BrowserThread()
_bt_started = False
_bt_lock    = threading.Lock()


def _ensure_started():
    global _bt_started
    with _bt_lock:
        if not _bt_started:
            _bt.start()
            _bt_started = True


# ── Public API ───────────────────────────────────────────────────────────────

def browser_control(parameters: dict, response=None, player=None, session_memory=None):
    """Compatibility entry point; share the current profile-aware browser backend."""
    global _api
    if _api is None:
        from local_mode.browser_api import shared_browser_api
        _api = shared_browser_api()
    return _api(parameters)


_api = None
