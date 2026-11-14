"""Three bounded offline integration regressions; no real desktop runtime starts."""
import json
import os
from pathlib import Path
import sys

import psutil
import pytest

from local_mode.messages import OwnedRuntimeError
from local_mode.owned_process import run_owned
from local_mode.text_helpers import SharedTextProvider


def test_stop_permanently_revokes_old_binding_but_new_same_identity_binding_works():
    events = []
    broker = None
    def emit(name, data):
        events.append((name, data))
        if name == "helper.text.request":
            broker.complete({key: data[key] for key in ("id", "owner", "session", "route")} | {"text": "fixture response"})
    broker = SharedTextProvider(emit)
    broker.attach("owner", "session", "route")
    old = broker.binding()
    broker.cancel()
    for _ in range(2):
        with pytest.raises(OwnedRuntimeError, match="stopped"):
            old({"prompt": "old handler must not start work after Stop"})
    assert events == []
    broker.attach("owner", "session", "route")
    assert broker.binding()({"prompt": "new explicit task"}) == {"text": "fixture response"}
    with pytest.raises(OwnedRuntimeError, match="stopped"):
        old({"prompt": "still revoked"})
    assert sum(name == "helper.text.request" for name, _ in events) == 1


def envelope(identifier, method, params=None):
    return json.dumps({"version": 1, "id": identifier, "method": method, "params": params or {}}).encode()


@pytest.mark.parametrize("block_model", [False, True])
def test_real_stdio_fast_and_reserved_lanes_bypass_four_saturated_workers(block_model):
    root = Path(__file__).resolve().parents[2]
    fixture = Path(__file__).with_name("fixtures") / "desktop_stdio_pool.py"
    replies = {}
    events = []
    sent = set()
    process_pid = None

    def batch(*requests):
        return b"\n".join(requests)

    def on_line(line):
        nonlocal process_pid
        record = json.loads(line)
        if "event" in record:
            events.append(record)
            if record["event"] == "fixture.ready":
                process_pid = record["data"]["pid"]
                return batch(*(envelope(slot, "fixture.block", {"slot": slot}) for slot in range(4)))
            if record["event"] == "fixture.saturated":
                if block_model:
                    return batch(envelope(13, "managed_chat.resolve", {"fixture_block": True}), envelope(12, "managed_chat.list"))
                return batch(envelope(10, "helper.text.complete"), envelope(11, "workbench.bootstrap"),
                             envelope(12, "managed_chat.list"), envelope(13, "managed_chat.resolve"),
                             envelope(14, "control", {"action": "probe"}))
            if record["event"] == "fixture.model_blocked":
                return batch(envelope(10, "helper.text.complete"), envelope(11, "workbench.bootstrap"),
                             envelope(14, "control", {"action": "probe"}))
            return None
        assert "error" not in record, record
        replies[record["id"]] = record["result"]
        if block_model and {10, 11, 14} <= replies.keys() and "model" not in sent:
            sent.add("model")
            return envelope(17, "control", {"action": "release_model"})
        if {10, 11, 12, 13, 14} <= replies.keys() and "general" not in sent:
            for identifier in (10, 11, 12, 13, 14):
                assert replies[identifier]["before_general_release"] is True
                assert replies[identifier]["active_general"] == 4
            assert all(replies[identifier]["thread"].startswith("Jarvis-model-setup") for identifier in (12, 13))
            assert all(replies[identifier]["thread"] == "MainThread" for identifier in (10, 11, 14))
            sent.add("general")
            return envelope(15, "control", {"action": "release_general"})
        if {0, 1, 2, 3, 15} <= replies.keys() and "finish" not in sent:
            assert all(replies[slot]["released"] for slot in range(4))
            sent.add("finish")
            return envelope(16, "control", {"action": "finish"})
        return None

    environment = dict(os.environ)
    environment.update({"PYTHONPATH": str(root) + os.pathsep + environment.get("PYTHONPATH", ""),
                        "JARVIS_DESKTOP_TEST": "1", "JARVIS_QA_ALLOW_CLOUD": "0"})
    result = run_owned([sys.executable, "-X", "utf8", str(fixture)], payload=b"{}", timeout=10,
                       cwd=root, env=environment, on_line=on_line)
    assert result.returncode == 0
    assert 16 in replies
    closed = next(record["data"] for record in events if record["event"] == "fixture.closed")
    assert closed == {"active_general": 0, "camera_calls": [None]}
    assert process_pid is not None and not psutil.pid_exists(process_pid)
