"""Provider-independent services use canonical stores and the same approval policy."""

import threading
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import ValidationError

from local_mode.desktop_host import DesktopRuntime
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent


def fixture(tmp_path):
    runtime = SimpleNamespace(
        suggestions_path=tmp_path / "suggestions.json", event=Mock()
    )
    return runtime, ServiceRegistry(runtime, lambda: None)


def test_ui_and_model_share_persistent_suggestions(tmp_path):
    _, registry = fixture(tmp_path)
    args = {"items": [["News", "Latest news"]]}
    with pytest.raises(ValueError, match="approval"):
        registry.execute("suggestions.save", args)
    registry.ui_call("suggestions.save", args)
    assert registry.execute("data.query", {"source": "suggestions"})["items"] == [
        {"name": "News", "prompt": "Latest news"}
    ]
    bridge = SharedAgent(Mock())
    token = bridge.prepare(
        registry, "suggestions.save", {"items": [["Music", "Play music"]]}
    )
    assert token["requires_approval"]
    bridge.execute(registry, token["token"])
    assert registry.ui_call("suggestions.get", {}) == [("Music", "Play music")]
    with pytest.raises(ValueError, match="consumed"):
        bridge.execute(registry, token["token"])


def test_catalogue_is_cached_bounded_and_excludes_credentials(tmp_path):
    _, registry = fixture(tmp_path)
    validator = registry.validators["data.sources"]
    for _ in range(2):
        registry.validate("data.sources", {})
    assert registry.validators["data.sources"] is validator
    names = []
    cursor = 0
    while cursor is not None:
        page = registry.catalog(cursor=cursor, limit=7)
        names.extend(i["name"] for i in page["items"])
        cursor = page["next_cursor"]
    assert len(names) == len(set(names))
    assert {"translation.run", "embedding.run", "data.query"} <= set(names)
    assert "api_add_key" not in names
    with pytest.raises(ValueError):
        registry.execute("api_add_key", {})
    with pytest.raises(ValidationError):
        registry.validate("data.query", {"source": "credentials"})
    with pytest.raises(ValueError):
        registry.validate("translation.run", {"path": "C:/Users/test/.ssh/id_rsa"})
    registry.register_workspace(
        [
            {
                "name": "read_file",
                "description": "Native read",
                "parameters": {"type": "object"},
            }
        ]
    )
    assert registry.all_descriptors()[-1]["executor"] == "native-host"
    with pytest.raises(ValueError):
        registry.execute("read_file", {})


def test_read_only_rpc_cannot_write(tmp_path):
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    _, runtime.services = fixture(tmp_path)
    with pytest.raises(ValueError, match="approved"):
        runtime.dispatch(
            "services.read", {"name": "suggestions.save", "args": {"items": []}}
        )
    assert (
        runtime.dispatch("services.read", {"name": "data.sources"})["credentials"]
        == "excluded"
    )


def test_session_owner_and_projection_validation():
    runtime = DesktopRuntime.__new__(DesktopRuntime)
    runtime.shared_agent = SharedAgent(Mock())
    runtime.lock = threading.RLock()
    runtime.messages = deque(
        [{"role": "system", "text": "journal", "time": 1}], maxlen=200
    )
    runtime.event = Mock()
    runtime._snapshot_messages = lambda: list(runtime.messages)
    runtime.shared_agent.attach("model", "session", "new")
    assert not runtime.shared_agent.detach("old")
    assert runtime.shared_agent.attached
    payload = {
        "session": "session",
        "owner": "new",
        "items": [{"id": "1", "role": "assistant", "text": "Answer", "time": 2}],
    }
    assert runtime.sync_conversation({**payload, "owner": "old"})["stale"]
    assert runtime.sync_conversation(payload)["ok"]
    assert [i["text"] for i in runtime.messages] == ["journal", "Answer"]
    payload["items"][0]["time"] = float("nan")
    with pytest.raises(ValueError):
        runtime.sync_conversation(payload)
    runtime.shared_agent.detach("new")
    assert runtime.sync_conversation({**payload, "items": []})["stale"]


