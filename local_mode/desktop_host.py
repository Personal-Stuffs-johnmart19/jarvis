"""Версионированный приватный stdio runtime для Tauri. Сетевого сервера нет."""

from __future__ import annotations
from concurrent.futures import Future, ThreadPoolExecutor
from collections import deque
from dataclasses import dataclass
import contextlib
import json
import os
import queue
import sys
import threading
import time
from typing import Any, Callable
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from local_mode.config import ROOT, Settings
from local_mode.gemini import safe_error
from local_mode.messages import state_label, tool_label, OwnedError, OwnedText, OwnedRuntimeError, OwnedValueError, text_payload
from local_mode.results import user_message, spoken_message, present_result
from local_mode.voice import LocalSpeaker
from local_mode.voice_cycle import VoiceCycle
from local_mode.models import MODEL_ORDER
from local_mode.storage import write_json

QUEUED_UI_METHODS = frozenset({
    "tasks.ui.pending", "tasks.ui.claim", "tasks.ui.complete", "tasks.ui.check",
    "tasks.ui.start", "tasks.ui.settled", "tasks.ui.orphaned",
})
PRIORITY_METHODS = frozenset({
    "control", "managed_chat.cancel", "embedding.cancel", "helper.text.complete", "workbench.bootstrap",
}) | QUEUED_UI_METHODS


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(default=1, ge=1, le=1)
    id: int = Field(ge=0)
    method: str = Field(min_length=1, max_length=64)
    params: dict = Field(default_factory=dict)


@dataclass
class Work:
    method: str
    params: dict
    future: Future
    request_id: str
    source: str = "local"


