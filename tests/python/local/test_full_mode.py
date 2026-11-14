"""Контракты полного runtime: никаких реальных кликов, отправок или shell."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from google.genai import types
from jsonschema.exceptions import ValidationError
from local_mode.tools import ToolRegistry, DECLARATIONS, HANDLERS
from local_mode.assistant import LocalAgent
from local_mode.config import ROOT, Settings
from local_mode.background import BackgroundServices
from local_mode.model import ContentModel, generate_content


def minimum(schema: dict):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {key: minimum(schema["properties"][key]) for key in schema.get("required", [])}
    return {"string": "test", "integer": 1, "number": 1.5, "boolean": False, "array": []}.get(kind, "test")


def response(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))])


class FullToolsTests(unittest.TestCase):
    def test_all_53_tools_have_valid_schemas_and_coverage(self):
        from jsonschema import Draft202012Validator
        from local_mode.capabilities import SPECIAL
        self.assertEqual(len(DECLARATIONS), 53)
        self.assertEqual({d["name"] for d in DECLARATIONS}, set(HANDLERS) | SPECIAL)
        for declaration in DECLARATIONS:
            with self.subTest(tool=declaration["name"]):
                Draft202012Validator.check_schema(declaration["parameters"])
                types.FunctionDeclaration(name=declaration["name"], parameters_json_schema=declaration["parameters"])

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.assistant.gemini.create_client")
    @patch("local_mode.assistant.gemini.generate")
    def test_model_dispatches_every_original_tool(self, generate, create, youtube, windows):
        for declaration in DECLARATIONS:
            name = declaration["name"]
            with self.subTest(tool=name):
                args = minimum(declaration["parameters"])
                generate.side_effect = [response(types.Part(function_call=types.FunctionCall(name=name, id="one", args=args))), response(types.Part(text="Готово"))]
                bot = LocalAgent(Settings())
                with patch.object(bot.tools, "risky", return_value=False), patch.object(bot.tools, "_invoke", return_value={"ok": True, "action": name}) as call:
                    result = bot.run("Выполни явно запрошенное действие")
                    self.assertTrue(result["ok"])
                    call.assert_called_once_with(name, args)

    def test_model_cannot_self_confirm(self):
        registry = ToolRegistry()
        with self.assertRaises(ValidationError):
            registry.execute("file_controller", {"action": "delete", "path": "test", "confirm": True})
        with patch.object(registry, "_invoke") as invoke:
            result = registry.execute("file_controller", {"action": "delete", "path": "test"})
            self.assertTrue(result["requires_confirmation"])
            invoke.assert_not_called()
            registry.source = "telegram:other"
            self.assertFalse(registry.confirm(True)["ok"])
            invoke.assert_not_called()
            registry.source = "local"
            registry.confirm(True)
            invoke.assert_called_once_with("file_controller", {"action": "delete", "path": "test"})
            registry.confirm(True)
            self.assertEqual(invoke.call_count, 1)

    def test_validation_rejects_extra_and_wrong_types(self):
        registry = ToolRegistry()
        for args in ({"focus": 42}, {"focus": "memory", "command": "unsafe"}):
            with self.assertRaises(ValidationError):
                registry.execute("system_status", args)

    def test_background_executor_cannot_bypass_confirmation(self):
        from agent.executor import _call_tool
        with patch.object(ToolRegistry, "_invoke") as invoke:
            with self.assertRaises(RuntimeError):
                _call_tool("file_controller", {"action": "delete", "path": "test"}, None)
            invoke.assert_not_called()

    def test_sensitive_confirmation_is_redacted(self):
        registry = ToolRegistry()
        result = registry.execute("api_add_key", {"key": "synthetic-sensitive-key-12345"})  # gitleaks:allow - intentionally synthetic redaction fixture
        self.assertNotIn("synthetic-sensitive-key-12345", json.dumps(result))

    def test_tool_failure_is_not_reported_as_success(self):
        registry = ToolRegistry()
        registry.special["web_search"] = lambda args: "Search failed: timeout"
        self.assertFalse(registry.execute("web_search", {"query": "x"})["ok"])

    def test_scheduler_fires_once_and_survives_restart(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as folder:
            path = Path(folder) / "schedule.json"
            report = Mock()
            service = BackgroundServices(threading.Event(), report, path)
            item = service.schedule({"action": "set", "seconds": 20, "message": "Тест"})["schedule"]
            recovered = BackgroundServices(threading.Event(), report, path)
            recovered.tick(item["fire_at"] + 1)
            recovered.tick(item["fire_at"] + 2)
            report.assert_called_once_with("Reminder: Тест")

    def test_telegram_rejects_other_senders(self):
        from local_mode.remote import RemoteServices
        remote = RemoteServices(None, Mock(), threading.Event(), Mock())
        self.assertTrue(remote.allowed({"chat": {"id": 123}, "from": {"id": 123}}, "123"))
        self.assertFalse(remote.allowed({"chat": {"id": 123}, "from": {"id": 456}}, "123"))
        self.assertFalse(remote.allowed({"chat": {"id": -100}, "from": {"id": 123}}, "-100"))

    @patch("local_mode.model.create_client")
    @patch("local_mode.model.generate")
    def test_legacy_prompt_uses_selected_model_and_preserves_system_instruction(self, generate, create):
        ContentModel("old-paid-name", system_instruction="JSON only").generate_content("test")
        self.assertEqual(generate.call_args.args[1], Settings().model)
        self.assertIn("JSON only", generate.call_args.args[3].system_instruction)
        create.return_value.__exit__.assert_called_once()

    @patch("local_mode.model.create_client")
    def test_paid_grounding_is_blocked_before_network(self, create):
        with self.assertRaises(ValueError):
            generate_content(contents="test", config={"tools": [{"google_search": {}}]})
        create.assert_not_called()

    def test_only_one_sdk_request_site_and_no_legacy_sdk(self):
        files = [* (ROOT / "actions").glob("*.py"), *(ROOT / "agent").glob("*.py"), *(ROOT / "vision").glob("*.py"), *(ROOT / "memory").glob("*.py")]
        for path in files:
            source = path.read_text(encoding="utf-8-sig")
            with self.subTest(path=path.name):
                self.assertNotIn("google.generativeai", source)
                self.assertNotIn(".models.generate_content(", source)


class BoundaryTests(unittest.TestCase):
    def test_credential_files_and_file_urls_are_not_sent_to_model(self):
        registry = ToolRegistry()
        with self.assertRaises(ValueError):
            registry.execute("file_controller", {"action": "read", "path": str(ROOT / "config/bootstrap-key.json")})
        with self.assertRaises(ValueError):
            registry.execute("browser_control", {"action": "navigate", "url": "file:///C:/private.txt"})

    def test_duplicate_gui_instance_is_blocked_without_opening_windows(self):
        from local_mode.instance import SingleInstance
        with tempfile.TemporaryDirectory() as folder, SingleInstance(Path(folder)) as first:
            self.assertTrue(first)
            with SingleInstance(Path(folder)) as second:
                self.assertFalse(second)

    def test_vosk_youtube_alias_preserves_default_query(self):
        from local_mode.assistant import local_request
        request = local_request("включи музыку на и тумбе", Settings())
        self.assertEqual(request.action, "youtube_play")
        self.assertEqual(request.query, Settings().music_query)

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.browser_profiles.read_preferences", return_value={"mode":"new","profile":""})
    def test_original_youtube_play_uses_verified_player(self, preferences, youtube, windows):
        youtube.return_value.play.return_value = {"ok": True, "verified": True}
        bot = LocalAgent(Settings())
        self.assertTrue(bot.tools.execute("youtube_video", {"action": "play", "query": "джаз"})["verified"])
        youtube.return_value.play.assert_called_once_with("джаз")


class StorageAndAudioTests(unittest.TestCase):
    def test_memory_roundtrip_finds_snake_case_key(self):
        from memory import memory_manager
        registry = ToolRegistry()
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as folder:
            with patch.object(memory_manager, "MEMORY_PATH", Path(folder) / "memory.json"):
                registry.execute("save_memory", {"category": "notes", "key": "qa_note", "value": "Русская заметка"})
                self.assertIn("Русская заметка", registry.execute("recall_memory", {"query": "qa_note"})["result"])

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("api_keys.add_key")
    def test_key_change_invalidates_existing_client(self, add_key, youtube, windows):
        bot = LocalAgent(Settings())
        client = bot.client = Mock()
        bot._change_key({"key": "synthetic-key-value-for-testing"})
        client.close.assert_called_once()
        self.assertIsNone(bot.client)


    def test_file_and_office_roundtrip_uses_real_local_libraries(self):
        from actions import office_builder
        from openpyxl import load_workbook
        from pptx import Presentation
        registry = ToolRegistry()
        with tempfile.TemporaryDirectory(dir=ROOT / ".cache") as directory:
            folder = Path(directory)
            registry.execute("file_controller", {"action": "create_file", "path": str(folder / "qa.txt"), "content": "Русский текст"})
            self.assertIn("Русский текст", registry.execute("file_controller", {"action": "read", "path": str(folder / "qa.txt")})["result"])
            with patch.object(office_builder, "OUT_DIR", folder):
                registry.execute("create_spreadsheet", {"filename": "qa", "headers": ["Тест"], "rows": [["Данные"]]})
                registry.execute("create_presentation", {"filename": "qa", "title": "Тест", "slides": [{"title": "Слайд", "content": "Проверка"}]})
            self.assertEqual(load_workbook(folder / "qa.xlsx").active["A2"].value, "Данные")
            self.assertGreater(len(Presentation(folder / "qa.pptx").slides), 0)


    def test_silent_mode_cancels_pending_audio_without_playback(self):
        from local_mode.voice import LocalSpeaker
        speaker = LocalSpeaker()
        speaker._queue.put("Первый ответ")
        speaker._queue.put("Второй ответ")
        speaker.cancel()
        self.assertEqual(speaker._queue.unfinished_tasks, 0)
        self.assertTrue(speaker._cancel.is_set())


class AccurateVoiceTests(unittest.TestCase):
    def test_whisper_resamples_native_rate_and_uses_local_russian_model(self):
        from local_mode.whisper_voice import AccurateRussianRecognizer
        from types import SimpleNamespace
        import numpy as np
        recognizer = AccurateRussianRecognizer.__new__(AccurateRussianRecognizer)
        recognizer.model = Mock()
        recognizer.model.transcribe.return_value = (iter([SimpleNamespace(text=" Полная русская команда ")]), None)
        text = recognizer.transcribe(np.full(48000, 8192, dtype=np.int16).tobytes(), 48000)
        self.assertEqual(text, "Полная русская команда")
        audio = recognizer.model.transcribe.call_args.args[0]
        self.assertEqual(len(audio), 16000 + 2 * 6400)
        np.testing.assert_array_equal(audio[6400:-6400], np.full(16000, 0.25, dtype=np.float32))
        self.assertFalse(audio[:6400].any())
        self.assertFalse(audio[-6400:].any())
        self.assertEqual(recognizer.model.transcribe.call_args.kwargs["language"], "ru")

    def test_microphone_closes_before_whisper_inference(self):
        from local_mode.speech import RussianRecognizer
        recognizer = RussianRecognizer.__new__(RussianRecognizer)
        recognizer.microphone = None
        recognizer.engine = "whisper"
        decoder = Mock()
        decoder.AcceptWaveform.return_value = True
        decoder.Result.return_value = '{"text":"команда"}'
        recognizer._decoder = Mock(return_value=decoder)
        events = []
        def infer(data, rate):
            self.assertEqual(events, ["closed"])
            return "Точная команда"
        recognizer._accurate = infer
        def stream(**kwargs):
            class Input:
                def __enter__(self):
                    kwargs["callback"](b"\x00\x00" * 1000, 1000, None, None)
                def __exit__(self, *args):
                    events.append("closed")
            return Input()
        with patch("sounddevice.query_devices", return_value={"default_samplerate": 48000}), patch("sounddevice.RawInputStream", side_effect=stream):
            self.assertEqual(recognizer.listen(), "Точная команда")

    def test_telegram_ogg_opus_decodes_locally(self):
        import av
        import io
        import numpy as np
        import telegram_bot
        output = io.BytesIO()
        with av.open(output, mode="w", format="ogg") as container:
            stream = container.add_stream("libopus", rate=48000)
            stream.layout = "mono"
            frame = av.AudioFrame.from_ndarray(np.zeros((1, 48000), dtype=np.float32), format="fltp", layout="mono")
            frame.sample_rate = 48000
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        with patch("local_mode.speech.RussianRecognizer") as recognizer:
            recognizer.return_value.engine = "whisper"
            recognizer.return_value._accurate.return_value = "Русская команда"
            self.assertEqual(telegram_bot.transcribe_voice("", output.getvalue()), "Русская команда")
            self.assertGreater(len(recognizer.return_value._accurate.call_args.args[0]), 30000)


    def test_autostart_routes_to_unified_launcher_and_preserves_remote_option(self):
        from actions.autostart import _launch_command
        local = _launch_command(False)
        remote = _launch_command(True)
        self.assertIn("launch_local.py", local)
        self.assertNotIn("--remote", local)
        self.assertTrue(remote.endswith(" --remote"))


    def test_wake_poll_extends_deadline_for_started_utterance(self):
        from local_mode.speech import RussianRecognizer
        r = RussianRecognizer.__new__(RussianRecognizer)
        r.microphone = None
        r.engine = "whisper"
        decoder = Mock()
        decoder.AcceptWaveform.side_effect = [False, True]
        decoder.PartialResult.return_value = '{"partial":"помощник"}'
        decoder.Result.return_value = '{"text":"помощник длинная команда"}'
        r._decoder = Mock(return_value=decoder)
        r._accurate = Mock(return_value="помощник длинная команда")
        def stream(**kwargs):
            class Input:
                def __enter__(self):
                    for _ in range(2):
                        kwargs["callback"](b"\x00\x00" * 1000, 1000, None, None)
                def __exit__(self, *args):
                    pass
            return Input()
        with patch("sounddevice.query_devices", return_value={"default_samplerate": 16000}), patch("sounddevice.RawInputStream", side_effect=stream), patch("local_mode.speech.time.monotonic", side_effect=[0, 0.1, 3.1]):
            self.assertEqual(r.listen(timeout=3), "помощник длинная команда")
        self.assertEqual(len(r._accurate.call_args.args[0]), 4000)


    def test_spoken_messages_require_verified_result(self):
        from local_mode.results import user_message
        self.assertIn("Не удалось", user_message({"ok": True, "action": "maximize_window", "verified": False}, locale="ru"))
        self.assertIn("развёрнуто", user_message({"ok": True, "action": "maximize_window", "verified": True}, locale="ru"))
        self.assertIn("YouTube играет", user_message({"ok": True, "action": "youtube_play", "verified": True}, locale="ru"))


def test_english_confirmation_buttons_use_same_pending_gate():
    from local_mode.assistant import LocalAgent
    from local_mode.config import Settings
    with patch("local_mode.assistant.WindowController"), patch("local_mode.assistant.YouTubePlayer"):
        bot = LocalAgent(Settings())
    for word, approved in [("confirm", True), ("cancel", False)]:
        bot.tools.pending = ("local", "cmd_control", {"task": "fixture", "command": "echo fixture"})
        with patch.object(bot.tools, "_invoke", return_value={"ok": True}) as execute:
            result = bot.run(word)
            assert execute.call_count == int(approved)
            assert bot.tools.pending is None
            if approved:
                assert result["ok"]
