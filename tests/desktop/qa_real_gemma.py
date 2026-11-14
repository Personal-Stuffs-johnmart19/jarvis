"""Real LM Studio / Gemma end-to-end QA, with a transparent localhost observer.

The model response is forwarded unchanged. Only aggregate protocol/tool names
are reported; request contents, API keys and model reasoning are not logged.
"""

import json
import os
import runpy
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

requests = 0
completed_tools = set()
provider_errors = []


class Observer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        global requests
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.command == "POST":
            payload = json.loads(body)
            assert payload["model"] == "google/gemma-4-e4b"
            assert all(
                t["function"]["parameters"].get("type") == "object"
                for t in payload.get("tools", [])
            )
            requests += 1
            if requests > 1:
                provider_errors.append(429)
                self.send_response(429)
                self.send_header("Content-Type","application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"Single-request smoke budget exhausted"}}')
                return
            names = {
                c["id"]: c["function"]["name"]
                for m in payload.get("messages", [])
                for c in m.get("tool_calls", [])
            }
            for message in payload.get("messages", []):
                if message["role"] == "tool" and message.get("tool_call_id") in names:
                    completed_tools.add(names[message["tool_call_id"]])
        try:
            with (
                httpx.Client(timeout=180, trust_env=False) as client,
                client.stream(
                    self.command,
                    "http://127.0.0.1:1234" + self.path,
                    content=body,
                    headers={"Content-Type": "application/json"},
                ) as response,
            ):
                self.send_response(response.status_code)
                self.send_header(
                    "Content-Type",
                    response.headers.get("Content-Type", "application/json"),
                )
                self.end_headers()
                if response.status_code >= 400:
                    provider_errors.append(response.status_code)
                for chunk in response.iter_bytes():
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == "__main__":
    models = httpx.get(
        "http://127.0.0.1:1234/api/v1/models", timeout=10, trust_env=False
    )
    models.raise_for_status()
    assert any(m["key"] == "google/gemma-4-e4b" for m in models.json()["models"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Observer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["JARVIS_QA_MODEL_BASE"] = f"http://127.0.0.1:{server.server_port}/v1"
    os.environ["JARVIS_QA_SMOKE"] = "1"
    try:
        runpy.run_path(
            str(Path(__file__).with_name("qa_shared_dialog.py")), run_name="__main__"
        )
        assert requests == 1, requests
        assert not provider_errors, provider_errors
        print(
            json.dumps(
                {
                    "real_gemma": True,
                    "model_requests": requests,
                    "completed_tools": sorted(completed_tools),
                    "provider_errors": provider_errors,
                }
            )
        )
    finally:
        server.shutdown()
        server.server_close()
