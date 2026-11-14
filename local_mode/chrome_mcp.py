"""Official Playwright MCP sessions for explicit Chrome profiles."""
from __future__ import annotations
import hashlib
from local_mode.messages import OwnedError, OwnedText, OwnedValueError, OwnedRuntimeError
import json
import os
import queue
import re
import subprocess
import threading
import time
from urllib.parse import quote_plus, urlsplit
from local_mode.browser_profiles import profiles, user_data
from local_mode.config import ROOT
from local_mode.media_player import (STATE_SCRIPT, PLAY_SCRIPT, MUSIC_RESULTS_SCRIPT,
    CONTROL_ACTIONS, control_player, validate_control_result, music_query, observed_music_url)

EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
STORE_URL = "https://chromewebstore.google.com/detail/playwright-extension/" + EXTENSION_ID
MUSIC_READY_TIMEOUT = 10
# Read rendered text without accessibility-tree refs, hidden input values or markup.
PAGE_TEXT_SCRIPT = "() => ({ scope: 'main_document_rendered_text', iframe_count: document.querySelectorAll('iframe').length, hint: 'Use snapshot for frames, shadow DOM and accessibility labels.', url: location.href, title: document.title, text: document.body?.innerText ?? '' })"
_sessions = {}
_sessions_lock = threading.Lock()
_session_locks = {}
_operations = {}


class ChromeRequestTimeout(OwnedRuntimeError):
    pass


class ChromeRequestCancelled(OwnedRuntimeError):
    pass


def installed(profile: str, channel: str = "chrome") -> bool:
    return (user_data(channel) / profile / "Extensions" / EXTENSION_ID).is_dir()


