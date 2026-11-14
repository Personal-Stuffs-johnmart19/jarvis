"""Приватный Gemini REST/SSE мост для окон разработки/браузера.

Ключ Google остаётся в Python; native-компоненты получают только случайный
loopback token. Внешние запросы проходят существующие SDK/Tenacity/маршрут/usage.
"""

from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

import httpx
from google.genai import types
from google.genai.errors import APIError
from local_mode import gemini
from local_mode.config import Settings
from local_mode.models import MODEL_ORDER

MAX_BODY = 16 * 1024 * 1024
FIRST_RESPONSE_SECONDS = 60
POLL_SECONDS = 0.1
HEARTBEAT_SECONDS = 1


def error_payload(code: int, message: str) -> dict:
    """Google REST errors require the canonical string status as well as code."""
    status = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED",
              404: "NOT_FOUND", 408: "DEADLINE_EXCEEDED", 409: "ABORTED",
              429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 501: "UNIMPLEMENTED",
              502: "UNAVAILABLE", 503: "UNAVAILABLE", 504: "DEADLINE_EXCEEDED"}.get(
                  code, "INVALID_ARGUMENT" if 400 <= code < 500 else "INTERNAL")
    return {"error": {"code": code, "message": str(message), "status": status}}


def parse_request(
    body: dict,
) -> tuple[list[types.Content], types.GenerateContentConfig]:
    if not isinstance(body, dict) or not isinstance(body.get("contents"), list):
        raise ValueError("Нужен массив Gemini contents.")
    contents = [types.Content.model_validate(item) for item in body["contents"]]
    if not contents or len(contents) > 256:
        raise ValueError("Неверное число сообщений Gemini.")
    options = dict(body.get("generationConfig") or {})
    for name in (
        "systemInstruction",
        "tools",
        "toolConfig",
        "safetySettings",
        "cachedContent",
    ):
        if name in body:
            options[name] = body[name]
    # Cache IDs и server tools принадлежат чужой сессии или отдельному тарифу.
    if "cachedContent" in options:
        raise ValueError("Удалённые cache IDs не передаются общему ключу.")
    config = types.GenerateContentConfig.model_validate(options)
    if any(
        set(tool.model_dump(exclude_none=True)) - {"function_declarations"}
        for tool in config.tools or []
    ):
        raise ValueError(
            "Облачные server tools/grounding отключены. Используйте локальные инструменты компонента."
        )
    if config.max_output_tokens is not None:
        config.max_output_tokens = min(config.max_output_tokens, 16384)
    config.http_options = types.HttpOptions(
        timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)
    )
    return contents, config


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = False


