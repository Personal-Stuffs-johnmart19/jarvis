"""Project generation preserves drafts without executing or installing anything."""
import json
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from actions import dev_agent as dev
from local_mode.owned_process import owned_action_cancel


@pytest.fixture
def generated(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("No commands may run")))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("No commands may run")))
    monkeypatch.setattr(dev, "PROJECTS_DIR", tmp_path / "projects")
    plan = {"project_name": "draft", "entry_point": "main.py", "files": [
        {"path": "main.py", "description": "entry"}], "dependencies": ["requests"],
        "run_command": "python main.py"}
    model = Mock()
    model.generate_content.side_effect = [SimpleNamespace(text=json.dumps(plan)),
                                         SimpleNamespace(text="print('draft')")]
    monkeypatch.setattr(dev, "_get_model", lambda: model)
    return plan, model


def test_project_saves_once_and_returns_review_plan_without_execution(generated):
    plan, model = generated
    result = dev.dev_agent({"description": "Create project"})
    assert result["ok"] is True and result["status"] == "saved"
    assert result["saved_files"] == ["main.py"]
    assert result["dependencies"] == ["requests"]
    assert result["proposed_run_command"] == "python main.py"
    assert result["execution_pending"] and result["requires_separate_approval"]
    assert (dev.PROJECTS_DIR / "draft/main.py").read_text() == "print('draft')"
    assert model.generate_content.call_count == 2


def test_existing_project_is_never_overwritten(generated):
    plan, model = generated
    directory = dev.PROJECTS_DIR / "draft"
    directory.mkdir(parents=True)
    original = directory / "main.py"
    original.write_text("original")
    result = dev.dev_agent({"description": "Create project"})
    assert result["ok"] is False and result["saved_files"] == []
    assert original.read_text() == "original"
    assert model.generate_content.call_count == 1


@pytest.mark.parametrize("path", ["../escape.py", "/escape.py", "C:/escape.py", "a\\escape.py", "NUL.py"])
def test_invalid_plan_path_is_rejected_before_directory_creation(generated, path):
    plan, model = generated
    plan["files"][0]["path"] = plan["entry_point"] = path
    model.generate_content.side_effect = [SimpleNamespace(text=json.dumps(plan))]
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and not dev.PROJECTS_DIR.exists()
    model.generate_content.assert_called_once()


@pytest.mark.parametrize("count", [0, 33])
def test_plan_file_count_is_bounded(generated, count):
    plan, model = generated
    plan["files"] = [{"path": f"file{i}.py"} for i in range(count)]
    model.generate_content.side_effect = [SimpleNamespace(text=json.dumps(plan))]
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and not dev.PROJECTS_DIR.exists()


def test_partial_generation_failure_reports_exact_saved_files_without_retry(generated):
    plan, model = generated
    plan["files"].append({"path": "second.py"})
    model.generate_content.side_effect = [SimpleNamespace(text=json.dumps(plan)),
                                         SimpleNamespace(text="first"), RuntimeError("writer failed")]
    result = dev.dev_agent({"description": "Create project"})
    assert result["status"] == "partial_failed" and not result["ok"]
    assert result["saved_files"] == ["main.py"]
    assert (dev.PROJECTS_DIR / "draft/main.py").read_text() == "first"
    assert not (dev.PROJECTS_DIR / "draft/second.py").exists()
    assert model.generate_content.call_count == 3


def test_failed_save_is_not_reported_saved(generated, monkeypatch):
    plan, model = generated
    monkeypatch.setattr(dev, "_publish_draft", Mock(side_effect=OSError("disk failure")))
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and result["saved_files"] == []
    assert "disk failure" in result["error"]


