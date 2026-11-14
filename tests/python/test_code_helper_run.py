"""Canonical file execution: owned synthetic scripts, no models or external APIs."""
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import types
from jsonschema.exceptions import ValidationError

from actions import code_helper as helper
from local_mode.services import ServiceRegistry
from local_mode.shared_agent import SharedAgent
from local_mode.tools import ToolRegistry, DECLARATIONS, declarations


@pytest.fixture
def canonical(monkeypatch):
    model = Mock(side_effect=AssertionError("run must not request a model"))
    monkeypatch.setattr(helper, "_get_gemini", model)
    shared = SharedAgent(lambda *_: None)
    shared.attach("fixture", "session", "owner")
    core = ToolRegistry()
    registry = ServiceRegistry(SimpleNamespace(shared_agent=shared, event=lambda *_: None), lambda: core)
    def prepare(args):
        receipt = shared.prepare(registry, "code_helper", args)
        assert receipt["requires_approval"] is True
        return receipt
    def execute(args):
        return shared.execute(registry, prepare(args)["token"])
    return SimpleNamespace(shared=shared, registry=registry, core=core, model=model, prepare=prepare, execute=execute)


def test_owned_write_then_raise_returns_top_level_failure_once_without_inference(canonical, tmp_path):
    script = tmp_path / "owned.py"
    script.write_text("from pathlib import Path\nwith open('attempts.txt','a') as f: f.write('ONCE\\n')\nPath('result.txt').write_text('42')\nprint('BEFORE',flush=True)\nraise RuntimeError('OWNED_RUN_FAILURE')\n")
    receipt = canonical.prepare({"action":"run","file_path":str(script),"args":[],"timeout":10})
    assert not (tmp_path / "attempts.txt").exists()
    result = canonical.shared.execute(canonical.registry, receipt["token"])
    assert result["ok"] is False and result["status"] == "failed" and result["exit_code"] == 1
    assert result["action"] == "code_helper" and result["stdout"] == "BEFORE" + os.linesep
    assert "RuntimeError: OWNED_RUN_FAILURE" in result["stderr"]
    assert result["timed_out"] is False and result["output_complete"] is True and result["truncated"] is False
    assert (tmp_path / "attempts.txt").read_text() == "ONCE\n"
    assert (tmp_path / "result.txt").read_text() == "42"
    with pytest.raises(ValueError):
        canonical.shared.execute(canonical.registry, receipt["token"])
    assert (tmp_path / "attempts.txt").read_text() == "ONCE\n"
    canonical.model.assert_not_called()


def test_success_retains_literal_argv_and_stdout(canonical, tmp_path):
    script = tmp_path / "args.py"
    script.write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n")
    args = ["literal space", "$not_shell", "Привет", "a&b"]
    result = canonical.execute({"action":"run","file_path":str(script),"args":args,"timeout":10})
    assert result["ok"] is True and result["exit_code"] == 0 and "error" not in result
    assert json.loads(result["stdout"]) == args and result["stderr"] == ""
    canonical.model.assert_not_called()


