"""Structured browser tools over Playwright/CDP."""
from __future__ import annotations
import secrets
import asyncio
import time
from local_mode.messages import OwnedText, OwnedError, OwnedValueError
from local_mode.media_player import (STATE_SCRIPT, PLAY_SCRIPT, MUSIC_RESULTS_SCRIPT,
    CONTROL_ACTIONS, control_plan, verify_control, validate_control_result, music_query, observed_music_url)
from urllib.parse import urlsplit, quote_plus


class BrowserAPI:
    def __init__(self, report=None):
        self.report = report
        self.refs = {}
        self.tabs = {}

    async def _music_observation(self, page, script, ready):
        deadline = time.monotonic()+10
        state = await page.evaluate(script)
        while not ready(state) and time.monotonic() < deadline:
            await asyncio.sleep(.25)
            state = await page.evaluate(script)
        return state

    async def snapshot(self, page):
        # Only visible elements; input values and passwords are not sent to the model.
        nodes = page.locator('a[href], button, input:not([type=password]):not([type=hidden]), textarea, select, [role=button], [role=link]')
        for _, handle in self.refs.values():
            await handle.dispose()
        self.refs.clear()
        elements = []
        for index in range(min(await nodes.count(), 250)):
            node = nodes.nth(index)
            if not await node.is_visible():
                continue
            ref = secrets.token_hex(5)
            handle = await node.element_handle()
            if handle is None:
                continue
            self.refs[ref] = (page, handle)
            elements.append({"ref": ref, "tag": await node.evaluate("e => e.tagName.toLowerCase()"),
                             "name": (await node.get_attribute("aria-label") or await node.get_attribute("placeholder") or await node.inner_text())[:160]})
        return {"ok": True, "url": page.url, "title": await page.title(),
                "text": (await page.locator("body").inner_text())[:12000],
                "elements": elements, "untrusted_content": True}

    async def execute(self, controller, args):
        try:
            async with asyncio.timeout(150):
                return await self._execute(controller, args)
        except TimeoutError:
            return {'ok':False,'verified':False,'timed_out':True,'error':OwnedText('The browser did not complete the action in time. Inspect the tab before retrying.')}

    async def _execute(self, controller, args):
        action = args.get("action", "snapshot")
        if action == "search":
            args = {**args, "url": "https://www.google.com/search?q=" + quote_plus(args.get("query", ""))}
            action = "go_to"
        await controller._launch_browser_if_needed()
        context = controller._context
        pages = [p for p in context.pages if not p.is_closed()]
        for page in pages:
            if page not in self.tabs.values():
                self.tabs[secrets.token_hex(5)] = page
        if action == "list_tabs":
            return {"ok": True, "tabs": [{"tab_id": key, "url": page.url, "title": await page.title()}
                    for key, page in self.tabs.items() if page in pages], "untrusted_content": True}
        if action == "new_tab":
            page = await context.new_page()
            controller._page = page
            self.tabs[secrets.token_hex(5)] = page
        elif args.get("tab_id"):
            page = self.tabs.get(args["tab_id"])
            if page is None or page.is_closed():
                raise OwnedValueError("Tab not found. Request list_tabs.")
            controller._page = page
        else:
            page = await controller._get_page()
        page.set_default_timeout(10000)
        if action == 'music_play':
            query = music_query(args)
            await page.goto('https://www.youtube.com/results?search_query='+quote_plus(query), wait_until='domcontentloaded', timeout=30000)
            url = observed_music_url(await self._music_observation(page,MUSIC_RESULTS_SCRIPT,observed_music_url))
            if not url:
                return {'ok':False,'verified':False,'error':OwnedText('No ordinary music video was found in the visible YouTube search results.')}
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            player = await self._music_observation(page,STATE_SCRIPT,lambda state:state.get('video'))
            if not player.get('video'):
                return {'ok':False,'verified':False,'error':OwnedText('No player was found in the selected tab. Open a video.')}
            controller._page = page
            action = 'media_play'
        if action in {"media_play", "media_resume"}:
            if not (await page.evaluate(STATE_SCRIPT)).get("video"):
                if args.get('action') == 'music_play':
                    return {'ok':False,'verified':False,'error':OwnedText('No player was found in the selected tab. Open a video.')}
                candidates = [candidate for candidate in pages if (await candidate.evaluate(STATE_SCRIPT)).get("video")]
                if len(candidates) != 1:
                    return {"ok":False,"verified":False,"error":OwnedText("Select the tab containing the desired player.")}
                page = controller._page = candidates[0]
            deadline = time.monotonic()+90
            while True:
                before = await page.evaluate(STATE_SCRIPT)
                prepared = await page.evaluate(PLAY_SCRIPT)
                try:
                    validate_control_result(prepared)
                except ValueError as error:
                    return {'ok':False,'verified':False,'error':OwnedText(error.template,*error.values)}
                if isinstance(prepared,dict) and prepared.get('play_error'):
                    return {'ok':False,'verified':False,'error':OwnedText('The browser rejected playback: {0}.',prepared['play_error'])}
                await page.wait_for_timeout(800)
                after = await page.evaluate(STATE_SCRIPT)
                verified = bool(before.get('url')==after.get('url') and before.get('source')==after.get('source') and after.get("video") and not after.get("paused") and not after.get("muted") and after.get("volume",0)>0 and after.get('ready_state',2)>=2 and not after.get('ended') and not before.get('ad_showing') and not after.get("ad_showing") and not after.get("error") and after.get("current_time",0)-before.get("current_time",0)>=.5)
                if verified or after.get('error') or time.monotonic() >= deadline or not isinstance(prepared,dict) or not (prepared.get('pending') or prepared.get('ad_showing')):
                    return {"ok":verified,"verified":verified,"media":after,"reply":OwnedText("Video is playing.") if verified else OwnedText("The player did not confirm playback.")}
                await asyncio.sleep(.2)
        if action in CONTROL_ACTIONS:
            if not args.get("tab_id") and not (await page.evaluate(STATE_SCRIPT)).get("video"):
                candidates = [candidate for candidate in pages if (await candidate.evaluate(STATE_SCRIPT)).get("video")]
                if len(candidates) != 1:
                    return {"ok":False,"verified":False,"error":OwnedText("Select the tab containing the desired player.")}
                page = controller._page = candidates[0]
            try:
                before = await page.evaluate(STATE_SCRIPT)
                script, expected = control_plan(action, args, before)
                validate_control_result(await page.evaluate(script))
                after = await page.evaluate(STATE_SCRIPT)
                return verify_control(action, expected, before, after)
            except ValueError as error:
                return {"ok":False,"verified":False,"error":OwnedText(error.template, *error.values) if isinstance(error, OwnedError) else str(error)}
        if action in {"go_to", "new_tab"} and args.get("url"):
            url = args["url"]
            if urlsplit(url).scheme not in {"http", "https"}:
                raise OwnedValueError("Only HTTP/HTTPS pages are allowed.")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        elif action == "back":
            await page.go_back(wait_until="commit")
        elif action == "forward":
            await page.go_forward(wait_until="commit")
        elif action == "reload":
            await page.reload(wait_until="domcontentloaded")
        elif action == "close_tab":
            await page.close()
            self.refs.clear()
            return {"ok": True, "result": OwnedText("Tab closed.")}
        elif action in {"click", "smart_click", "type", "smart_type"}:
            ref = args.get("ref")
            if ref:
                entry = self.refs.get(ref)
                if entry is None or entry[0] != page:
                    raise OwnedValueError("Stale ref. Request a snapshot and select the element again.")
                locator = entry[1]
            elif args.get("selector"):
                locator = page.locator(args["selector"])
            elif args.get("role") and args.get("name"):
                locator = page.get_by_role(args["role"], name=args["name"], exact=True)
            elif action in {"type", "smart_type"} and args.get("description"):
                locator = page.get_by_label(args["description"], exact=True)
            else:
                name = args.get("description") or args.get("text")
                if not name:
                    raise OwnedValueError("Provide a ref, selector or role/name from the snapshot.")
                locator = page.get_by_text(name, exact=True)
            if not ref and await locator.count() != 1:
                raise OwnedValueError("The element was not found or is ambiguous. Refine the snapshot selection.")
            if action in {"click", "smart_click"}:
                await locator.click()
            else:
                if await locator.get_attribute("type") == "password":
                    raise OwnedValueError("Passwords must be entered manually by the user.")
                await locator.fill(args.get("text", ""))
        elif action == "fill_form":
            fields = args.get("fields", {})
            if not fields or len(fields) > 20:
                raise OwnedValueError("Provide between 1 and 20 form fields.")
            targets = []
            for selector, value in fields.items():
                node = page.locator(selector)
                if await node.count() != 1 or await node.get_attribute("type") == "password":
                    raise OwnedValueError("The field is ambiguous or is a password.")
                targets.append((node, str(value)))
            for node, value in targets:
                await node.fill(value)
        elif action == "press":
            await page.keyboard.press(args.get("key", "Enter"))
        elif action == "scroll":
            await page.mouse.wheel(0, -500 if args.get("direction") == "up" else 500)
        elif action not in {"snapshot", "get_text", "select_tab", "new_tab", "go_to"}:
            raise OwnedValueError("Unknown browser command: {0}", action)
        if page not in self.tabs.values():
            self.tabs[secrets.token_hex(5)] = page
        result = await self.snapshot(page)
        result["tab_id"] = next((key for key, value in self.tabs.items() if value == page), "")
        return result

    def __call__(self, args):
        from local_mode.browser_profiles import read_preferences
        from actions import browser_control as legacy
        prefs = read_preferences()
        from local_mode.browser_profiles import profiles
        from local_mode.config import Settings
        from local_mode import chrome_mcp
        available, current = profiles(Settings.from_env().browser_channel)
        from local_mode.playback_queue import QUEUE_ACTIONS, playback_queue
        if args.get("action") in QUEUE_ACTIONS:
            profile = args.get("profile_id") or (prefs["profile"] if prefs["mode"] == "specific" else current)
            target = {"profile_id":profile} if prefs["mode"] != "new" or args.get("profile_id") else {}
            return playback_queue().execute(args, self, target, self.report)
        if args.get("action") == "list_profiles":
            return {"ok":True,"profiles":[{**item,"extension_installed":chrome_mcp.installed(item["id"],Settings.from_env().browser_channel)} for item in available]}
        explicit = args.get("profile_id")
        if explicit or prefs["mode"] != "new":
            profile = explicit or (prefs["profile"] if prefs["mode"] == "specific" else current)
            if profile not in {item["id"] for item in available}:
                return {"ok":False,"requires_setup":True,"reply":OwnedText("Select an existing Chrome profile in browser settings.")}
            if args.get("action") == "close":
                chrome_mcp.close_all()
                return {"ok":True,"result":OwnedText("Connection closed; the main Chrome browser remains running.")}
            if args.get("action") in {"go_to", "search"} and not explicit:
                from local_mode.chrome_credentials import load_token
                channel = Settings.from_env().browser_channel
                if not chrome_mcp.installed(profile, channel) or not load_token(profile, channel):
                    from local_mode.browser_profiles import open_url
                    url = ("https://www.google.com/search?q=" + quote_plus(args.get("query", ""))
                           if args["action"] == "search" else args.get("url", ""))
                    open_url(url, channel)
                    return {"ok": True, "verified": False, "reply": OwnedText("Opened in the selected browser profile: {0}. Tab content has not been verified.", url)}
            return chrome_mcp.execute(profile,args,Settings.from_env().browser_channel)
        if args.get("incognito"):
            return {"ok": False, "error": OwnedText("Private sessions are not supported by the shared browser backend.")}
        legacy._ensure_started()
        if args.get("action") == "close":
            return legacy._bt.run(legacy._bt._close_browser())
        return legacy._bt.run(self.execute(legacy._bt, args), timeout=160)


_default_api = None


def shared_browser_api():
    """One tab/ref session for legacy entry points and all shared agents."""
    global _default_api
    if _default_api is None:
        _default_api = BrowserAPI()
    return _default_api
