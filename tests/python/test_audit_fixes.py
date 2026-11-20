"""Focused regression tests for tool wiring, security boundaries and bug fixes."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest


def test_all_declared_tools_and_handlers_import_cleanly():
    from local_mode.capabilities import report

    result = report()
    assert result["ok"] is True
    missing = [t for t in result["tools"] if not t["handler_available"]]
    assert missing == []


def test_gmail_omitted_action_requires_confirmation_and_read_stays_low():
    import safety

    assert safety.classify("gmail", {}) == safety.Risk.HIGH
    assert safety.classify("gmail", {"action": "send"}) == safety.Risk.HIGH
    assert safety.classify("gmail", {"action": "read"}) == safety.Risk.LOW
    assert safety.classify("gmail", {"action": "search"}) == safety.Risk.LOW


def test_code_helper_optimize_requires_approval_and_refuses_silent_truncation(tmp_path):
    import safety
    from actions import code_helper
    from local_mode.tools import ToolRegistry

    assert safety.classify("code_helper", {"action": "optimize"}) == safety.Risk.HIGH
    assert ToolRegistry().risky("code_helper", {"action": "optimize"}) is True
    assert ToolRegistry().risky("code_helper", {"action": "explain"}) is False

    big_file = tmp_path / "large.py"
    big_file.write_text("x = 1\n" * 5000, encoding="utf-8")
    result = code_helper.code_helper({"action": "optimize", "file_path": str(big_file)})
    assert isinstance(result, dict) and result["ok"] is False
    assert result["status"] == "file_too_large"
    assert big_file.read_text(encoding="utf-8") == "x = 1\n" * 5000


def test_validate_paths_allows_http_source_and_blocks_smart_home_secret():
    from local_mode.tools import validate_paths

    validate_paths({"source": "https://example.com/docs/guide.html"})
    validate_paths({"source": "http://example.com/page"})
    with pytest.raises(ValueError):
        validate_paths({"path": "config/smart_home.json"})
    with pytest.raises(ValueError):
        validate_paths({"source": "config/smart_home.json"})


def test_smart_home_control_wraps_ha_config_for_turn_on(monkeypatch):
    from actions import smart_home

    captured = {}

    def fake_ha_request(cfg, method, path, body=None):
        captured["cfg"] = cfg
        captured["method"] = method
        captured["path"] = path
        captured["body"] = body
        return {}

    monkeypatch.setattr(smart_home, "_ha_request", fake_ha_request)
    monkeypatch.setattr(
        smart_home,
        "_load_config",
        lambda: {"home_assistant": {"url": "http://127.0.0.1:8123", "token": "tok"}},
    )
    out = smart_home._control_home_assistant(
        {"name": "Desk Lamp", "entity": "light.desk"}, "turn_on", None
    )
    assert "OK" in out
    assert captured["cfg"] == {"home_assistant": {"url": "http://127.0.0.1:8123", "token": "tok"}}
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/services/light/turn_on"


def test_media_control_defaults_to_toggle_and_supports_previous(monkeypatch):
    from actions import browser_control as bc
    from actions.media_control import media_control_action

    calls = []

    def fake_browser(params):
        calls.append(dict(params))
        if params.get("action") == "media_state":
            return {"ok": True, "media": {"video": True, "paused": True}}
        return {"ok": True, "action": params.get("action")}

    monkeypatch.setattr(bc, "browser_control", fake_browser)
    res_default = media_control_action({})
    assert res_default["ok"] is True
    assert calls[-1]["action"] == "media_resume"

    res_prev = media_control_action({"action": "previous"})
    assert res_prev["ok"] is True
    assert calls[-1]["action"] == "media_seek_back"


def test_flight_finder_handles_dict_browser_text_and_numeric_price(monkeypatch):
    from actions import browser_control as bc
    from actions import flight_finder

    monkeypatch.setattr(flight_finder.time if hasattr(flight_finder, "time") else __import__("time"), "sleep", lambda *_: None)
    monkeypatch.setattr(
        bc,
        "browser_control",
        lambda params: {"ok": True, "text": "Airline A $200 Airline B $1000"}
        if params.get("action") == "get_text"
        else {"ok": True},
    )
    raw, url = flight_finder._search_flights_browser("IST", "LON", "2026-12-01", None, 2, "business")
    assert isinstance(raw, str) and "Airline A" in raw
    assert "business+class" in url and "2+passengers" in url

    spoken = flight_finder._format_spoken(
        [
            {"airline": "ExpensiveAir", "price": "$1000", "currency": "USD", "stops": 0},
            {"airline": "BudgetAir", "price": "$200", "currency": "USD", "stops": 1},
        ],
        "IST",
        "LON",
        "2026-12-01",
    )
    assert "BudgetAir" in spoken.split("cheapest option is")[-1]


def test_daily_briefing_formats_structured_weather_dict(monkeypatch):
    from actions import daily_briefing, weather_report

    monkeypatch.setattr(
        weather_report,
        "weather_action",
        lambda **_: {
            "ok": True,
            "location": {"name": "London"},
            "current": {"temperature_2m": 15.2, "apparent_temperature": 14.0, "wind_speed_10m": 12.0},
            "current_units": {"temperature_2m": "°C", "wind_speed_10m": "km/h"},
            "daily": {"temperature_2m_max": [18.0], "temperature_2m_min": [9.5]},
        },
    )
    text = daily_briefing._get_weather("London")
    assert "London:" in text and "15.2°C" in text and "{" not in text


def test_file_controller_rejects_empty_move_or_copy_destination(tmp_path):
    from actions.file_controller import file_controller

    src = tmp_path / "sample.txt"
    src.write_text("hello", encoding="utf-8")
    for action in ("move", "copy"):
        res = file_controller({"action": action, "path": str(src), "destination": ""})
        assert res["ok"] is False
        assert src.exists()


def test_drive_escapes_quotes_and_calendar_attaches_timezone():
    from actions.calendar import _parse_time
    from actions.drive import _escape_query
    from datetime import datetime

    assert _escape_query("O'Reilly\\notes") == "O\\'Reilly\\\\notes"
    now = datetime.now().astimezone()
    rfc = _parse_time("2026-12-01T15:30:00", now)
    parsed = datetime.fromisoformat(rfc)
    assert parsed.tzinfo is not None


def test_cloud_translate_works_when_selected_text_provider_is_bound():
    from local_mode.text_helpers import using_text_provider
    from local_mode.translation import cloud_translate

    seen = {}

    def fake_provider(request, *, cancel_flag=None, deadline=None):
        seen["request"] = request
        return {"text": json.dumps({"translation": "Привет", "source_language": "en"})}

    with using_text_provider(fake_provider):
        translated, detected = cloud_translate("Hello", "auto", "ru")
    assert translated == "Привет"
    assert detected == "en"
    assert seen["request"]["json_mode"] is True


def test_task_queue_restarts_cleanly_after_stop():
    from agent import task_queue as tq

    q = tq.get_queue()
    assert q._running is True
    tq.cancel_existing(stop=True)
    q2 = tq.get_queue()
    assert q2._running is True
    tq.cancel_existing(stop=True)


def test_vision_event_store_add_many_and_recent(tmp_path):
    from vision.event_store import VisionEventStore
    from vision.vision_events import VisionEvent

    store = VisionEventStore(tmp_path / "events.db")
    store.add_many([
        VisionEvent(type="object", source="cam", label="cup", confidence=0.9),
        VisionEvent(type="object", source="cam", label="book", confidence=0.8),
    ])
    rows = store.recent(10)
    assert len(rows) == 2
    assert {r["label"] for r in rows} == {"cup", "book"}
