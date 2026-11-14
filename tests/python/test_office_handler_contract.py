"""Canonical office handler outcomes using owned files, without provider calls."""
from unittest.mock import patch

import pytest

from actions import office_builder
from local_mode.shared_agent import SharedAgent
from local_mode.tools import ToolRegistry


@pytest.mark.parametrize("title", ["", "   "])
def test_missing_presentation_title_reports_failure_without_artifact(tmp_path, title):
    registry = ToolRegistry()
    bridge = SharedAgent(lambda *_: None)
    with patch.object(office_builder, "OUT_DIR", tmp_path):
        receipt = bridge.prepare(registry, "create_presentation", {"title": title})
        result = bridge.execute(registry, receipt["token"])
        assert result["ok"] is False
        assert result["error"]
        assert list(tmp_path.iterdir()) == []
        with pytest.raises(ValueError, match="consumed"):
            bridge.execute(registry, receipt["token"])


def test_presentation_success_is_an_owned_readable_artifact(tmp_path):
    from pptx import Presentation

    registry = ToolRegistry()
    bridge = SharedAgent(lambda *_: None)
    with patch.object(office_builder, "OUT_DIR", tmp_path):
        receipt = bridge.prepare(registry, "create_presentation", {
            "title": "Owned title", "slides": [{"title": "Owned slide", "bullets": ["Owned bullet"]}]})
        result = bridge.execute(registry, receipt["token"])
        assert result["ok"] is True
        path = tmp_path / "Owned title.pptx"
        assert result["path"] == str(path)
        deck = Presentation(path)
        assert len(deck.slides) == 2
        assert deck.slides[0].shapes.title.text == "Owned title"
        assert deck.slides[1].shapes.title.text == "Owned slide"
        assert "Owned bullet" in deck.slides[1].placeholders[1].text


def test_presentation_save_failure_is_typed_and_leaves_no_file(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("owned blocker", encoding="utf-8")
    registry = ToolRegistry()
    with patch.object(office_builder, "OUT_DIR", blocked):
        result = registry.execute("create_presentation", {"title": "Owned title"}, approved=True)
    assert result["ok"] is False
    assert result["error"].startswith("Could not create presentation:")
    assert blocked.read_text(encoding="utf-8") == "owned blocker"

def test_spreadsheet_success_is_an_owned_readable_artifact(tmp_path):
    from openpyxl import load_workbook

    registry = ToolRegistry()
    bridge = SharedAgent(lambda *_: None)
    with patch.object(office_builder, "OUT_DIR", tmp_path):
        receipt = bridge.prepare(registry, "create_spreadsheet", {
            "filename": "Owned sheet", "headers": ["Column"], "rows": [["Owned value"]]})
        result = bridge.execute(registry, receipt["token"])
    path = tmp_path / "Owned sheet.xlsx"
    assert result["ok"] is True and result["path"] == str(path)
    workbook = load_workbook(path)
    assert workbook.active["A1"].value == "Column"
    assert workbook.active["A2"].value == "Owned value"
    workbook.close()
    with pytest.raises(ValueError, match="consumed"):
        bridge.execute(registry, receipt["token"])


@pytest.mark.parametrize("name", ["create_presentation", "create_spreadsheet"])
def test_office_derived_reserved_filename_is_rejected_before_creating_output_folder(tmp_path, name):
    output = tmp_path / "not-created"
    registry = ToolRegistry()
    with patch.object(office_builder, "OUT_DIR", output):
        result = registry.execute(name, {"filename": "NUL", **({"title": "Owned title"} if name == "create_presentation" else {})}, approved=True)
    assert result["ok"] is False and result["error"]
    assert "path" not in result and not output.exists()


@pytest.mark.parametrize("name,suffix", [("create_presentation", ".pptx"), ("create_spreadsheet", ".xlsx")])
def test_office_exclusive_create_preserves_existing_artifact(tmp_path, name, suffix):
    existing = tmp_path / ("Owned" + suffix)
    existing.write_bytes(b"owned original bytes")
    registry = ToolRegistry()
    with patch.object(office_builder, "OUT_DIR", tmp_path):
        result = registry.execute(name, {"filename": "Owned", **({"title": "Owned title"} if name == "create_presentation" else {})}, approved=True)
    assert result["ok"] is False and result["error"]
    assert "path" not in result and existing.read_bytes() == b"owned original bytes"


def test_spreadsheet_save_failure_is_typed(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"owned blocker")
    registry = ToolRegistry()
    with patch.object(office_builder, "OUT_DIR", blocked):
        result = registry.execute("create_spreadsheet", {"filename": "Owned"}, approved=True)
    assert result["ok"] is False and result["error"].startswith("Could not create spreadsheet:")
    assert "path" not in result and blocked.read_bytes() == b"owned blocker"

@pytest.mark.parametrize("name,suffix,target", [
    ("create_presentation", ".pptx", "pptx.presentation.Presentation.save"),
    ("create_spreadsheet", ".xlsx", "openpyxl.workbook.workbook.Workbook.save")])
def test_office_serialization_failure_creates_no_destination_and_allows_retry(tmp_path, name, suffix, target):
    registry = ToolRegistry()
    output = tmp_path / "not-created"
    path = output / ("Owned" + suffix)
    args = {"filename": "Owned", **({"title": "Owned title"} if name == "create_presentation" else {})}

    def failed_writer(destination):
        destination.write(b"owned incomplete prefix")
        raise ValueError("owned serialization failure")

    with patch.object(office_builder, "OUT_DIR", output):
        with patch(target, side_effect=failed_writer):
            result = registry.execute(name, args, approved=True)
        assert result["ok"] is False and "owned serialization failure" in result["error"]
        assert "path" not in result and not path.exists() and not output.exists()
        retry = registry.execute(name, args, approved=True)
    assert retry["ok"] is True and retry["path"] == str(path) and path.stat().st_size > 0