class ChromeSession:
    def __init__(self, profile: str, channel: str = "chrome", *, deadline=None, cancel=None):
        if profile not in {item["id"] for item in profiles(channel)[0]}:
            raise OwnedValueError("Chrome profile not found. Refresh the profile list.")
        from local_mode.chrome_credentials import load_token
        token = load_token(profile,channel)
        self.token_fingerprint = hashlib.sha256((token or "").encode()).hexdigest()
        self._secret = token
        self.profile = profile
        self.lock = threading.RLock()
        self.messages = queue.Queue()
        self.sequence = 0
        self._operation_deadline = deadline
        self._request_cancel = cancel
        self._media_wait_cancel = threading.Event()
        env = {k:v for k,v in os.environ.items() if k not in {"GEMINI_API_KEY", "GOOGLE_API_KEY", "PLAYWRIGHT_MCP_EXTENSION_TOKEN", "PLAYWRIGHT_MCP_PROFILE_DIR_NAME"}}
        if token:
            env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = token
        # Тот же Node/CLI, что использует python -m playwright, без дочерней
        # Python-обёртки: закрытие сеанса завершает именно сервер MCP.
        from playwright._impl._driver import compute_driver_executable
        node, cli = compute_driver_executable()
        self.process = subprocess.Popen([node, cli, "mcp", "--extension", "--browser", channel,
                    "--profile-dir-name", profile, "--no-webmcp", "--timeout-action", "10000",
                    "--output-dir", str(ROOT / ".cache" / "playwright-mcp")],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def read():
            try:
                for line in self.process.stdout:
                    if len(line) > 2 * 1024 * 1024:
                        continue
                    try:self.messages.put(json.loads(line))
                    except ValueError:continue
            finally:self.messages.put(None)
        threading.Thread(target=read,daemon=True,name="ChromeMCPResponses").start()
        try:
            self.request("initialize", {"protocolVersion":"2025-03-26", "capabilities":{}, "clientInfo":{"name":"Jarvis","version":"1.0"}})
            self.send({"jsonrpc":"2.0","method":"notifications/initialized"})
            self.tools = {item["name"]: item for item in self.request("tools/list",{})["tools"]}
        except Exception:
            self.close()
            raise
        self._operation_deadline = None

    def redact(self, value):
        text = str(value)
        secret = getattr(self,"_secret",None)
        if secret:
            text = text.replace(secret,"[redacted]")
        return re.sub(r"([?&](?:token|access_token|api_key|key)=)[^&\s)\]\"]+",r"\1[redacted]",text,flags=re.I)

    def error_text(self, error):
        if isinstance(error, OwnedError):
            values=tuple(self.redact(value) if isinstance(value,str) else value for value in error.values)
            return OwnedText(error.template,*values)
        return self.redact(error)

    def send(self, message):
        self.process.stdin.write(json.dumps(message,ensure_ascii=True)+"\n")
        self.process.stdin.flush()

    def request(self, method, params, timeout=45):
        with self.lock:
            self.sequence += 1
            identifier = self.sequence
            deadline = time.monotonic()+timeout
            operation_deadline = getattr(self, '_operation_deadline', None)
            if operation_deadline is not None:
                deadline = min(deadline, operation_deadline)
            cancel = getattr(self, '_request_cancel', None)
            if cancel is not None and cancel.is_set():
                self.close()
                raise ChromeRequestCancelled('Video startup cancelled.')
            if time.monotonic() >= deadline:
                self.close()
                raise ChromeRequestTimeout('The browser did not complete the action in time. Inspect the tab before retrying.')
            self.send({"jsonrpc":"2.0","id":identifier,"method":method,"params":params})
            while time.monotonic()<deadline:
                cancel = getattr(self, '_request_cancel', None)
                if cancel is not None and cancel.is_set():
                    self.close()
                    raise ChromeRequestCancelled('Video startup cancelled.')
                try:message = self.messages.get(timeout=min(.1,max(.01,deadline-time.monotonic())))
                except queue.Empty:continue
                if message is None:
                    raise OwnedRuntimeError("The Playwright MCP process exited.")
                if message.get("id") != identifier:
                    continue
                if "error" in message:
                    detail=message["error"].get("message")
                    if detail is None:
                        raise OwnedRuntimeError("MCP error.")
                    raise RuntimeError(self.redact(detail))
                return message["result"]
            # После таймаута закрываем сеанс: поздний ответ нельзя принять за новую команду.
            self.close()
            raise ChromeRequestTimeout("Chrome did not confirm the connection or result in time. Check the permission window and profile; inspect the tab before retrying.")

    def tool(self, name, args):
        if name not in self.tools:
            raise OwnedValueError("This Playwright version does not support {0}.", name)
        args = dict(args)
        properties = self.tools[name].get("inputSchema",{}).get("properties",{})
        # SDK 1.64 использует target; прежние серверы называли это ref.
        if "ref" in args and "target" in properties:
            args["target"] = args.pop("ref")
        if name == "browser_fill_form":
            field_properties = properties["fields"]["items"]["properties"]
            if "target" in field_properties:
                args["fields"] = [{**{k:v for k,v in field.items() if k != "ref"}, "target":field["ref"]} for field in args["fields"]]
        result = self.request("tools/call", {"name":name,"arguments":args})
        return {"ok":not result.get("isError",False), "text":self.redact("\n".join(item.get("text","") for item in result.get("content",[]) if item.get("type")=="text"))[:20000],
                "untrusted_content":True, "profile_id":self.profile}

    def _media_evaluate(self, function):
        result = self.tool("browser_evaluate", {"function": function})
        if not result["ok"]:
            raise OwnedValueError("Chrome player: {0}", result["text"])
        text = result["text"].split("### Result", 1)[-1].lstrip()
        try:
            state, _ = json.JSONDecoder().raw_decode(text)
        except ValueError as error:
            raise OwnedValueError("Could not read the Chrome player state.") from error
        if not isinstance(state, dict):
            raise OwnedValueError("Invalid Chrome player state.")
        return state

    def _ensure_media_tab(self):
        if self._media_evaluate(STATE_SCRIPT).get("video"):
            return
        result = self.tool("browser_tabs", {"action": "list"})
        if not result["ok"]:
            raise OwnedValueError("Could not retrieve player tabs.")
        rows = re.findall(r"(?m)^- (\d+): (.*)$", result["text"])
        original = next((int(index) for index, text in rows if "(current)" in text), None)
        candidates = [int(index) for index, text in rows if re.search(r"\(https?://", text)]
        found = []
        for index in candidates[:8]:
            selected = self.tool("browser_tabs", {"action": "select", "index": index})
            if selected["ok"] and self._media_evaluate(STATE_SCRIPT).get("video"):
                found.append(index)
        if len(found) == 1:
            self.tool("browser_tabs", {"action": "select", "index": found[0]})
            return
        if original is not None:
            self.tool("browser_tabs", {"action": "select", "index": original})
        raise OwnedValueError("Multiple players were found. Select the desired tab." if found else
                         "No player was found in the available tabs of the selected Chrome profile.")

    def media_state(self):
        def read():
            return self._media_evaluate(STATE_SCRIPT)
        first=read()
        time.sleep(0.8)
        state=read()
        progress=max(0,float(state.get("current_time",0))-float(first.get("current_time",0)))
        verified=bool(first.get("url")==state.get("url") and first.get('source')==state.get('source') and state.get("video") and not state.get("paused")
            and not state.get("muted") and state.get("volume",0)>0 and state.get("ready_state",0)>=2
            and not state.get("ended") and not state.get("ad_showing") and not first.get("ad_showing")
            and not state.get("error") and progress>=0.5)
        return {"ok":True,"profile_id":self.profile,"verified":verified,"media":state,"progress_seconds":round(progress,2),"untrusted_content":True}

    def _music_observation(self, script, ready):
        deadline = min(time.monotonic()+MUSIC_READY_TIMEOUT, self._operation_deadline)
        state = self._media_evaluate(script)
        while not ready(state) and time.monotonic() < deadline:
            if self._media_wait_cancel.wait(.25):
                raise ChromeRequestCancelled('Video startup cancelled.')
            state = self._media_evaluate(script)
        return state

    def execute(self, args, *, deadline=None, cancel=None):
        with self.lock:
            self._operation_deadline = deadline or time.monotonic()+150
            media_action = args.get('action') in {'music_play', 'media_play', 'media_resume'}
            if media_action:
                if cancel is None:
                    cancel = threading.Event()
                self._media_wait_cancel = cancel
            self._request_cancel = cancel
            try:
                return self._execute(args)
            finally:
                self._request_cancel = None
                self._operation_deadline = None

    def _execute(self, args):
        with self.lock:
            action = args.get("action","snapshot")
            if args.get("tab_id") and action not in {"select_tab","close_tab"}:
                selected = self.tool("browser_tabs", {"action":"select","index":int(args["tab_id"])})
                if not selected["ok"]:return selected
            if action in {"list_tabs","new_tab","select_tab","close_tab"}:
                parameters={"action":{"list_tabs":"list","new_tab":"new","select_tab":"select","close_tab":"close"}[action]}
                if args.get("tab_id"):parameters["index"]=int(args["tab_id"])
                result=self.tool("browser_tabs",parameters)
                if action != "new_tab" or not args.get("url") or not result["ok"]:return result
                action="go_to"
            if action in {"go_to","search"}:
                url = "https://www.google.com/search?q="+quote_plus(args.get("query","")) if action=="search" else args.get("url","")
                if urlsplit(url).scheme not in {"http","https"} or not urlsplit(url).hostname:
                    raise OwnedValueError("Only HTTP/HTTPS links are allowed.")
                return self.tool("browser_navigate",{"url":url})
            if action=="media_state":return self.media_state()
            if action == 'music_play':
                query = music_query(args)
                navigated = self.tool('browser_navigate', {'url':'https://www.youtube.com/results?search_query='+quote_plus(query)})
                if not navigated['ok']:
                    return navigated
                observed = self._music_observation(MUSIC_RESULTS_SCRIPT, observed_music_url)
                url = observed_music_url(observed)
                if not url:
                    return {'ok':False, 'verified':False, 'error':OwnedText('No ordinary music video was found in the visible YouTube search results.')}
                navigated = self.tool('browser_navigate', {'url':url})
                if not navigated['ok']:
                    return navigated
                self._music_observation(STATE_SCRIPT, lambda state:state.get('video'))
                return self._execute({'action':'media_play'})
            if action in {"media_play", "media_resume"}:
                if action == "media_resume" and not args.get("tab_id"):
                    try:
                        self._ensure_media_tab()
                    except ValueError as error:
                        return {"ok":False,"verified":False,"error":self.error_text(error)}
                cancel = getattr(self, "_media_wait_cancel", None)
                if cancel is None:
                    cancel = self._media_wait_cancel = threading.Event()
                # Рекламу ждём локально, не расходуя каждый раз шаг Gemini.
                deadline=min(time.monotonic()+90, self._operation_deadline)
                result=None
                while time.monotonic()<deadline:
                    if cancel.is_set():
                        return {"ok":False,"verified":False,"cancelled":True,"reply":OwnedText("Video startup cancelled.")}
                    prepared=self._media_evaluate(PLAY_SCRIPT)
                    try:
                        validate_control_result(prepared)
                    except ValueError as error:
                        return {'ok':False,'verified':False,'error':self.error_text(error)}
                    if prepared.get('play_error'):
                        return {'ok':False,'verified':False,'error':OwnedText('The browser rejected playback: {0}.', prepared['play_error'])}
                    if cancel.is_set():
                        return {"ok":False,"verified":False,"cancelled":True,"reply":OwnedText("Video startup cancelled.")}
                    result=self.media_state()
                    if result["verified"]:
                        return {**result,"action":action,"reply":OwnedText("Video is playing.")}
                    if result["media"].get("error"):
                        break
                    cancel.wait(1)
                return {**(result or {}),"ok":False,"verified":False,"error":OwnedText("The player did not confirm audible music after waiting for loading or ads.")}
            if action in CONTROL_ACTIONS:
                try:
                    if not args.get("tab_id"):
                        self._ensure_media_tab()
                    return {**control_player(self._media_evaluate, action, args), "profile_id":self.profile}
                except ValueError as error:
                    return {"ok":False,"verified":False,"action":action,"profile_id":self.profile,"error":self.error_text(error)}
            if action=="snapshot":return self.tool("browser_snapshot",{})
            if action=="get_text":return self.tool("browser_evaluate",{"function":PAGE_TEXT_SCRIPT})
            if action in {"click","smart_click","type","smart_type"}:
                if not args.get("ref"):raise OwnedValueError("A ref from the latest snapshot is required; arbitrary first buttons are not selected.")
                parameters={"ref":args["ref"]}
                if action in {"type","smart_type"}:parameters["text"]=args.get("text","")
                return self.tool("browser_type" if "type" in action else "browser_click",parameters)
            if action=="back":return self.tool("browser_navigate_back",{})
            if action in {"forward","reload"}:
                # Newer servers advertise navigation tools that already return a snapshot.
                navigation = "browser_navigate_forward" if action=="forward" else "browser_reload"
                if navigation in self.tools:
                    return self.tool(navigation,{})
                # Fixed application code only; never repeat navigation to recover output.
                function = "() => { history.forward(); }" if action=="forward" else "() => { location.reload(); }"
                result = self.tool("browser_evaluate",{"function":function})
                if not result["ok"] or re.search(r"(?m)^### (?:Page )?Snapshot\s*$", result.get("text", "")):
                    return result
                snapshot = self.tool("browser_snapshot",{})
                return {**snapshot, "action":action, "action_completed":True, "action_result":result}
            if action in {"press","scroll"}:
                key = args.get("key","Enter") if action=="press" else ("PageUp" if args.get("direction")=="up" else "PageDown")
                return self.tool("browser_press_key",{"key":key})
            if action=="fill_form":
                return self.tool("browser_fill_form",{"fields":[{"name":ref,"type":"textbox","ref":ref,"value":str(value)} for ref,value in args.get("fields",{}).items()]})
            raise OwnedValueError("Unknown Chrome command: {0}", action)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        if self.process.stdin:self.process.stdin.close()
        if self.process.stdout:self.process.stdout.close()


def execute(profile: str, args: dict, channel: str = "chrome") -> dict:
    deadline = time.monotonic()+150
    if not installed(profile,channel):
        return {"ok":False,"requires_setup":True,"setup_kind":"chrome_extension","profile_id":profile,"reply":OwnedText("The selected profile requires the official Playwright Extension. Connect the selected profile in Jarvis browser settings and confirm installation in Chrome.")}
    from local_mode.chrome_credentials import load_token
    token = load_token(profile,channel)
    if not token:
        return {"ok":False,"requires_setup":True,"setup_kind":"chrome_token","profile_id":profile,
                "reply":OwnedText("Enter the Playwright token for profile {0} in browser settings. You can paste the entire PLAYWRIGHT_MCP_EXTENSION_TOKEN=… line.", profile)}
    session = None
    key = (channel, profile)
    cancel = threading.Event()
    media_action = args.get('action') in {'music_play', 'media_play', 'media_resume'}
    with _sessions_lock:
        operation_lock = _session_locks.setdefault(key, threading.Lock())
        _operations[cancel] = (profile, media_action)
    acquired = False
    try:
        # Serialize one profile without holding the global inventory mutex during
        # connection, I/O or teardown. Stop can signal even an initializing MCP.
        while not acquired:
            if cancel.is_set():
                raise ChromeRequestCancelled('Video startup cancelled.')
            if time.monotonic() >= deadline:
                raise ChromeRequestTimeout('The browser did not complete the action in time. Inspect the tab before retrying.')
            acquired = operation_lock.acquire(timeout=.1)
        with _sessions_lock:
            session=_sessions.get(key)
        fingerprint = hashlib.sha256((token or '').encode()).hexdigest()
        if session is None or session.process.poll() is not None or session.token_fingerprint != fingerprint:
            if session is not None:
                session.close()
            session=ChromeSession(profile,channel,deadline=deadline,cancel=cancel)
            with _sessions_lock:
                _sessions[key]=session
        result = session.execute(args, deadline=deadline, cancel=cancel)
    except ChromeRequestCancelled:
        return {'ok':False,'verified':False,'cancelled':True,'reply':OwnedText('Video startup cancelled.')}
    except ChromeRequestTimeout as error:
        return {'ok':False,'verified':False,'timed_out':True,'profile_id':profile,
                'reply':OwnedText(error.template, *error.values)}
    except RuntimeError as error:
        return {"ok":False,"verified":False,"profile_id":profile,
                "reply":session.error_text(error) if session is not None else OwnedText('Could not start the Chrome connection. Check the browser settings.')}
    finally:
        with _sessions_lock:
            _operations.pop(cancel, None)
        if acquired:
            operation_lock.release()
    if not result.get("ok") and re.search(r"token|extension|connect",result.get("text",""),re.I):
        return {**result,"requires_setup":True,"setup_kind":"chrome_token",
                "reply":OwnedText("Could not connect to the selected profile. Update its Playwright token in settings. {0}", session.redact(result.get("text","")[:1000]))}
    return result


def cancel_media_waits(profile: str | None = None) -> None:
    """Можно вызвать из GUI: только Event, без ожидания занятого MCP-потока."""
    with _sessions_lock:
        sessions = [session for (_, selected), session in _sessions.items() if profile is None or selected == profile]
        pending = [cancel for cancel, (selected, media) in _operations.items() if media and (profile is None or selected == profile)]
    for cancel in pending:
        cancel.set()
    for session in sessions:
        cancel = getattr(session, "_media_wait_cancel", None)
        if cancel is not None:
            cancel.set()


def close_all():
    with _sessions_lock:
        sessions = list(_sessions.values())
        for cancel in _operations:
            cancel.set()
        _sessions.clear()
    for session in sessions:
        session.close()
