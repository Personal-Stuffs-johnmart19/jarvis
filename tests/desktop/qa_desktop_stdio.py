"""Мост тестируется через настоящий stdio, без GUI и сетевых действий."""

import json, os, queue, subprocess, threading, time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
env = {**os.environ, "JARVIS_DESKTOP_TEST": "1", "PYTHONIOENCODING": "utf-8"}
process = subprocess.Popen(
    [
        str(root / ".venv/Scripts/python.exe"),
        "-u",
        "-X",
        "utf8",
        "-m",
        "local_mode.desktop_host",
    ],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    encoding="utf-8",
    env=env,
    cwd=root,
    creationflags=0x08000000,
)
lines = queue.Queue()


def read():
    for line in process.stdout:
        try:
            lines.put(json.loads(line))
        except ValueError:
            raise AssertionError("Non-JSON stdout")


threading.Thread(target=read, daemon=True).start()


def request(number, method, params=None):
    process.stdin.write(
        json.dumps(
            {"version": 1, "id": number, "method": method, "params": params or {}},
            ensure_ascii=False,
        )
        + "\n"
    )
    process.stdin.flush()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        value = lines.get(timeout=30)
        if value.get("id") == number:
            assert "error" not in value, value
            return value["result"]


try:
    snapshot = request(1, "snapshot")
    assert snapshot["muted"] and snapshot["silent"]
    assert "token" not in snapshot and "api_key" not in snapshot
    assert len(request(2, "capabilities")) == 53
    hints = request(3, "suggestions.get")
    assert isinstance(hints, list)
    assert request(4, "chat", {"text": "который сейчас час"})["ok"]
    deadline = time.monotonic() + 15
    found = False
    while time.monotonic() < deadline:
        event = lines.get(timeout=15)
        if event.get("event") == "message" and event["data"]["role"] == "assistant":
            assert "Сейчас" in event["data"]["text"]
            found = True
            break
    assert found
    print(
        "PASS: real Python stdio -> snapshot, 53 tools, saved hints, native chat reply; secrets withheld."
    )
finally:
    process.stdin.close()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        raise AssertionError("Backend did not stop after EOF")
    assert process.returncode == 0, process.stderr.read()[-1000:]
