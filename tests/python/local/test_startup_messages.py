"""Passive startup contracts: no network, model requests, retries or preference writes."""
import ast
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from local_mode.messages import OwnedText
from local_mode.network.startup import start_network_check
from local_mode.results import user_message


@pytest.mark.parametrize("mode", ["system", "proxy", "auto"])
def test_startup_preserves_manual_routing_and_never_probes_a_model(mode, tmp_path):
    from local_mode.network import network_preferences
    path = tmp_path / "network_preferences.json"
    original = '{"mode":"' + mode + '","proxy":"http://127.0.0.1:7890"}'
    path.write_text(original, encoding="utf-8")
    reports, connected = [], Mock()
    with patch.object(network_preferences, "NETWORK_PATH", path), patch("local_mode.network.diagnostics.check_model") as model, patch("local_mode.network.network_preferences.prepare_startup_network") as prepare, patch("local_mode.network.network_preferences.save_network_preferences") as save, patch("local_mode.network.network_preferences.read_network_preferences") as read, patch("local_mode.network.public_proxies.discover_proxies") as discover:
        assert start_network_check(Event(), reports.append, connected) is None
        assert start_network_check(Event(), reports.append, connected, initialize=False) is None
    model.assert_not_called()
    prepare.assert_not_called()
    save.assert_not_called()
    read.assert_not_called()
    discover.assert_not_called()
    connected.assert_not_called()
    assert path.read_text(encoding="utf-8") == original
    assert len(reports) == 1 and isinstance(reports[0], OwnedText)
    assert user_message({"ok": True, "reply": reports[0]}, locale="en") == "Network checks are manual. Saved routing is used for the next model request."
    assert user_message({"ok": True, "reply": reports[0]}, locale="ru") == "Сетевые проверки запускаются вручную. Сохранённый маршрут используется при следующем запросе к модели."


def _startup_callbacks():
    source = (Path(__file__).resolve().parents[3] / "local_mode/desktop_host.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [node.args[1] for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "start_network_check"]


def test_desktop_startup_journal_callbacks_use_current_locale_and_preserve_raw_values():
    callbacks = _startup_callbacks()
    assert len(callbacks) == 2
    detail = "Microphone selected. {0} C:/raw/provider"
    for expression in callbacks:
        journal = []
        runtime = SimpleNamespace(interface_locale="ru", write_log=journal.append)
        callback = eval(compile(ast.Expression(expression), "desktop-startup-callback", "eval"), {"self": runtime, "user_message": user_message})
        callback(OwnedText("Gemini check: {0}", detail))
        assert journal[-1] == "SYS: Проверка Gemini: " + detail
        runtime.interface_locale = "en"
        callback(OwnedText("Gemini check: {0}", detail))
        assert journal[-1] == "SYS: Gemini check: " + detail
        callback("Microphone selected.")
        assert journal[-1] == "SYS: Microphone selected."


@pytest.mark.parametrize("provider", ["ollama:local", "lmstudio:local", "managed:local", "gemini"])
def test_desktop_startup_for_any_selected_provider_does_not_contact_google(provider):
    source = (Path(__file__).resolve().parents[3] / "local_mode/desktop_host.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "start_network_check" and not node.keywords)
    runtime = SimpleNamespace(stop=Event(), interface_locale="en", write_log=Mock(), shared_agent=SimpleNamespace(model=provider), connected=False)
    with patch("local_mode.network.diagnostics.check_model") as model, patch("local_mode.gemini.create_client") as client, patch("local_mode.routing.ModelRouter.generate") as generate:
        result = eval(compile(ast.Expression(call), "desktop-startup-call", "eval"), {"self": runtime, "start_network_check": start_network_check, "user_message": user_message})
    assert result is None
    model.assert_not_called()
    client.assert_not_called()
    generate.assert_not_called()
    assert runtime.connected is False


def test_cancelled_or_recovery_startup_never_reports_or_schedules_waits():
    stop = Mock(spec=Event)
    stop.is_set.return_value = True
    report, connected = Mock(), Mock()
    assert start_network_check(stop, report, connected) is None
    stop.is_set.return_value = False
    assert start_network_check(stop, report, connected, initialize=False) is None
    report.assert_not_called()
    connected.assert_not_called()
    stop.wait.assert_not_called()


def test_explicit_network_model_check_remains_a_single_user_requested_probe():
    source = (Path(__file__).resolve().parents[3] / "local_mode/desktop_host.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) and node.test.left.id == "method" and any(isinstance(value, ast.Constant) and value.value == "network.check" for value in node.test.comparators))
    wrapper = ast.FunctionDef(name="explicit_check", args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="params")], kwonlyargs=[], kw_defaults=[], defaults=[]), body=branch.body, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    scope = {}
    exec(compile(module, "explicit-network-check", "exec"), scope)
    settings = {"mode": "proxy", "proxy": "http://127.0.0.1:7890"}
    expected = {"ok": True, "message": OwnedText("Model {0} responds: {1}", "model", "raw reply")}
    with patch("local_mode.network.network_preferences.read_network_preferences", return_value=settings), patch("local_mode.network.diagnostics.check_model", return_value=expected) as model, patch("local_mode.network.diagnostics.check_connection") as connection:
        assert scope["explicit_check"]({"model": True}) is expected
    model.assert_called_once_with(**settings)
    connection.assert_not_called()