class DesktopRuntime:
    "A single worker owns LocalAgent; audio controls do not wait for model requests."

    def __init__(
        self, emit: Callable[[str, dict], None], settings: Settings | None = None
    ):
        self.emit = emit
        self.settings = settings or Settings.from_env()
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.speaker = LocalSpeaker()
        self.speaker.on_error = self.publish_speech_error
        self.speaker.on_status = self.publish_speech_status
        self.cycle = VoiceCycle(self.speaker)
        self.work: queue.Queue[Work] = queue.Queue(maxsize=8)
        self.messages: deque[dict] = deque(maxlen=200)
        self.lock = threading.RLock()
        self.muted = os.environ.get("JARVIS_DESKTOP_TEST") == "1"
        self.silent_mode = os.environ.get("JARVIS_DESKTOP_TEST") == "1"
        self.wake_enabled = False
        self.connected = False
        self.confirmation = False
        self.stream = ""
        self.interface_locale = "en"
        self.current_request = ""
        self.preview = False
        self.camera_at = 0.0
        self._thread = None
        self.target = None
        self.bot = None
        self._last_text_at = 0.0
        self.suggestions_path = ROOT / (
            ".cache/desktop-test/quick-suggestions.json"
            if os.environ.get("JARVIS_DESKTOP_TEST") == "1"
            else "data/quick-suggestions.json"
        )
        self.network_thread = None
        self.remote = None
        self.streaming_voice = None
        self.shared_busy = False
        from local_mode.shared_agent import SharedAgent

        self.shared_agent = SharedAgent(self.event)
        from local_mode.text_helpers import SharedTextProvider
        self.text_provider = SharedTextProvider(self.emit)
        from local_mode.services import ServiceRegistry
        self.services = ServiceRegistry(self, lambda: self.bot.tools if self.bot else None)

    def event(self, name: str, data: dict) -> None:
        self.emit(
            name,
            {**data, "request_id": self.current_request, "monotonic": time.monotonic()},
        )

    def publish_speech_error(self, text: str) -> None:
        with self.lock:
            if self.speaker.error != text:
                return
            self.write_log("ERR: " + str(text))
            self.event("backend.error", {"text": text_payload(text)})

    def publish_speech_status(self, text: str) -> None:
        # Serialize publication with snapshots and use the current value: a voice
        # switch may have superseded this callback after the speaker lock released.
        with self.lock:
            current = self.speaker.backend_status
            if current != text:
                return
            if text:
                self.write_log("SYS: " + str(text))
            self.event("speech.status", {"text": text_payload(text)})

    def report_background(self, text: str) -> None:
        text = user_message({"ok": True, "reply": text}, locale=self.interface_locale)
        self.write_log("AI: " + text)
        if not self.silent_mode:
            self.speaker.say(text)

    def report_worker_error(self, error: Exception) -> str:
        text = user_message({"ok": False, "error": safe_error(error)}, locale=self.interface_locale)
        self.write_log("ERR: " + text)
        return text

    def write_log(self, text: str) -> None:
        role = (
            "error"
            if text.startswith("ERR:")
            else (
                "user"
                if text.startswith(("You:", "Вы:"))
                else "assistant" if text.startswith("AI:") else "system"
            )
        )
        message = {
            "id": uuid4().hex,
            "role": role,
            "text": text[:16000],
            "time": time.time(),
        }
        with self.lock:
            self.messages.append(message)
        self.event("message", message)

    def set_state(self, state: str) -> None:
        self.cycle.set_phase(state)

    def set_persona(self, **kwargs) -> None:
        self.event("persona", kwargs)

    def set_vision_signal(self, text: str) -> None:
        self.event("vision", {"text": text})

    def set_vision_detections(self, events) -> None:
        self.event("vision.detections", {"items": list(events)[:100]})

    def set_arcade_signal(self, *args) -> None:
        self.event("arcade", {"signal": list(args)})

    def set_arcade_pose(self, *args) -> None:
        self.event("arcade", {"pose": list(args)})

    def set_arcade_frame(self, frame) -> None:
        self.set_vision_frame(frame)

    def set_vision_frame(self, frame) -> None:
        if not self.preview or time.monotonic() - self.camera_at < 0.2:
            return
        self.camera_at = time.monotonic()
        import cv2, base64

        image = cv2.resize(frame, (480, 360))
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 65])
        if ok:
            self.event(
                "camera.frame", {"image": base64.b64encode(encoded).decode("ascii")}
            )

    def _snapshot_messages(self) -> list[dict]:
        selected = []
        size = 0
        for message in reversed(self.messages):
            size += len(message["text"].encode("utf-8")) + 200
            if size > 300000:
                break
            selected.append(message)
        return list(reversed(selected))

    def snapshot(self) -> dict:
        from local_mode.voice_settings import available_neural_voices, gemini_tts_configured
        from local_mode.network.network_preferences import read_network_preferences
        from local_mode.browser_profiles import read_preferences

        with self.lock:
            return {
                "version": 1,
                "state": self.cycle.display_state(self.muted, self.confirmation),
                "muted": self.muted,
                "silent": self.silent_mode,
                "wake": self.wake_enabled,
                "connected": self.connected,
                "messages": self._snapshot_messages(),
                "stream": self.stream,
                "confirmation": self.confirmation,
                "model": self.settings.model,
                "models": list(MODEL_ORDER),
                "auto_fallback": self.settings.auto_fallback,
                "voice": self.speaker.voice,
                "voices": available_neural_voices(),
                "network": read_network_preferences(),
                "browser": read_preferences(),
                "voice_label": self.speaker.label,
                "speech_status": text_payload(self.speaker.backend_status),
                "key_configured": gemini_tts_configured(),
                "microphone": self.settings.microphone,
                "active_model": (
                    self.shared_agent.model if self.shared_agent.attached and self.shared_agent.model else self.bot.router.active_model if self.bot else self.settings.model
                ),
            }

    def submit(self, method: str, params: dict, source="local") -> Future:
        future = Future()
        if self.stop.is_set() or self.failed.is_set():
            raise RuntimeError("The backend has stopped. Restart Jarvis.")
        if method == "chat":
            text = params.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 12000:
                raise ValueError("Enter a command of up to 12000 characters.")
        if method in {"chat", "speech.recognize"}:
            self.cycle.invalidate_capture()
            self.speaker.cancel()
            from local_mode.chrome_mcp import cancel_media_waits

            cancel_media_waits()
        self.work.put_nowait(Work(method, params, future, uuid4().hex, source))
        return future

    def control(self, action: str) -> dict:
        if action == "cancel_listen":
            self.cycle.invalidate_capture()
        elif action == "pause":
            self.speaker.pause()
        elif action == "resume":
            self.speaker.resume()
        elif action == "toggle_pause":
            self.speaker.toggle_pause()
        elif action == "stop":
            from local_mode.embedding_jobs import cancel_all
            cancel_all()
            self.text_provider.cancel()
            self.shared_agent.cancel_current()
            from agent.task_queue import cancel_existing
            cancel_existing()
            self.event("tasks.cleared", {})
            self.emit("agent.stop", {})
            self.speaker.cancel()
        elif action == "speech_stop":
            self.speaker.cancel()
        elif action == "mute":
            self.muted = not self.muted
            self.cycle.invalidate_capture()
        elif action == "voice":
            self.silent_mode = not self.silent_mode
            if self.silent_mode:
                self.speaker.cancel()
        elif action == "listen":
            self.submit("chat", {"text": "__local_listen__"})
        elif action == "wake":
            return self.services.ui_call("voice.activation", {"enabled": not self.wake_enabled})
        else:
            raise ValueError("Unknown control action.")
        return {"ok": True}

    def sync_conversation(self, params):
        import math
        with self.shared_agent.lock:
            if not self.shared_agent.attached or params.get("owner") != self.shared_agent.owner or params.get("session") != self.shared_agent.session:
                return {"ok": False, "stale": True}
        items = params.get("items")
        if not isinstance(items, list) or len(items) > 200 or len(json.dumps(items, ensure_ascii=False).encode()) > 50000:
            raise ValueError("Invalid conversation projection")
        cleaned = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"id", "role", "text", "time"} or item["role"] not in {"user", "assistant"} or not isinstance(item["id"], str) or len(item["id"]) > 256 or not isinstance(item["text"], str) or len(item["text"]) > 12000 or (type(item["time"]) not in {int, float} or not math.isfinite(item["time"])):
                raise ValueError("Invalid conversation message")
            cleaned.append(dict(item))
        with self.shared_agent.lock, self.lock:
            if params.get("owner") != self.shared_agent.owner or params.get("session") != self.shared_agent.session or not self.shared_agent.attached:
                return {"ok": False, "stale": True}
            journal = [m for m in self.messages if m["role"] not in {"user", "assistant"}]
            self.messages.clear()
            self.messages.extend(sorted(journal + cleaned, key=lambda m:m["time"])[-200:])
            projected = self._snapshot_messages()
        self.event("history.replace", {"items": projected})
        return {"ok": True}

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "settings.locale":
            if set(params) != {"locale"} or not isinstance(params["locale"], str) or params["locale"] not in {"en", "ru"}:
                raise ValueError("Unsupported interface language.")
            with self.lock:
                self.interface_locale = params["locale"]
            return {"ok": True}
        if method == "screen.capture":
            from local_mode.screen_capture import capture_screen
            if set(params) - {"monitor", "destination"}:
                from local_mode.messages import OwnedValueError
                raise OwnedValueError("Unexpected screen capture arguments.")
            return capture_screen(**params)
        if not hasattr(self, "services"):
            from local_mode.services import ServiceRegistry
            self.services = ServiceRegistry(self, lambda: self.bot.tools if getattr(self, "bot", None) else None)
        if method == "embedding.cancel":
            from local_mode.embedding_jobs import cancel
            return cancel(params.get("request_id"))
        if method in self.services.aliases:
            return self.services.ui_call(method, params)
        if method.startswith("managed_chat."):
            from local_mode.managed_chat import manager
            from local_mode.managed_rpc import dispatch
            return dispatch(method, params, manager)
        if method.startswith("llama."):
            from local_mode.local_chat import runner
            if method == "llama.status":
                return runner.status()
            if method == "llama.start":
                return runner.start(**params)
            if method == "llama.stop":
                return runner.stop()
            raise ValueError("Unknown llama.cpp operation")
        if method == "services.read":
            name,args=params.get("name"),params.get("args",{})
            self.services.validate(name, args)
            if name not in self.services.services or self.services.risky(name,args):
                raise ValueError("This service requires an approved action")
            return self.services.execute(name,args)
        if method == "services.register_workspace":
            result = self.services.register_workspace(params.get("items"))
            from agent.task_queue import existing_queue
            if queue := existing_queue():
                queue.invalidate_ui_reads(self.services)
            return result
        if method == "services.catalog":
            return self.services.catalog(**params)
        if method == "conversation.sync":
            return self.sync_conversation(params)
        if method == "agent.ui_speech":
            with self.shared_agent.lock:
                if not self.shared_agent.attached or params.get("owner") != self.shared_agent.owner or params.get("session") != self.shared_agent.session:
                    return {"ok": False, "stale": True}
                run = params.get("run")
                if not isinstance(run, str) or not 1 <= len(run) <= 128:
                    raise ValueError("Invalid speech run")
                if params.get("phase") == "begin":
                    self.speaker.cancel()
                    self._ui_speech = (self.shared_agent.owner, run, self.speaker.cancel_revision)
                    return {"ok": True}
                lease = getattr(self, "_ui_speech", None)
                if not lease or lease[:2] != (self.shared_agent.owner, run):
                    return {"ok": False, "stale": True}
                text = params.get("text", "")
                if not isinstance(text, str) or len(text) > 12000:
                    raise ValueError("Invalid speech text")
                self._ui_speech = None
                if text and not self.silent_mode and not self.current_request:
                    self.speaker.say_if_current(text, lease[2])
                return {"ok": True}
        if method == "agent.activity":
            if params.get("owner", "") != self.shared_agent.owner:
                return {"ok": False, "stale": True}
            busy = params.get("busy")
            if type(busy) is not bool:
                raise ValueError("Invalid agent activity")
            self.shared_busy = busy
            if busy:
                self.cycle.invalidate_capture()
                self.cycle.set_phase("CONFIRMATION" if params.get("approval") else "PROCESSING")
            elif not self.current_request:
                self.cycle.set_phase("ONLINE")
            return {"ok": True}
        if method == "agent.detach":
            if self.shared_agent.detach(params.get("owner")):
                self.shared_busy = False
                self.text_provider.detach()
                from agent.task_queue import existing_queue
                if queue := existing_queue():
                    queue.invalidate_ui_reads(self.services)
            return {"ok": True}
        if method == "agent.attach":
            self.shared_agent.attach(params.get("model", ""), params.get("session", ""), params.get("owner", ""), params.get("workspace"))
            self.text_provider.attach(params.get("owner", ""), params.get("session", ""), params.get("route", ""))
            from agent.task_queue import existing_queue
            if queue := existing_queue():
                queue.invalidate_ui_reads(self.services)
            return {"ok": True}
        if method == "helper.text.complete":
            return self.text_provider.complete(params)
        if method in QUEUED_UI_METHODS:
            from agent.task_queue import existing_queue
            queue = existing_queue()
            if method == "tasks.ui.pending":
                return queue.ui_reads(self.services, params.get("owner"), params.get("session"), params.get("route"), params.get("workspace")) if queue else []
            if queue is None:
                from local_mode.messages import OwnedValueError
                raise OwnedValueError("Expired or changed queued task approval")
            if method == "tasks.ui.check":
                return queue.check_ui_read(params, self.services)
            if method == "tasks.ui.start":
                return queue.start_ui_dispatch(params, self.services)
            if method == "tasks.ui.settled":
                return queue.settled_ui_dispatch(params, self.services)
            if method == "tasks.ui.orphaned":
                return queue.orphaned_ui_dispatches(self.services)
            return (queue.claim_ui_read(params, self.services) if method == "tasks.ui.claim"
                    else queue.complete_ui_read(params, self.services))
        if method == "agent.complete":
            self.shared_agent.complete(params.get("id"), params.get("text", ""), params.get("error"))
            self.connected = not bool(params.get("error"))
            return {"ok": True}
        if method == "agent.catalog":
            return list(self.services.descriptors)
        if method in {"agent.prepare", "agent.execute"}:
            if not self.bot or not self.shared_agent.attached:
                raise ValueError("Shared agent is not ready")
            if method == "agent.prepare":
                if params.get("name") == "api_add_key":
                    raise ValueError("Use trusted key settings")
                return self.shared_agent.prepare(self.services, params.get("name"), params.get("args", {}))
            result = self.shared_agent.execute(self.services, params.get("token"))
            self.wake_enabled = self.bot.tools.wake_enabled
            return result
        if method == "snapshot":
            return self.snapshot()
        if method == "speech.recognize":
            return self.submit(method, params).result(timeout=90)
        if method == "chat":
            self.submit(method, params)
            return {"ok": True}
        if method == "control":
            return self.control(params.get("action", ""))
        if method == "history.clear":
            with self.lock:
                self.messages.clear()
            self.event("history.clear", {})
            return {"ok": True}
        if method == "workbench.bootstrap":
            from local_mode.companions import workbench_environment

            environment = workbench_environment()
            return {
                "base": environment["JARVIS_AI_BASE"],
                "token": environment["JARVIS_AI_TOKEN"],
                "model": environment["JARVIS_AI_MODEL"],
                "test": os.environ.get("JARVIS_DESKTOP_TEST") == "1",
            }
        if method == "chrome.token":
            from local_mode.chrome_credentials import save_token
            from local_mode.browser_profiles import profiles

            profile = params.get("profile", "")
            if profile not in {
                item["id"] for item in profiles(self.settings.browser_channel)[0]
            }:
                raise ValueError("Select an existing profile.")
            save_token(profile, params.get("token", ""), self.settings.browser_channel)
            return {"ok": True}
        if method == "network.check":
            from local_mode.network.diagnostics import check_connection, check_model
            from local_mode.network.network_preferences import read_network_preferences

            options = read_network_preferences()
            return (check_model if params.get("model") else check_connection)(**options)
        if method == "capabilities":
            from local_mode.tools import DECLARATIONS

            return [
                {
                    "id": item["name"],
                    "name": tool_label(item["name"]),
                }
                for item in self.services.all_descriptors()
            ]
        if method in {
            "settings.key",
            "settings.microphone",
            "settings.models",
            "settings.network",
            "browser.action",
            "camera.command",
            "tool",
        }:
            return self.submit(method, params).result(timeout=180)
        raise ValueError("Unknown application method.")

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._safe_worker, name="Jarvis-actions", daemon=True
        )
        self._thread.start()
        threading.Thread(
            target=self._heartbeat, name="Jarvis-state", daemon=True
        ).start()
        if os.environ.get("JARVIS_DESKTOP_TEST") != "1":
            self.speaker.prepare()

    def _heartbeat(self) -> None:
        previous = None
        while not self.stop.wait(0.05):
            state = self.cycle.display_state(self.muted, self.confirmation)
            current = (
                state,
                self.muted,
                self.silent_mode,
                self.wake_enabled,
                self.connected,
                self.speaker.voice,
                self.shared_agent.model if self.shared_agent.attached else self.settings.model,
            )
            if current != previous:
                self.event(
                    "state",
                    {
                        "state": state,
                        "label": state_label(state),
                        "muted": self.muted,
                        "silent": self.silent_mode,
                        "wake": self.wake_enabled,
                        "connected": self.connected,
                        "voice_label": self.speaker.label,
                        "voice": self.speaker.voice,
                        "active_model": self.shared_agent.model if self.shared_agent.attached else self.settings.model,
                    },
                )
                previous = current
            level = self.speaker.audio_level
            if level > 0 or getattr(self, "_previous_level", 0) > 0:
                self.event("audio.level", {"level": level})
            self._previous_level = level

    def _on_text(self, text: str) -> None:
        self.stream = text
        if self.streaming_voice:
            self.streaming_voice.feed(text)
        now = time.monotonic()
        if not text or now - self._last_text_at > 0.04:
            self.event("stream", {"text": text})
            self._last_text_at = now

    def _safe_worker(self) -> None:
        try:
            self._worker()
        except Exception as error:
            self.failed.set()
            self._drain_pending()
            self.write_log("ERR: Backend failed to start: " + safe_error(error))
            self.cycle.set_phase("OFFLINE")
            self.speaker.close()

    def _recover_network(self, error: Exception) -> None:
        import httpx
        from local_mode.network.network_transport import NetworkRouteError

        if os.environ.get("JARVIS_DESKTOP_TEST") == "1" or not isinstance(
            error, (NetworkRouteError, httpx.TransportError)
        ):
            return
        if self.network_thread is not None and self.network_thread.is_alive():
            return
        from local_mode.network.startup import start_network_check

        self.network_thread = start_network_check(
            self.stop,
            lambda text: self.write_log("SYS: " + user_message({"ok": True, "reply": text}, locale=self.interface_locale)),
            lambda ok: setattr(self, "connected", ok),
            initialize=False,
        )

    def _worker(self) -> None:
        from local_mode.assistant import LocalAgent
        from local_mode.speech import RussianRecognizer, voice_command
        from local_mode.windows import WindowController
        from local_mode.remote import RemoteServices
        from local_mode.network.startup import start_network_check

        try:
            self.bot = bot = LocalAgent(
                self.settings, self.stop, player=self, speak=self.report_background
            )
            bot.tools.wake_enabled = self.wake_enabled
            bot.tools.special["set_voice"] = lambda args: self.speaker.select(
                args["voice"]
            )
            bot.on_text = self._on_text

            def model_state(phase):
                if phase == "PROCESSING" and self.streaming_voice:
                    self.streaming_voice.reset()
                self.cycle.set_phase(phase)

            bot.on_state = model_state
            bot.progress = lambda text: self.write_log("SYS: " + user_message({"ok": True, "reply": text}, locale=self.interface_locale))
            if os.environ.get("JARVIS_DESKTOP_TEST") != "1":
                bot.background.start()
            self.remote = RemoteServices(
                self,
                lambda command, **kw: self.submit(
                    "chat", {"text": command}, kw.get("source", "remote")
                ),
                self.stop,
                self.write_log,
            )
            if os.environ.get("JARVIS_DESKTOP_TEST") != "1":
                self.remote.start()
            if os.environ.get("JARVIS_DESKTOP_TEST") != "1":
                self.network_thread = start_network_check(
                    self.stop,
                    lambda text: self.write_log("SYS: " + user_message({"ok": True, "reply": text}, locale=self.interface_locale)),
                    lambda ok: setattr(self, "connected", ok),
                )
            windows = WindowController()
            recognizer = None

            def capture_command():
                nonlocal recognizer
                if self.muted:
                    raise ValueError(
                        "Enable the Jarvis microphone in the bottom bar first."
                    )
                while self.speaker.microphone_blocked and not self.stop.wait(0.05):
                    pass
                recognizer = recognizer or RussianRecognizer(
                    self.settings.vosk_model, self.settings.microphone
                )
                token = self.cycle.capture_token()
                return recognizer.listen(
                    self.stop,
                    interrupted=lambda: self.cycle.capture_interrupted(
                        token, self.muted
                    ),
                    on_state=self.cycle.set_phase,
                )

            while not self.stop.is_set():
                window = windows.foreground()
                if window and "Jarvis" not in window.title:
                    self.target = window
                queued = True
                try:
                    item = self.work.get(timeout=0.2)
                except queue.Empty:
                    if self.shared_busy or not self.cycle.can_listen(self.muted):
                        continue
                    token = self.cycle.capture_token()
                    try:
                        recognizer = recognizer or RussianRecognizer(
                            self.settings.vosk_model, self.settings.microphone
                        )
                        heard = recognizer.listen(
                            self.stop,
                            timeout=3,
                            interrupted=lambda: self.cycle.capture_interrupted(
                                token, self.muted
                            )
                            or not self.work.empty() or self.shared_busy,
                            on_state=self.cycle.set_phase,
                        )
                    except Exception as error:
                        self.muted = True
                        self.write_log("ERR: Microphone disconnected: " + safe_error(error))
                        continue
                    finally:
                        self.cycle.set_phase("ONLINE")
                    if (
                        not heard
                        or self.cycle.capture_interrupted(token, self.muted)
                        or not self.work.empty()
                    ):
                        continue
                    heard = voice_command(heard, self.wake_enabled)
                    if not heard:
                        continue
                    queued = False
                    item = Work("chat", {"text": heard}, Future(), uuid4().hex)
                self.current_request = item.request_id
                try:
                    self.cycle.set_phase("PROCESSING")
                    bot.tools.source = item.source
                    if item.method == "speech.recognize":
                        recognized = capture_command()
                        result = {
                            "ok": bool(recognized),
                            "text": recognized or "",
                            "cancelled": not bool(recognized),
                        }
                    elif item.method == "chat":
                        command = item.params["text"]
                        if command == "__local_listen__":
                            command = capture_command()
                            if not command:
                                raise ValueError("Speech was not recognized.")
                        if command == "__toggle_wake__":
                            command = (
                                "выключи голосовую активацию"
                                if self.wake_enabled
                                else "включи голосовую активацию"
                            )
                        if not self.shared_agent.attached:
                            self.write_log("You: " + command)
                        self.speaker.cancel()
                        self.stream = ""
                        from local_mode.streaming_speech import SpeechStream

                        self.streaming_voice = SpeechStream(
                            self.speaker, lambda: not self.silent_mode, self.stop
                        )
                        from local_mode.shared_agent import SharedAgentCancelled
                        try:
                            result = (
                                self.shared_agent.run(command, self.stop)
                                if self.shared_agent.attached and not self.confirmation
                                else bot.run(command, self.target, source=item.source)
                            )
                        except SharedAgentCancelled:
                            self.streaming_voice.abort()
                            result = {"ok": True, "cancelled": True, "reply": ""}
                        self.wake_enabled = bot.tools.wake_enabled
                        self.confirmation = bool(result.get("requires_confirmation"))
                        self.stream = ""
                        self.event("stream", {"text": ""})
                        if not self.shared_agent.attached:
                            self.write_log("AI: " + user_message(result, locale=self.interface_locale))
                        self.event(
                            "result",
                            {
                                "result": {
                                    key: result[key]
                                    for key in (
                                        "requires_confirmation",
                                        "requires_setup",
                                        "setup_kind",
                                        "profile_id",
                                    )
                                    if key in result
                                }
                            },
                        )
                        self.streaming_voice.finish(result, locale=self.interface_locale)
                        self.streaming_voice = None
                    elif item.method == "settings.key":
                        from local_mode.credentials import save_key

                        key = item.params.get("key")
                        if (
                            not isinstance(key, str)
                            or not 20 <= len(key) <= 4096
                            or any(char.isspace() for char in key)
                        ):
                            raise ValueError("Enter an API key without spaces.")
                        save_key(key)
                        os.environ["GEMINI_API_KEY"] = key
                        if bot.client:
                            bot.client.close()
                        bot.client = None
                        bot.turns.clear()
                        result = {"ok": True, "message": OwnedText("The key is protected by Windows DPAPI.")}
                    elif item.method == "settings.microphone":
                        index = item.params.get("microphone")
                        devices = self.dispatch("audio.devices", {})
                        if index is not None and (
                            type(index) is not int
                            or index not in {item["id"] for item in devices}
                        ):
                            raise ValueError("Microphone is unavailable.")
                        path = ROOT / "data/preferences.json"
                        import json

                        values = (
                            json.loads(path.read_text(encoding="utf-8-sig"))
                            if path.exists()
                            else {}
                        )
                        values["microphone"] = index
                        write_json(path, values)
                        self.settings = Settings.model_validate(
                            {**self.settings.model_dump(), "microphone": index}
                        )
                        recognizer = None
                        self.cycle.invalidate_capture()
                        result = {"ok": True, "message": OwnedText("Microphone selected.")}
                    elif item.method == "settings.models":
                        self.settings = bot.configure_models(
                            item.params.get("model", ""),
                            item.params.get("auto_fallback"),
                        )
                        result = {"ok": True}
                    elif item.method == "settings.network":
                        result = bot.configure_network(
                            item.params.get("mode", ""), item.params.get("proxy", "")
                        )
                    elif item.method == "browser.action":
                        result = bot.tools.execute("browser_control", item.params)
                    elif item.method == "camera.command":
                        args = dict(item.params)
                        preview = args.pop("preview", None)
                        if preview is not None:
                            self.preview = bool(preview)
                        result = bot.tools.execute("vision_gesture", args)
                    elif item.method == "tool":
                        result = bot.tools.execute(
                            item.params.get("name", ""), item.params.get("args", {})
                        )
                    else:
                        raise ValueError("Unknown task.")
                    if (
                        item.method != "chat"
                        and isinstance(result, dict)
                        and result.get("requires_confirmation")
                    ):
                        self.confirmation = True
                        self.write_log("AI: " + user_message(result, locale=self.interface_locale))
                        self.event(
                            "result",
                            {
                                "result": {
                                    key: result[key]
                                    for key in (
                                        "requires_confirmation",
                                        "requires_setup",
                                        "setup_kind",
                                        "profile_id",
                                    )
                                    if key in result
                                }
                            },
                        )
                    if not item.future.done():
                        item.future.set_result(result)
                except Exception as error:
                    if self.streaming_voice:
                        self.streaming_voice.abort()
                        self.streaming_voice = None
                    self.stream = ""
                    self.event("stream", {"text": ""})
                    error_text = self.report_worker_error(error)
                    if self.shared_agent.attached:
                        self.connected = False
                    else:
                        self._recover_network(error)
                    if not item.future.done():
                        if isinstance(error, OwnedError):
                            # Log in the selected locale; retain a sanitized template
                            # in RPC so an open settings panel can change language.
                            detail = safe_error(error)
                            failure_type = OwnedValueError if isinstance(error, ValueError) else OwnedRuntimeError
                            item.future.set_exception(failure_type(detail.template, *detail.values))
                        else:
                            item.future.set_exception(ValueError(error_text))
                finally:
                    self.connected = (
                        bool(bot.model_connected)
                        if bot.model_connected is not None and not self.shared_agent.attached
                        else self.connected
                    )
                    self.current_request = ""
                    self.cycle.set_phase("ONLINE")
                    self.work.task_done() if queued else None
        except Exception as error:
            self.failed.set()
            self.write_log("ERR: Backend failed to start: " + safe_error(error))
            self.cycle.set_phase("OFFLINE")
        finally:
            self.failed.set()
            self._drain_pending()
            if self.remote:
                self.remote.close()
            if self.bot:
                self.bot.close()
            self.speaker.close()

    def _drain_pending(self) -> None:
        while True:
            try:
                item = self.work.get_nowait()
            except queue.Empty:
                break
            if not item.future.done():
                item.future.set_exception(RuntimeError("The backend has stopped."))
            self.work.task_done()

    def close(self) -> None:
        self.text_provider.detach()
        self.shared_agent.cancel_current()
        from agent.task_queue import cancel_existing
        cancel_existing(stop=True)
        from local_mode.embedding_jobs import cancel_all
        cancel_all()
        from local_mode.managed_chat import manager
        manager.stop()
        from local_mode.local_chat import runner
        runner.stop()
        from local_mode.companions import close
        close()
        from local_mode.local_embeddings import _runner
        _runner.close()
        self.stop.set()
        self.speaker.cancel()
        self._drain_pending()
        if self._thread:
            self._thread.join(timeout=8)
        if self._thread and self._thread.is_alive():
            self.speaker.close()


