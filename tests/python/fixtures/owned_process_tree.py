"""Owned process-tree fixture: reads a parent grant before creating descendants."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def spawn(mode, folder):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen([sys.executable, __file__, mode, str(folder)], creationflags=flags)


if len(sys.argv) > 1:
    mode, folder = sys.argv[1], Path(sys.argv[2])
    (folder / (mode + ".pid")).write_text(str(os.getpid()), encoding="utf-8")
    if mode == "child":
        spawn("grandchild", folder)
    time.sleep(30)
else:
    request = json.loads(sys.stdin.buffer.readline())
    folder = Path(request["folder"])
    (folder / "root.pid").write_text(str(os.getpid()), encoding="utf-8")
    spawn("child", folder)
    deadline = time.monotonic() + 5
    while not (folder / "grandchild.pid").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Fixture descendants did not start")
        time.sleep(0.01)
    print(json.dumps({"event": "ready"}), flush=True)
    if request["mode"] == "relay":
        print(json.dumps({"event": "text_request", "request_id": "fixture", "request": {"prompt": "synthetic"}}), flush=True)
        reply = json.loads(sys.stdin.buffer.readline())
        print(json.dumps({"event": "result", "text": reply["response"]["text"]}), flush=True)
    elif request["mode"] == "overflow":
        print("x" * 4096, flush=True)
        time.sleep(30)
    elif request["mode"] == "success":
        print(json.dumps({"event": "result", "ok": True}), flush=True)
    else:
        time.sleep(30)
