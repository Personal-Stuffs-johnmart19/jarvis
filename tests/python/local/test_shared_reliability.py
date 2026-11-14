"""Дополнительные исправления из gemini_agent 8c7a5ea."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from google.genai import types
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from agent.loop_guard import LoopGuard
from local_mode.storage import write_json


class ReliabilityTests(unittest.TestCase):
    def test_long_arguments_are_not_truncated_for_loop_detection(self):
        guard = LoopGuard()
        for index in range(6):
            self.assertIsNone(guard.check("file", {"prefix": "x" * 300, "tail": index}))

    def test_different_files_in_alternating_tools_are_not_a_loop(self):
        guard = LoopGuard()
        for index in range(6):
            self.assertIsNone(guard.check("read" if index % 2 == 0 else "write", {"path": str(index)}))

    def test_identical_calls_and_true_alternation_are_stopped(self):
        guard = LoopGuard()
        self.assertIsNone(guard.check("read", {"path": "x"}))
        self.assertIsNone(guard.check("read", {"path": "x"}))
        self.assertIsNotNone(guard.check("read", {"path": "x"}))
        guard.reset()
        for index in range(5):
            self.assertIsNone(guard.check("read" if index % 2 == 0 else "write", {"path": "x"}))
        self.assertIsNotNone(guard.check("write", {"path": "x"}))

    def test_transient_windows_write_lock_is_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferences.json"
            replace = Path.replace
            calls = []
            def locked(source, target):
                calls.append(target)
                if len(calls) < 3:
                    error = PermissionError("transient lock")
                    error.winerror = 32
                    raise error
                return replace(source, target)
            with patch.object(Path, "replace", locked), patch("local_mode.storage.time.sleep") as pause:
                write_json(path, {"ok": True})
            self.assertEqual(len(calls), 3)
            self.assertEqual(pause.call_count, 2)
            self.assertIn("true", path.read_text())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_invalid_json_value_keeps_previous_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "preferences.json"
            write_json(path, {"value": 1})
            with self.assertRaises(ValueError):
                write_json(path, {"value": float("nan")})
            self.assertIn("1", path.read_text())
            self.assertEqual(len(list(Path(folder).iterdir())), 1)


class AgentReliabilityTests(unittest.TestCase):
    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.assistant.gemini.create_client")
    def test_exact_app_command_uses_no_gemini(self, create, youtube, windows):
        bot = LocalAgent(Settings())
        with patch.object(bot.tools, "execute", return_value={"ok": True}) as execute:
            for command in ["открой блокнот", "запусти блокнот"]:
                self.assertTrue(bot.run(command)["ok"])
                execute.assert_called_with("open_app", {"app_name": "notepad"})
        create.assert_not_called()

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.assistant.gemini.create_client", side_effect=RuntimeError("network deliberately disabled"))
    def test_negation_and_compound_request_do_not_trigger_local_app(self, create, youtube, windows):
        bot = LocalAgent(Settings())
        with patch.object(bot.tools, "execute") as execute:
            for command in ["не открывай блокнот", "открой блокнот и удали файл"]:
                with self.assertRaisesRegex(RuntimeError, "network deliberately disabled"):
                    bot.run(command)
            execute.assert_not_called()

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.assistant.gemini.create_client")
    @patch("local_mode.assistant.gemini.generate")
    def test_tool_loop_stops_before_another_request(self, generate, create, youtube, windows):
        generate.side_effect = [types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name="web_search", args={"query": "same"}, id=str(index)))]))]) for index in range(3)]
        bot = LocalAgent(Settings())
        with patch.object(bot.tools, "execute", return_value={"ok": True}) as execute:
            result = bot.run("Проверь результаты поиска")
            self.assertFalse(result["ok"])
            self.assertEqual(result["action"], "tool_loop")
            self.assertEqual(execute.call_count, 2)
            self.assertEqual(generate.call_count, 3)
