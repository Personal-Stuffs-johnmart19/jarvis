"""Поток, подтверждения и скрытый Tk: без действий на рабочем столе."""
import threading
import unittest
from unittest.mock import Mock, patch
import httpx
from google.genai import types
from google.genai.errors import APIError
from local_mode import gemini
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.network.network_transport import RoutingTransport, RouteStream, NetworkRouteError
from local_mode.tools import ToolRegistry


def chunk(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(index=0,
        content=types.Content(role="model", parts=list(parts)))])


class StreamingTests(unittest.TestCase):
    def test_text_arrives_before_completion_and_preserves_tool_signature(self):
        client = Mock()
        updates = []
        call = types.Part(function_call=types.FunctionCall(name="system_status", args={}),
                          thought_signature=b"signature")
        def source():
            yield chunk(types.Part(text="При"))
            self.assertEqual(updates[-1], "При")
            yield chunk(types.Part(text="вет"), types.Part(text="скрыто", thought=True))
            yield chunk(call)
        client.models.generate_content_stream.return_value = source()
        response = gemini.generate_stream(client, "test", [], on_text=updates.append)
        self.assertEqual(updates, ["", "При", "Привет"])
        self.assertEqual(response.candidates[0].content.parts[-1].thought_signature, b"signature")
        self.assertEqual(response.function_calls[0].name, "system_status")

    def test_retry_replaces_partial_draft(self):
        client = Mock()
        updates = []
        def broken():
            yield chunk(types.Part(text="старое"))
            raise APIError(503, {"error": {"message": "test"}})
        def complete():
            yield chunk(types.Part(text="новое"))
        client.models.generate_content_stream.side_effect = [broken(), complete()]
        gemini.generate_stream.retry_with(wait=lambda _: 0)(client, "test", [], on_text=updates.append)
        self.assertEqual(updates, ["", "старое", "", "новое"])

    def test_sse_transport_does_not_read_ahead(self):
        read = []
        class Body(httpx.SyncByteStream):
            def __iter__(self):
                read.append(True)
                yield b"data: {}\n\n"
        route = RoutingTransport("system")
        inner = httpx.Client(transport=httpx.MockTransport(lambda request:
            httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())))
        with patch.object(route, "_new_client", return_value=inner), httpx.Client(transport=route) as client:
            with client.stream("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:streamGenerateContent") as response:
                self.assertEqual(read, [])
                self.assertTrue(response.read())
        self.assertEqual(read, [True])

    def test_stream_disconnect_is_reported_and_repeated_failure_requests_reserve(self):
        class Broken(httpx.SyncByteStream):
            def __iter__(self):
                yield b"data: partial"
                raise httpx.ReadError("disconnected")
        route = RoutingTransport("auto")
        route.active_proxy = "http://8.8.8.8:80"
        for _ in range(2):
            with self.assertRaises(httpx.ReadError):
                list(RouteStream(Broken(), route._stream_failed, route._stream_completed))
        with patch.object(route, "_advance", return_value=False) as advance:
            with self.assertRaises(NetworkRouteError):
                route.handle_request(httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models"))
            advance.assert_called_once()
        self.assertIn("http://8.8.8.8:80", route.rejected)

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.assistant.gemini.create_client")
    def test_incomplete_stream_never_executes_a_tool(self, create, *_):
        def broken():
            yield chunk(types.Part(function_call=types.FunctionCall(name="system_status", args={})))
            raise APIError(429, {"error": {"details": [{"violations": [{"quotaValue": "0"}]}]}})
        create.return_value.models.generate_content_stream.return_value = broken()
        bot = LocalAgent(Settings(auto_fallback=False))
        bot.on_text = Mock()
        with patch.object(bot.tools, "execute") as execute:
            with self.assertRaises(APIError):
                bot.run("Проверь компьютер")
            execute.assert_not_called()

    def test_cancel_closes_stream(self):
        closed = []
        def source():
            try:
                yield chunk(types.Part(text="текст"))
            finally:
                closed.append(True)
        stop = threading.Event()
        stop.set()
        client = Mock()
        client.models.generate_content_stream.return_value = source()
        with self.assertRaisesRegex(RuntimeError, "отменено"):
            gemini.generate_stream(client, "test", [], stop=stop)
        self.assertEqual(closed, [True])


class ConfirmationTests(unittest.TestCase):
    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    def test_yes_executes_once_and_cancel_does_not_execute(self, *_):
        bot = LocalAgent(Settings())
        args = {"task": "проверка", "command": "whoami", "visible": False}
        with patch.object(bot.tools, "_invoke", return_value={"ok": True}) as invoke:
            question = bot.tools.execute("cmd_control", args)
            self.assertIn("Command: whoami", question["reply"])
            self.assertNotIn('{"task"', question["reply"])
            self.assertTrue(bot.run("Да!")["ok"])
            invoke.assert_called_once()
            self.assertFalse(bot.run("подтверждаю")["ok"])
            bot.tools.execute("cmd_control", args)
            self.assertTrue(bot.run("отмена")["ok"])
            self.assertEqual(invoke.call_count, 1)


    def test_unsupported_shell_is_rejected_before_confirmation(self):
        registry = ToolRegistry()
        with patch("actions.cmd_control._run_silent") as execute:
            result = registry.execute("cmd_control", {"task": "время", "command": "powershell (Get-Date)"})
            self.assertFalse(result["ok"])
            self.assertNotIn("requires_confirmation", result)
            self.assertIsNone(registry.pending)
            execute.assert_not_called()

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    def test_time_uses_native_clock_without_model_or_shell(self, *_):
        bot = LocalAgent(Settings())
        with patch("local_mode.assistant.gemini.create_client") as client:
            result = bot.run("Сколько сейчас времени в Москве?")
            self.assertTrue(result["ok"])
            self.assertIn("in Moscow", result["reply"])
            client.assert_not_called()