class GeminiBroker:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(48)
        self.slots = threading.BoundedSemaphore(2)
        self._server = _Server(("127.0.0.1", 0), partial(_Handler, self))
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="Jarvis-model-broker", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def environment(self) -> dict[str, str]:
        return {
            "JARVIS_AI_BASE": self.url + "/v1beta",
            "JARVIS_AI_TOKEN": self.token,
            "JARVIS_AI_MODEL": Settings.from_env().model,
        }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "JarvisBroker/1"

    def __init__(self, broker: GeminiBroker, *args, **kwargs):
        self.broker = broker
        self.sent = False
        super().__init__(*args, **kwargs)

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(90)

    def log_message(self, *args) -> None:
        pass  # Ни заголовки/ключи, ни prompts не пишутся в access log.

    def authorized(self) -> bool:
        authorization = self.headers.get("Authorization", "")
        token = (
            authorization.removeprefix("Bearer ")
            if authorization.startswith("Bearer ")
            else self.headers.get("x-goog-api-key", "")
        )
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        return (
            host == f"127.0.0.1:{self.server.server_port}"
            and origin is None
            and token.isascii()
            and secrets.compare_digest(token, self.broker.token)
        )

    def reply(self, status: int, value: dict) -> None:
        if isinstance(value.get("error"), dict):
            value = error_payload(status, value["error"].get("message", "Request failed"))
        data = json.dumps(value, ensure_ascii=False).encode("utf8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler's parse/method errors otherwise return HTML,
        # which hides the original failure behind the Google SDK's parser.
        self.reply(code, error_payload(code, message or "Unsupported broker request"))

    def do_OPTIONS(self) -> None:
        self.reply(
            403,
            {"error": {"code": 403, "message": "Browser origins are not admitted."}},
        )

    def do_GET(self) -> None:
        if not self.authorized():
            self.reply(
                403, {"error": {"code": 403, "message": "Unauthorized local client."}}
            )
            return
        path = urlsplit(self.path).path
        if path == "/_jarvis/info":
            self.reply(
                200,
                {
                    "model": Settings.from_env().model,
                    "models": list(MODEL_ORDER),
                    "protocol": 1,
                },
            )
            return
        if path == "/v1beta/models":
            self.reply(
                200,
                {
                    "models": [
                        {
                            "name": "models/" + model,
                            "displayName": model,
                            "supportedGenerationMethods": ["generateContent"],
                        }
                        for model in MODEL_ORDER
                    ]
                },
            )
            return
        self.reply(
            404, {"error": {"code": 404, "message": "Unsupported local endpoint."}}
        )

    def do_POST(self) -> None:
        if not self.authorized():
            self.reply(
                403, {"error": {"code": 403, "message": "Unauthorized local client."}}
            )
            return
        match = re.fullmatch(
            r"/v1beta/models/([a-z0-9.-]+):(generateContent|streamGenerateContent)",
            urlsplit(self.path).path,
        )
        if match is None or match[1] not in MODEL_ORDER:
            self.reply(
                400,
                {
                    "error": {
                        "code": 400,
                        "message": "Model/operation not enabled in Jarvis Free Tier.",
                    }
                },
            )
            return
        if not self.broker.slots.acquire(blocking=False):
            self.reply(
                429, {"error": {"code": 429, "message": "Jarvis model bridge is busy."}}
            )
            return
        worker_owns_slot = False
        stop = threading.Event()
        clients = []
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked request bodies are not accepted.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Request exceeds the local model input bound.")
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("Incomplete model request.")
            contents, config = parse_request(json.loads(data))
            streaming = match[2] == "streamGenerateContent"
            previous = ""

            def start_stream():
                if not self.sent:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.flush()
                    self.sent = True

            def emit(value: dict) -> None:
                start_stream()
                self.wfile.write(
                    b"data: "
                    + json.dumps(value, ensure_ascii=False).encode("utf8")
                    + b"\n\n"
                )
                self.wfile.flush()

            def on_text(text: str) -> None:
                nonlocal previous
                if not text:
                    if previous:
                        raise RuntimeError(
                            "Gemini stream interrupted; retry this turn."
                        )
                    return
                delta = text[len(previous) :]
                previous = text
                if delta:
                    emit(
                        {
                            "candidates": [
                                {
                                    "index": 0,
                                    "content": {
                                        "role": "model",
                                        "parts": [{"text": delta}],
                                    },
                                }
                            ]
                        }
                    )

            events = queue.Queue(maxsize=32)

            def enqueue(kind, value):
                while not stop.is_set():
                    try:
                        events.put((kind, value), timeout=POLL_SECONDS)
                        return
                    except queue.Full:
                        pass
                raise RuntimeError("Model request cancelled")

            def generate():
                try:
                    with gemini.create_client(retry_attempts=0, stop=stop) as client:
                        clients.append(client)
                        if stop.is_set():
                            return
                        # One caller request is exactly one selected-model SDK
                        # attempt. Legacy router retries/fallback stay elsewhere.
                        response = (gemini.generate_stream(client, match[1], contents, config,
                                    on_text=lambda text: enqueue("text", text), stop=stop, single_attempt=True)
                                    if streaming else gemini.generate(client, match[1], contents, config, single_attempt=True))
                        enqueue("result", response)
                except Exception as error:
                    if not stop.is_set():
                        try:
                            enqueue("error", error)
                        except RuntimeError:
                            pass
                finally:
                    # Cancellation does not falsely advertise a free upstream
                    # slot while a synchronous SDK read is still unwinding.
                    self.broker.slots.release()

            if streaming:
                start_stream()
            worker = threading.Thread(target=generate, name="Jarvis-broker-request", daemon=True)
            worker.start()
            worker_owns_slot = True
            deadline = time.monotonic() + FIRST_RESPONSE_SECONDS
            heartbeat = time.monotonic()
            meaningful = False
            while True:
                if not meaningful and time.monotonic() >= deadline:
                    raise TimeoutError("Gemini did not return a meaningful response within 60 seconds")
                if streaming and time.monotonic() - heartbeat >= HEARTBEAT_SECONDS:
                    # SSE comments keep transport alive; they are not model
                    # text, function calls or usage and never enter saved chat.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    heartbeat = time.monotonic()
                try:
                    kind, value = events.get(timeout=POLL_SECONDS)
                except queue.Empty:
                    continue
                if kind == "error":
                    raise value
                if kind == "result":
                    response = value
                    break
                if value:
                    meaningful = True
                on_text(value)
            result = response.model_dump(mode="json", by_alias=True, exclude_none=True)
            if not streaming:
                self.reply(200, result)
                return
            if previous:
                # Text deltas уже показаны; function calls/signatures/usage идут один раз.
                for candidate in result.get("candidates", []):
                    content = candidate.get("content", {})
                    content["parts"] = [
                        part
                        for part in content.get("parts", [])
                        if "text" not in part or part.get("thought")
                    ]
            emit(result)
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:
            status = (
                error.code
                if isinstance(error, APIError)
                else (504 if isinstance(error, (TimeoutError, httpx.TimeoutException))
                      else 400 if isinstance(error, (ValueError, TypeError)) else 503)
            )
            failure = error_payload(status, gemini.safe_error(error))
            if self.sent:
                try:
                    emit(failure)
                except (OSError, RuntimeError):
                    pass
                self.close_connection = True
            else:
                self.reply(status, failure)
        finally:
            stop.set()
            for client in clients:
                try:
                    client.close()
                except Exception:
                    pass
            if not worker_owns_slot:
                self.broker.slots.release()