def _serve() -> int:
    # stdout — только протокол. Любые print сторонних библиотек уходят в stderr.
    output = sys.stdout
    lock = threading.Lock()

    def send(payload):
        with lock:
            output.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
            output.flush()

    with contextlib.redirect_stdout(sys.stderr):
        runtime = DesktopRuntime(
            lambda event, data: send({"version": 1, "event": event, "data": data})
        )
        from local_mode.camera_devices import select_camera

        select_camera(None)
        runtime.start()
        slots = threading.BoundedSemaphore(16)
        executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="Jarvis-rpc")
        model_slots = threading.BoundedSemaphore(2)
        model_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Jarvis-model-setup")
        from local_mode import embedding_jobs

        def handle(request, release=True, generation=None, admission=None):
            try:
                with embedding_jobs.accepted(generation):
                    result = runtime.dispatch(request.method, request.params)
                send(
                    {
                        "version": 1,
                        "id": request.id,
                        "result": present_result(result, locale=runtime.interface_locale) if isinstance(result, dict) else result,
                    }
                )
            except Exception as error:
                send({"version": 1, "id": request.id, "error": error.payload() if isinstance(error, OwnedError) else safe_error(error)})
            finally:
                if release:
                    (admission or slots).release()

        try:
            while True:
                line = sys.stdin.buffer.readline(65538)
                if not line:
                    break
                request = None
                try:
                    if len(line) > 65536:
                        send(
                            {"version": 1, "id": -1, "error": "Request is too large."}
                        )
                        break
                    request = Envelope.model_validate_json(line)
                    if request.method in PRIORITY_METHODS:
                        handle(request, False)
                    elif request.method in {"managed_chat.list", "managed_chat.resolve", "llama.status", "llama.start"}:
                        if not model_slots.acquire(blocking=False):
                            raise ValueError("The model setup queue is full.")
                        model_executor.submit(handle, request, True, embedding_jobs.generation(), model_slots)
                    elif not slots.acquire(blocking=False):
                        raise ValueError("The application queue is full.")
                    else:
                        executor.submit(handle, request, True, embedding_jobs.generation())
                except Exception:
                    send(
                        {
                            "version": 1,
                            "id": request.id if request is not None else -1,
                            "error": "Invalid request or too many requests.",
                        }
                    )
        finally:
            runtime.close()
            executor.shutdown(wait=False, cancel_futures=True)
            model_executor.shutdown(wait=False, cancel_futures=True)
    return 0


def main() -> int:
    from local_mode.instance import SingleInstance

    with SingleInstance(
        ROOT / ".cache/desktop-test"
        if os.environ.get("JARVIS_DESKTOP_TEST") == "1"
        else ROOT
    ) as acquired:
        if not acquired:
            sys.stdout.write(
                json.dumps(
                    {
                        "version": 1,
                        "event": "backend.error",
                        "data": {
                            "text": "Jarvis is already running. Close the existing instance."
                        },
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
            return 2
        return _serve()


if __name__ == "__main__":
    raise SystemExit(main())