def test_owned_python_prints_and_argv_preserve_unicode_without_global_env_changes(canonical, tmp_path, monkeypatch):
    script = tmp_path / "unicode.py"
    script.write_text("import json,sys,os\nprint('English Русский Українська 日本語 😀')\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\nprint(os.environ['PYTHONUTF8']+':'+os.environ['PYTHONIOENCODING'])\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    arguments = ["English", "Русский текст", "日本語", "😀"]
    result = canonical.execute({"action":"run","file_path":str(script),"args":arguments,"timeout":10})
    assert result["ok"] is True and result["exit_code"] == 0
    lines = result["stdout"].splitlines()
    assert lines[0] == "English Русский Українська 日本語 😀"
    assert json.loads(lines[1]) == arguments and lines[2] == "1:utf-8"
    assert result["decoding_loss"] is False and result["output_complete"] is True
    assert os.environ["PYTHONUTF8"] == "0" and os.environ["PYTHONIOENCODING"] == "ascii"
    canonical.model.assert_not_called()


def test_invalid_utf8_is_reported_as_decoding_loss_without_fake_truncation(canonical, tmp_path):
    script = tmp_path / "invalid_bytes.py"
    script.write_text("import sys\nsys.stdout.buffer.write(b'valid \\xff')\nsys.stderr.buffer.write(b'bad \\xfe')\n")
    result = canonical.execute({"action":"run","file_path":str(script),"timeout":10})
    assert result["ok"] is True and result["exit_code"] == 0
    assert result["stdout"] == "valid \ufffd" and result["stderr"] == "bad \ufffd"
    assert result["decoding_loss"] is True and result["output_complete"] is False
    assert result["truncated"] is False and len(json.dumps(result).encode()) < 48000


def test_valid_replacement_character_is_preserved_without_claiming_decoding_loss():
    result = helper._run_result("succeeded", stdout="valid \ufffd".encode(), exit_code=0)
    assert result["stdout"] == "valid \ufffd" and result["decoding_loss"] is False
    assert result["output_complete"] is True


@pytest.mark.parametrize("change", [{"args":"--legacy-string"},{"args":[1]},{"timeout":0},
                                   {"timeout":181},{"timeout":True},{"code":""},{"code":"print(42)"},
                                   {"file_path":""}])
def test_invalid_run_is_rejected_before_approval_or_execution(canonical, tmp_path, change):
    script = tmp_path / "never.py"
    script.write_text("raise AssertionError('must not execute')\n")
    with pytest.raises(ValidationError):
        canonical.prepare(dict(action="run",file_path=str(script),args=[],timeout=10) | change)
    assert canonical.shared.receipts == {}
    canonical.model.assert_not_called()


def test_run_requires_file_path_and_direct_inline_code_is_not_ignored(canonical):
    with pytest.raises(ValidationError):
        canonical.prepare({"action":"run"})
    result = helper.code_helper({"action":"run","code":"","file_path":"owned.py"})
    assert result["ok"] is False and result["status"] == "invalid_arguments"


def test_missing_file_and_runtime_have_structured_failure(canonical, tmp_path, monkeypatch):
    missing = canonical.execute({"action":"run","file_path":str(tmp_path / "missing.py")})
    assert missing["ok"] is False and missing["status"] == "missing_file" and missing["exit_code"] is None
    script = tmp_path / "owned.js"
    script.write_text("never executed")
    monkeypatch.setattr(helper.shutil, "which", lambda _: None)
    runtime = canonical.execute({"action":"run","file_path":str(script)})
    assert runtime["ok"] is False and runtime["status"] == "missing_runtime"
    unsupported = tmp_path / "owned.unsupported"
    unsupported.write_text("never executed")
    assert canonical.execute({"action":"run","file_path":str(unsupported)})["status"] == "unsupported_runtime"
    canonical.model.assert_not_called()


def test_timeout_stops_owned_tree_and_reports_unavailable_partial_output(canonical, tmp_path):
    script = tmp_path / "timeout.py"
    script.write_text("import time\nprint('START',flush=True)\ntime.sleep(20)\n")
    result = canonical.execute({"action":"run","file_path":str(script),"timeout":1})
    assert result["ok"] is False and result["status"] == "timed_out" and result["timed_out"] is True
    assert result["exit_code"] is None and result["output_complete"] is False
    canonical.model.assert_not_called()


def test_bounded_output_cannot_drop_failure_status_in_service_wrapper(canonical, tmp_path):
    script = tmp_path / "output.py"
    script.write_text("import sys\nsys.stdout.write('\\0'*7000)\nsys.stderr.write('failure')\nraise SystemExit(3)\n")
    result = canonical.execute({"action":"run","file_path":str(script),"timeout":10})
    assert result["ok"] is False and result["exit_code"] == 3 and result["truncated"] is True
    assert len(json.dumps(result).encode()) < 48000 and result["stderr"] == "failure"


def test_excess_output_stops_tree_and_reports_incomplete_output(canonical, tmp_path):
    script = tmp_path / "excess.py"
    script.write_text("import sys\nsys.stdout.write('x'*100000)\nsys.stdout.flush()\n")
    result = canonical.execute({"action":"run","file_path":str(script),"timeout":10})
    assert result["ok"] is False and result["status"] == "output_limit"
    assert result["exit_code"] is None and result["output_complete"] is False


def test_owned_cancellation_stays_a_typed_failure(canonical, tmp_path, monkeypatch):
    from local_mode.owned_process import ProcessCancelled
    script = tmp_path / "cancelled.py"
    script.write_text("raise AssertionError('must not launch')\n")
    def cancelled(*_args, **_kwargs):
        raise ProcessCancelled("owned scope stopped")
    monkeypatch.setattr("local_mode.owned_process.run_owned", cancelled)
    result = canonical.execute({"action":"run","file_path":str(script),"timeout":5})
    assert result["ok"] is False and result["status"] == "cancelled" and result["cancelled"] is True
    assert result["exit_code"] is None and result["output_complete"] is False
    assert result["error"].template == "Code execution was cancelled; its process tree was stopped."


def test_google_projection_constructs_real_sdk_config_without_conditional_keywords(canonical, tmp_path):
    exported = declarations()
    run = next(d for d in exported if d.name == "code_helper")
    assert "allOf" not in run.parameters_json_schema
    assert run.parameters_json_schema["properties"]["args"]["type"] == "array"
    types.GenerateContentConfig(tools=[types.Tool(function_declarations=exported)])
    for descriptor in DECLARATIONS:
        if descriptor["name"] != "code_helper":
            assert next(d for d in exported if d.name == descriptor["name"]).parameters_json_schema == descriptor["parameters"]
    assert "allOf" in canonical.core.schemas["code_helper"]
    canonical.core.validate("code_helper", {"action":"build","description":"legacy","args":"unchanged"})
    with pytest.raises(ValidationError):
        canonical.core.validate("code_helper", {"action":"run","file_path":str(tmp_path / "owned.py"),"args":"invalid"})


@pytest.mark.skipif(os.name != "nt", reason="Windows inherited hidden-console contract")
def test_owned_python_and_ordinary_cmd_descendant_keep_hidden_console(canonical, tmp_path):
    observer = "import ctypes,json\nfrom ctypes import wintypes\nk=ctypes.WinDLL('kernel32');u=ctypes.WinDLL('user32')\nk.GetConsoleWindow.restype=wintypes.HWND\nu.IsWindowVisible.argtypes=[wintypes.HWND]\nh=k.GetConsoleWindow()\nprint(json.dumps({'handle':int(h or 0),'visible':bool(u.IsWindowVisible(h))}))\n"
    child = tmp_path / "child.py"
    child.write_text(observer)
    parent = tmp_path / "parent.py"
    parent.write_text(observer + "import subprocess,sys\nsubprocess.run(['cmd.exe','/d','/c',sys.executable,'child.py'],check=True)\n")
    result = canonical.execute({"action":"run","file_path":str(parent),"timeout":10})
    assert result["ok"] is True, result
    observations = [json.loads(line) for line in result["stdout"].splitlines()]
    assert len(observations) == 2
    assert all(item["handle"] and not item["visible"] for item in observations)
    assert len({item["handle"] for item in observations}) == 1