def test_generated_utf8_bytes_are_bounded_before_save(generated):
    plan, model = generated
    model.generate_content.side_effect = [SimpleNamespace(text=json.dumps(plan)),
                                         SimpleNamespace(text="😀" * (dev.MAX_FILE_BYTES // 3))]
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and result["saved_files"] == []
    assert not (dev.PROJECTS_DIR / "draft/main.py").exists()


def test_project_total_bytes_are_bounded_preserving_previous_files(generated, monkeypatch):
    plan, model = generated
    plan["files"].append({"path": "second.py"})
    monkeypatch.setattr(dev, "MAX_PROJECT_BYTES", 5)
    model.generate_content.side_effect = [SimpleNamespace(text=json.dumps(plan)),
                                         SimpleNamespace(text="first"), SimpleNamespace(text="second")]
    result = dev.dev_agent({"description": "Create project"})
    assert result["status"] == "partial_failed" and result["saved_files"] == ["main.py"]
    assert (dev.PROJECTS_DIR / "draft/main.py").read_text() == "first"
    assert not (dev.PROJECTS_DIR / "draft/second.py").exists()


def test_file_created_during_generation_is_not_overwritten(generated):
    plan, model = generated
    def generate(prompt):
        if model.generate_content.call_count == 1:
            return SimpleNamespace(text=json.dumps(plan))
        (dev.PROJECTS_DIR / "draft/main.py").write_text("external work")
        return SimpleNamespace(text="new code")
    model.generate_content.side_effect = generate
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and result["saved_files"] == []
    assert (dev.PROJECTS_DIR / "draft/main.py").read_text() == "external work"


def test_cancel_before_generation_never_calls_model(generated):
    plan, model = generated
    cancel = threading.Event()
    cancel.set()
    with owned_action_cancel(cancel):
        result = dev.dev_agent({"description": "Create project"})
    assert result["status"] == "cancelled" and result["cancelled"]
    model.generate_content.assert_not_called()
    assert not dev.PROJECTS_DIR.exists()


def test_cancel_during_generation_never_publishes_response(generated):
    plan, model = generated
    cancel = threading.Event()
    def generate(prompt):
        if model.generate_content.call_count == 1:
            return SimpleNamespace(text=json.dumps(plan))
        cancel.set()
        return SimpleNamespace(text="must not save")
    model.generate_content.side_effect = generate
    with owned_action_cancel(cancel):
        result = dev.dev_agent({"description": "Create project"})
    assert result["status"] == "cancelled" and result["saved_files"] == []
    assert not (dev.PROJECTS_DIR / "draft/main.py").exists()
    assert model.generate_content.call_count == 2


def test_writer_appearing_during_fsync_is_preserved(generated, monkeypatch):
    original_fsync = dev.os.fsync
    destination = dev.PROJECTS_DIR / "draft/main.py"
    def write_concurrently(fd):
        original_fsync(fd)
        destination.write_text("concurrent writer")
    monkeypatch.setattr(dev.os, "fsync", write_concurrently)
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and result["saved_files"] == []
    assert destination.read_text() == "concurrent writer"
    assert list(destination.parent.iterdir()) == [destination]


def test_cancel_during_fsync_never_publishes_draft(generated, monkeypatch):
    cancel = threading.Event()
    original_fsync = dev.os.fsync
    def cancel_staging(fd):
        original_fsync(fd)
        cancel.set()
    monkeypatch.setattr(dev.os, "fsync", cancel_staging)
    with owned_action_cancel(cancel):
        result = dev.dev_agent({"description": "Create project"})
    assert result["status"] == "cancelled" and result["saved_files"] == []
    assert list((dev.PROJECTS_DIR / "draft").iterdir()) == []


@pytest.mark.parametrize("completed", [0, 1])
def test_provider_exception_after_stop_reports_cancelled_with_exact_saved_files(generated, completed):
    plan, model = generated
    if completed:
        plan["files"].append({"path": "second.py"})
    cancel = threading.Event()
    def generate(prompt):
        if model.generate_content.call_count == 1:
            return SimpleNamespace(text=json.dumps(plan))
        if completed and model.generate_content.call_count == 2:
            return SimpleNamespace(text="saved source")
        cancel.set()
        raise RuntimeError("provider interrupted")
    model.generate_content.side_effect = generate
    with owned_action_cancel(cancel):
        result = dev.dev_agent({"description": "Create project"})
    assert result["status"] == "cancelled" and result["cancelled"]
    assert result["saved_files"] == (["main.py"] if completed else [])
    if completed:
        assert (dev.PROJECTS_DIR / "draft/main.py").read_text() == "saved source"


def test_unsupported_create_only_publication_fails_closed(generated, monkeypatch):
    monkeypatch.setattr(dev.os, "link", Mock(side_effect=OSError("hard links unavailable")))
    result = dev.dev_agent({"description": "Create project"})
    assert not result["ok"] and result["saved_files"] == []
    assert "hard links unavailable" in result["error"]
    assert list((dev.PROJECTS_DIR / "draft").iterdir()) == []


def test_staging_cleanup_failure_does_not_hide_a_published_file(generated, monkeypatch):
    original_unlink = dev.Path.unlink
    def fail_cleanup(path, *args, **kwargs):
        if path.suffix == ".tmp":
            raise PermissionError("staging cleanup denied")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(dev.Path, "unlink", fail_cleanup)
    result = dev.dev_agent({"description": "Create project"})
    assert result["ok"] and result["saved_files"] == ["main.py"]
    assert (dev.PROJECTS_DIR / "draft/main.py").read_text() == "print('draft')"
