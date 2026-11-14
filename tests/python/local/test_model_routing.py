"""Проверки квот и переключения без сети, кликов и запуска приложений."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import httpx
from google.genai import types
from google.genai.errors import APIError
from local_mode import gemini
from local_mode.config import Settings, save_model_preferences
from local_mode.routing import ModelRouter, portable_history
from local_mode.models import DEFAULT_MODEL, model_chain
from local_mode.assistant import LocalAgent


def error(code=429, details=None):
    return APIError(code, {"error": {"message": "synthetic", "details": details or []}})


def daily():
    return error(details=[{"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}])


def response(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))])


class RoutingTests(unittest.TestCase):
    def test_daily_and_zero_quota_are_not_retried(self):
        for failure in [daily(), error(details=[{"violations": [{"quotaValue": "0"}]}])]:
            client = Mock()
            client.models.generate_content.side_effect = failure
            with self.assertRaises(APIError):
                gemini.generate.retry_with(wait=lambda _: 0)(client, DEFAULT_MODEL, [], types.GenerateContentConfig())
            self.assertEqual(client.models.generate_content.call_count, 1)

    def test_transient_attempt_budget_with_and_without_reserve(self):
        for reserve, attempts in [(True, 3), (False, 5)]:
            client = Mock()
            client.models.generate_content.side_effect = error(503)
            with self.assertRaises(APIError):
                gemini.generate.retry_with(wait=lambda _: 0)(client, DEFAULT_MODEL, [], types.GenerateContentConfig(), fallback_available=reserve)
            self.assertEqual(client.models.generate_content.call_count, attempts)

    def test_exhausted_chain_terminates(self):
        router = ModelRouter("gemini-3.6-flash")
        generate = Mock(side_effect=daily())
        with self.assertRaises(APIError):
            router.generate(Mock(), contents="test", generator=generate)
        self.assertEqual(generate.call_count, len(router.models))
        self.assertEqual(router.active_model, "gemini-3.1-flash-lite")

    def test_retry_delay_and_minute_quota(self):
        failure = error(details=[{"retryDelay": "12.5s"}])
        state = Mock(attempt_number=1)
        state.outcome.exception.return_value = failure
        self.assertEqual(gemini.wait_for_retry(state), 12.5)
        self.assertTrue(gemini.is_transient(failure))
        self.assertFalse(gemini.is_transient(error(details=[{"retryDelay": "90s"}])))
        self.assertIn("daily quota", gemini.safe_error(daily()))
        self.assertNotIn("daily quota", gemini.safe_error(error()))

    def test_fallback_stays_active_and_preserves_configuration(self):
        router = ModelRouter("gemini-3.6-flash")
        generate = Mock(side_effect=[daily(), response(types.Part(text="OK")), response(types.Part(text="OK"))])
        config = types.GenerateContentConfig(max_output_tokens=40)
        router.generate(Mock(), contents="test", config=config, generator=generate)
        router.generate(Mock(), contents="next", config=config, generator=generate)
        self.assertEqual([c.args[1] for c in generate.call_args_list], ["gemini-3.6-flash", DEFAULT_MODEL, DEFAULT_MODEL])
        self.assertEqual(config.max_output_tokens, 40)

    def test_auth_network_and_disabled_fallback(self):
        for failure in [error(403), error(400), httpx.ConnectError("synthetic")]:
            generate = Mock(side_effect=failure)
            router = ModelRouter("gemini-3.6-flash")
            with self.assertRaises(type(failure)):
                router.generate(Mock(), contents="test", generator=generate)
            self.assertEqual(generate.call_count, 1)
        self.assertEqual(model_chain(DEFAULT_MODEL, False), (DEFAULT_MODEL,))
        with self.assertRaises(ValueError):
            model_chain("paid-only-model")

    def test_portable_history_drops_signatures_but_keeps_results_and_image(self):
        history = [types.Content(role="model", parts=[types.Part(text="hidden", thought=True),
                   types.Part(function_call=types.FunctionCall(name="tool", args={"x": 1}, id="old"), thought_signature=b"foreign")]),
                   types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(name="tool", response={"ok": True})),
                   types.Part.from_bytes(data=b"image", mime_type="image/png")])]
        portable = portable_history(history)
        self.assertIn("Результат уже выполненного", portable[1].parts[0].text)
        self.assertEqual(portable[1].parts[1].inline_data.data, b"image")
        self.assertTrue(all(not p.thought_signature and not p.function_call and not p.function_response for c in portable for p in c.parts))
        self.assertEqual(history[0].parts[1].thought_signature, b"foreign")

    def test_preferences_persist_without_losing_browser(self):
        with tempfile.TemporaryDirectory() as folder, patch("local_mode.config.ROOT", Path(folder)), patch.dict("os.environ", {}, clear=True):
            path = Path(folder) / "data" / "preferences.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"browser_channel": "msedge"}))
            save_model_preferences(Settings(model="gemini-3.6-flash", auto_fallback=False))
            settings = Settings.from_env()
            self.assertEqual(settings.model, "gemini-3.6-flash")
            self.assertFalse(settings.auto_fallback)
            self.assertEqual(settings.browser_channel, "msedge")
            path.write_text('{"auto_fallback": "false"}')
            with self.assertRaises(ValueError):
                Settings.from_env()

    @patch("local_mode.assistant.WindowController")
    @patch("local_mode.assistant.YouTubePlayer")
    @patch("local_mode.assistant.gemini.create_client")
    @patch("local_mode.assistant.gemini.generate")
    def test_tool_is_not_replayed_with_new_id_after_fallback(self, generate, create, youtube, windows):
        def call(identifier):
            return response(types.Part(function_call=types.FunctionCall(name="web_search", args={"query": "test"}, id=identifier)))
        generate.side_effect = [call("original"), daily(), call("new-model-id"), response(types.Part(text="Готово"))]
        bot = LocalAgent(Settings(model="gemini-3.6-flash"))
        with patch.object(bot.tools, "execute", return_value={"ok": True}) as execute:
            self.assertTrue(bot.run("Найди информацию по запросу test")["ok"])
            execute.assert_called_once()
        self.assertEqual(bot.router.active_model, DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
