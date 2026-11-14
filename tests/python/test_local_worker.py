"""Offline local-worker permissions and resource limits; no local model needed."""
import io
import json
import time

import pytest

from tools import local_worker as worker


def answer(content="finished", calls=None):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def call(name, arguments, identifier="call-1"):
    return {"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class Response:
    status_code = 200

    def __init__(self, value):
        self.body = io.BytesIO(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.raw = self

    def read1(self, count, decode_content=True):
        return self.body.read(count)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def api(monkeypatch):
    class Session:
        trust_env = True
        replies = []
        requests = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            assert not self.trust_env
            assert kwargs["allow_redirects"] is False
            self.requests.append((url, kwargs))
            assert self.replies, "unexpected extra model request"
            return self.replies.pop(0)
    monkeypatch.setattr(worker.requests, "Session", Session)
    return Session


def bounded(tmp_path, **kwargs):
    result = worker.Worker(tmp_path, receipt=lambda record: None, **kwargs)
    result.deadline = time.monotonic() + 10
    return result


def test_mock_api_exact_edit_and_receipts_do_not_log_task_or_contents(tmp_path, api):
    path = tmp_path / "allowed.txt"
    path.write_bytes(b"secret-value\r\nold\r\n")
    receipts = []
    api.replies = [Response(answer(calls=[call("read_file", {"path": "allowed.txt", "start": 2, "end": 2}),
                                        call("replace_text", {"path": "allowed.txt", "old": "old", "new": "new"}, "call-2")])),
                   Response(answer("secret-value task-secret"))]
    result = worker.Worker(tmp_path, ["allowed.txt"], ["allowed.txt"], receipt=receipts.append).run("task-secret")
    assert path.read_bytes() == b"secret-value\r\nnew\r\n"
    assert result["changed"] == ["allowed.txt"]
    assert "secret-value" not in json.dumps(receipts) and "task-secret" not in json.dumps(receipts)
    assert result["verification"] == "supervisor-required"


@pytest.mark.parametrize("endpoint", ["https://example.com/v1", "http://localhost:1234/v1", "http://127.0.0.1.evil/v1", "http://user:pass@127.0.0.1/v1", "http://127.0.0.1/v1?key=secret"])
def test_no_remote_or_credential_endpoint(tmp_path, endpoint):
    with pytest.raises(ValueError):
        bounded(tmp_path, endpoint=endpoint)


def test_paths_cannot_escape_workspace_or_cross_tool_allowlists(tmp_path):
    (tmp_path / "read.txt").write_text("read-only")
    (tmp_path / "write.txt").write_text("writable")
    instance = bounded(tmp_path, read=["read.txt"], write=["write.txt"])
    with pytest.raises(PermissionError):
        instance.tool("replace_text", {"path": "read.txt", "old": "read", "new": "changed"})
    with pytest.raises(PermissionError):
        instance.tool("read_file", {"path": "write.txt"})
    with pytest.raises(ValueError):
        bounded(tmp_path, read=["../outside.txt"])
    with pytest.raises(PermissionError):
        instance.tool("shell", {"path": "read.txt", "command": "whoami"})


def test_symlink_escape_is_refused_even_when_explicitly_allowlisted(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.txt")
    outside.write_text("outside")
    try:
        (tmp_path / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    try:
        with pytest.raises(ValueError):
            bounded(tmp_path, read=["link.txt"])
    finally:
        outside.unlink()


def test_exact_once_replace_rejects_ambiguous_and_missing_matches(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("duplicate duplicate")
    instance = bounded(tmp_path, write=["file.txt"])
    for old in ("duplicate", "missing", ""):
        with pytest.raises(ValueError):
            instance.tool("replace_text", {"path": "file.txt", "old": old, "new": "changed"})
    assert path.read_text() == "duplicate duplicate"
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("start,end", [(0, 1), (1, 201), (True, 2), (3, 2)])
def test_read_line_bounds(tmp_path, start, end):
    (tmp_path / "file.txt").write_text("one\ntwo\nthree")
    instance = bounded(tmp_path, read=["file.txt"])
    with pytest.raises(ValueError):
        instance.tool("read_file", {"path": "file.txt", "start": start, "end": end})


def test_step_limit_stops_repeated_tool_calls(tmp_path, api):
    (tmp_path / "file.txt").write_text("data")
    api.replies = [Response(answer(calls=[call("read_file", {"path": "file.txt"})])) for _ in range(2)]
    with pytest.raises(worker.LimitError, match="Step"):
        bounded(tmp_path, read=["file.txt"], steps=2).run("inspect")
    assert len(api.requests) == 2


def test_context_and_output_limits_abort_without_more_requests(tmp_path, api):
    with pytest.raises(worker.LimitError, match="Context"):
        bounded(tmp_path, context=1000).run("x" * 1000)
    assert not api.requests
    api.replies = [Response(answer("x" * 200))]
    with pytest.raises(worker.LimitError, match="output"):
        bounded(tmp_path, output=128).run("inspect")


def test_timeout_during_api_response_prevents_edits(tmp_path, api, monkeypatch):
    clock = [0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    response = Response(answer())
    def read1(*args, **kwargs):
        clock[0] = 2
        return b" "
    response.read1 = read1
    api.replies = [response]
    with pytest.raises(TimeoutError):
        worker.Worker(tmp_path, seconds=1).run("inspect")
    assert len(api.requests) == 1


def test_response_size_and_redirects_fail_without_fallback(tmp_path, api):
    api.replies = [Response(b"x" * 40000)]
    with pytest.raises(worker.LimitError, match="response"):
        bounded(tmp_path).run("inspect")
    response = Response(answer())
    response.status_code = 302
    api.replies = [response]
    with pytest.raises(RuntimeError, match="API"):
        bounded(tmp_path).run("inspect")
    assert len(api.requests) == 2


def test_overall_deadline_bounds_slow_first_response(tmp_path, api, monkeypatch):
    import threading
    gate = threading.Event()
    def slow_post(self, *args, **kwargs):
        assert kwargs["timeout"][1] > .01
        gate.wait(2)
        return Response(answer())
    monkeypatch.setattr(api, "post", slow_post)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            worker.Worker(tmp_path, seconds=.05).run("inspect")
        assert time.monotonic() - started < .5
    finally:
        gate.set()


def test_configured_batch_accepts_five_read_operations(tmp_path, api):
    (tmp_path / "file.txt").write_text("data")
    api.replies = [Response(answer(calls=[call("read_file", {"path": "file.txt"}, f"call-{i}") for i in range(5)])), Response(answer())]
    assert bounded(tmp_path, read=["file.txt"], max_tool_calls=8).run("inspect")["event"] == "complete"


def test_batch_cap_reports_actual_count_and_limit(tmp_path, api):
    api.replies = [Response(answer(calls=[call("read_file", {"path": "file.txt"}, f"call-{i}") for i in range(5)]))]
    with pytest.raises(worker.LimitError, match="batch=5/4; total=5/64"):
        bounded(tmp_path).run("inspect")


def test_total_operations_cap_prevents_unbounded_batches(tmp_path, api):
    (tmp_path / "file.txt").write_text("data")
    api.replies = [Response(answer(calls=[call("read_file", {"path": "file.txt"}, f"call-{i}") for i in range(16)])) for _ in range(5)]
    receipts = []
    instance = worker.Worker(tmp_path, read=["file.txt"], steps=8, max_tool_calls=16, output=8000, context=64000, receipt=receipts.append)
    with pytest.raises(worker.LimitError, match="batch=16/16; total=80/64"):
        instance.run("inspect")
    assert len(receipts) == 64
    assert len(api.requests) == 5


def test_tool_deadline_aborts_batch_before_following_tool(tmp_path, api, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("old")
    clock = [0.0]
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    api.replies = [Response(answer(calls=[call("read_file", {"path": "file.txt"}),
        call("replace_text", {"path": "file.txt", "old": "old", "new": "changed"}, "call-2")]))]
    receipts = []
    instance = worker.Worker(tmp_path, read=["file.txt"], write=["file.txt"], seconds=1, receipt=receipts.append)
    original_tool = instance.tool
    calls = []
    def tool_after_deadline(name, arguments):
        calls.append(name)
        clock[0] = 2.0
        return original_tool(name, arguments)
    monkeypatch.setattr(instance, "tool", tool_after_deadline)
    with pytest.raises(TimeoutError, match="time limit"):
        instance.run("inspect and edit")
    assert calls == ["read_file"]
    assert path.read_text() == "old"
    assert receipts == []
    assert len(api.requests) == 1