def test_long_actions_do_not_hold_receipt_lock(tmp_path):
    _, registry = fixture(tmp_path)
    bridge = SharedAgent(Mock())
    started = threading.Event()
    release = threading.Event()
    registry.services["suggestions.get"] = SimpleNamespace(
        handler=lambda _: (started.set(), release.wait(2)), approval=False
    )
    token = bridge.prepare(registry, "suggestions.get", {})["token"]
    thread = threading.Thread(target=lambda: bridge.execute(registry, token))
    thread.start()
    assert started.wait(1)
    try:
        assert bridge.lock.acquire(timeout=0.2)
        bridge.lock.release()
    finally:
        release.set()
        thread.join(2)


def test_large_results_are_bounded_only_at_model_boundary(tmp_path):
    _, registry = fixture(tmp_path)
    result = "Ж" * 50000
    registry.services["suggestions.get"] = SimpleNamespace(
        handler=lambda _: result, approval=False
    )
    assert registry.execute("suggestions.get", {})["truncated"]
    assert registry.ui_call("suggestions.get", {}) == result


def test_voice_activation_changes_runtime_not_text_preferences(tmp_path):
    runtime, registry = fixture(tmp_path)
    runtime.lock = threading.RLock()
    runtime.bot = SimpleNamespace(tools=SimpleNamespace(wake_enabled=False))
    runtime.cycle = Mock()
    runtime.wake_enabled = False
    runtime.muted = True
    assert registry.ui_call("voice.activation", {"enabled": True}) == {
        "ok": True,
        "enabled": True,
        "phrase": "Jarvis",
        "microphone_enabled": False,
        "listening": "microphone off",
    }
    assert runtime.bot.tools.wake_enabled is True
    assert runtime.muted is True
    runtime.cycle.invalidate_capture.assert_called_once()
    assert registry.execute("voice.activation", {})["enabled"] is True
    registry.ui_call("voice.activation", {"enabled": False})
    assert runtime.wake_enabled is False and runtime.bot.tools.wake_enabled is False
    host = DesktopRuntime.__new__(DesktopRuntime)
    host.services = registry
    host.wake_enabled = False
    assert host.control("wake")["enabled"] is True


def test_ui_catalogue_includes_workspace_without_schemas(tmp_path):
    _, registry = fixture(tmp_path)
    old_revision=registry.catalog()["revision"]
    registry.register_workspace([{"name":"native_fixture_read","description":"Native fixture","parameters":{"type":"object"}}])
    page=registry.catalog(query="native_fixture_read",include_parameters=False)
    assert page["revision"] != old_revision
    assert page["total"]==1 and page["items"][0]["executor"]=="native-host"
    assert "parameters" not in page["items"][0]
    assert registry.catalog(query="native_fixture_read")["items"][0]["parameters"]=={"type":"object"}
    with pytest.raises(ValueError):registry.catalog(include_parameters="false")


def test_catalogue_marks_owned_english_descriptions_and_preserves_workspace_text(tmp_path):
    _, registry = fixture(tmp_path)
    owned = registry.catalog(limit=100, include_parameters=False)["items"]
    assert len(owned) == len(registry.descriptors)
    assert all(item["description_owned"] is True for item in owned)
    assert all(not any("Ѐ" <= char <= "ӿ" for char in item["description"]) for item in owned)
    assert all("parameters" not in item for item in owned)
    assert {"open_app", "set_voice", "system_status"} <= {item["name"] for item in owned}

    # A workspace row may equal an owned locale key; equality is not provenance.
    external_description = owned[0]["description"]
    registry.register_workspace([{
        "name": "custom_tool", "description": external_description,
        "parameters": {"type": "object"}, "description_owned": True,
    }])
    published = registry.catalog(query="custom_tool", include_parameters=False)["items"]
    custom = next(item for item in published if item["name"] == "custom_tool")
    assert custom["description"] == external_description
    assert "description_owned" not in custom
