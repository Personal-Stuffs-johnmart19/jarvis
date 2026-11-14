"""Проверки локальных команд, Win32-контракта, Gemini и YouTube без UI-действий."""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

from google.genai import types
from google.genai.errors import APIError
from pydantic import ValidationError
from local_mode.assistant import LocalAgent, Request, local_request
from local_mode.config import Settings
from local_mode import gemini
from local_mode.windows import Window, WindowController
from local_mode.youtube import YouTubePlayer


def model_response(*parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model', parts=list(parts)))])


def tool_call(identifier: str = 'call1', action: str = 'maximize_window') -> types.GenerateContentResponse:
    return model_response(types.Part(function_call=types.FunctionCall(name='computer_action', id=identifier,
                                                                      args={'action': action}), thought_signature=b'\xff\xfe'))


class RoutingTests(unittest.TestCase):
    def test_required_russian_commands(self) -> None:
        settings = Settings()
        for phrase in ['Разверни окно!', 'разверни активное окно', 'разверни окно на весь экран']:
            self.assertEqual(local_request(phrase, settings).action, 'maximize_window')
        for phrase in ['включи музыку на YouTube', 'включи музыку на ютубе', 'включи музыку в ютубе']:
            self.assertEqual(local_request(phrase, settings).action, 'youtube_play')
        self.assertEqual(local_request('включи музыку на YouTube джаз', settings).query, 'джаз')

    def test_negation_and_incidental_mentions_are_not_local_actions(self) -> None:
        for phrase in ['не включай музыку', 'что значит разверни окно', 'если скажу включи музыку на YouTube', 'не разверни окно']:
            self.assertIsNone(local_request(phrase, Settings()))

    def test_strict_tool_arguments(self) -> None:
        for value in [{'action': 'shell'}, {'action': 'maximize_window', 'extra': True},
                      {'action': 'youtube_play', 'query': 123}]:
            with self.assertRaises(ValidationError):
                Request.model_validate(value)

    def test_paid_model_not_accepted(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(model='gemini-4-argon')

    @patch('local_mode.assistant.WindowController')
    @patch('local_mode.assistant.YouTubePlayer')
    @patch('local_mode.assistant.gemini.create_client')
    @patch('local_mode.browser_profiles.read_preferences', return_value={'mode':'new','profile':''})
    def test_local_actions_do_not_call_gemini(self, preferences: Mock, create: Mock, youtube: Mock, windows: Mock) -> None:
        bot = LocalAgent(Settings())
        bot.run('разверни окно')
        bot.run('включи музыку на YouTube')
        windows.return_value.maximize.assert_called_once()
        youtube.return_value.play.assert_called_once()
        create.assert_not_called()


class WindowsTests(unittest.TestCase):
    def controller(self) -> WindowController:
        value = WindowController.__new__(WindowController)
        value.api = Mock()
        value.describe = Mock(return_value=Window(100, 'Test', False))
        return value

    def test_checks_native_maximized_state(self) -> None:
        windows = self.controller()
        windows.api.IsZoomed.return_value = True
        result = windows.maximize(target=Window(100, 'Test', False))
        self.assertTrue(result['verified'])
        self.assertTrue(result['changed'])
        windows.api.ShowWindowAsync.assert_called_once_with(100, 3)

    @patch('local_mode.windows.time.monotonic', side_effect=[0, 4])
    def test_does_not_claim_success_if_native_state_did_not_change(self, clock: Mock) -> None:
        windows = self.controller()
        with self.assertRaisesRegex(RuntimeError, 'не подтвердила'):
            windows.maximize(target=Window(100, 'Test', False))

    def test_stale_handle_rejected(self) -> None:
        windows = self.controller()
        windows.describe.return_value = Window(100, 'Other title', False)
        with self.assertRaises(RuntimeError):
            windows.maximize(target=Window(100, 'Test', False))
        windows.api.ShowWindowAsync.assert_not_called()

    def test_ambiguous_title_requires_clarification(self) -> None:
        windows = self.controller()
        windows.list_windows = Mock(return_value=[Window(1, 'Chrome one', False), Window(2, 'Chrome two', False)])
        with self.assertRaisesRegex(ValueError, 'Уточните'):
            windows.maximize('Chrome')
        windows.api.ShowWindowAsync.assert_not_called()

    def test_exact_title_wins(self) -> None:
        windows = self.controller()
        windows.list_windows = Mock(return_value=[Window(100, 'Test', False), Window(2, 'Test 2', False)])
        windows.api.IsZoomed.return_value = True
        self.assertTrue(windows.maximize('Test')['verified'])


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        self.profile_mode = patch('local_mode.browser_profiles.read_preferences', return_value={"mode": "new", "profile": ""})
        self.profile_mode.start()
        self.addCleanup(self.profile_mode.stop)

    def player(self) -> YouTubePlayer:
        player = YouTubePlayer(Path('data/test-profile'))
        player.page = Mock()
        player.page.is_closed.return_value = False
        player._ensure = Mock()
        player._cookie_consent = Mock()
        return player

    def state(self, seconds: float, **overrides: object) -> dict:
        return {'video': True, 'ready_state': 4, 'paused': False, 'muted': False, 'volume': .25,
                'current_time': seconds, 'ended': False, 'ad_showing': False, **overrides}

    def test_requires_media_time_progress_and_unmuted_audio(self) -> None:
        player = self.player()
        player.state = Mock(side_effect=[self.state(0), self.state(.75)])
        result = player.play('музыка & джаз')
        self.assertTrue(result['verified'])
        self.assertEqual(result['progress_seconds'], .75)
        self.assertIn('%26', player.page.goto.call_args.args[0])

    @patch('local_mode.youtube.time.monotonic', side_effect=[0, 1, 70])
    def test_static_or_paused_video_is_not_success(self, clock: Mock) -> None:
        player = self.player()
        player.state = Mock(return_value=self.state(0, paused=True))
        with self.assertRaisesRegex(RuntimeError, 'не подтвердил'):
            player.play('музыка')

    @patch('local_mode.youtube.time.monotonic', side_effect=[0, 1, 70])
    def test_advertisement_does_not_count_as_music(self, clock: Mock) -> None:
        player = self.player()
        player.state = Mock(return_value=self.state(10, ad_showing=True))
        with self.assertRaises(RuntimeError):
            player.play('музыка')

    def test_player_error_is_reported(self) -> None:
        player = self.player()
        player.state = Mock(return_value=self.state(0, error='unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'ошибку плеера'):
            player.play('музыка')

    def test_pause_uses_actual_state(self) -> None:
        player = self.player()
        player.state = Mock(return_value=self.state(10, paused=True))
        self.assertTrue(player.pause()['verified'])

    def test_cancellation_prevents_browser_input(self) -> None:
        player = self.player()
        player.stop.set()
        with self.assertRaisesRegex(RuntimeError, 'отменено'):
            player.play('музыка')
        player.page.locator.return_value.first.click.assert_not_called()


class GeminiTests(unittest.TestCase):
    def test_exact_retry_policy(self) -> None:
        client = Mock()
        client.models.generate_content.side_effect = APIError(503, {'error': {'message': 'busy'}})
        call = gemini.generate.retry_with(wait=lambda state: 0)
        with self.assertRaises(APIError):
            call(client, 'test', [], types.GenerateContentConfig())
        self.assertEqual(client.models.generate_content.call_count, 5)
        self.assertEqual([gemini.generate.retry.wait(Mock(attempt_number=i)) for i in range(1, 6)], [2, 2, 4, 8, 10])

    def test_invalid_key_is_not_retried(self) -> None:
        client = Mock()
        client.models.generate_content.side_effect = APIError(403, {'error': {'message': 'bad key'}})
        with self.assertRaises(APIError):
            gemini.generate(client, 'test', [], types.GenerateContentConfig())
        self.assertEqual(client.models.generate_content.call_count, 1)

    @patch.dict(os.environ, {}, clear=True)
    def test_key_optional_until_generative_command(self) -> None:
        with patch('local_mode.gemini.KEY_FILE') as stored, patch('local_mode.gemini.BOOTSTRAP_FILE') as bootstrap:
            stored.is_file.return_value = False
            bootstrap.is_file.return_value = False
            self.assertFalse(gemini.has_key())
            with self.assertRaisesRegex(ValueError, 'основные команды'):
                gemini.create_client()

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-secret'}, clear=True)
    def test_secret_redacted(self) -> None:
        self.assertNotIn('test-secret', gemini.safe_error(ValueError('test-secret')))

    @patch('local_mode.assistant.WindowController')
    @patch('local_mode.assistant.YouTubePlayer')
    @patch('local_mode.assistant.gemini.create_client')
    @patch('local_mode.assistant.gemini.generate')
    def test_sdk_signature_and_id_preserved(self, generate: Mock, create: Mock, youtube: Mock, windows: Mock) -> None:
        windows.return_value.maximize.return_value = {'ok': True, 'verified': True}
        generate.side_effect = [tool_call(), model_response(types.Part(text='Готово'))]
        bot = LocalAgent(Settings())
        result = bot.run('Пожалуйста, сделай текущее приложение большим')
        self.assertTrue(result['verified'])
        contents = generate.call_args.args[2]
        self.assertEqual(contents[1].parts[0].thought_signature, b'\xff\xfe')
        self.assertEqual(contents[2].parts[0].function_response.id, 'call1')
        self.assertEqual(generate.call_args.args[3].http_options.retry_options.attempts, 1)

    @patch('local_mode.assistant.WindowController')
    @patch('local_mode.assistant.YouTubePlayer')
    @patch('local_mode.assistant.gemini.create_client')
    @patch('local_mode.assistant.gemini.generate')
    def test_repeated_id_does_not_repeat_action(self, generate: Mock, create: Mock, youtube: Mock, windows: Mock) -> None:
        windows.return_value.maximize.return_value = {'ok': True, 'verified': True}
        generate.side_effect = [tool_call(), tool_call(), model_response(types.Part(text='Готово'))]
        LocalAgent(Settings()).run('Сделай приложение большим')
        windows.return_value.maximize.assert_called_once()

    @patch('local_mode.assistant.WindowController')
    @patch('local_mode.assistant.YouTubePlayer')
    def test_cancellation_prevents_native_actions(self, youtube: Mock, windows: Mock) -> None:
        event = threading.Event()
        bot = LocalAgent(Settings(), event)
        event.set()
        with self.assertRaises(RuntimeError):
            bot.run('разверни окно')
        windows.return_value.maximize.assert_not_called()


class CredentialTests(unittest.TestCase):
    def test_dpapi_roundtrip_is_not_plaintext(self) -> None:
        from local_mode.config import ROOT
        from local_mode.credentials import save_key, load_key
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as folder:
            target = Path(folder) / 'key.dpapi'
            token = 'synthetic-key-for-local-test'
            save_key(token, target)
            self.assertNotIn(token.encode(), target.read_bytes())
            self.assertEqual(load_key(target), token)

    def test_corrupt_encrypted_key_has_clear_error(self) -> None:
        from local_mode.config import ROOT
        from local_mode.credentials import load_key
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as folder:
            target = Path(folder) / 'key.dpapi'
            target.write_bytes(b'corrupt')
            with self.assertRaisesRegex(RuntimeError, 'DPAPI'):
                load_key(target)

    def test_bootstrap_import_on_new_install(self) -> None:
        from local_mode.config import ROOT
        from local_mode import credentials
        with tempfile.TemporaryDirectory(dir=ROOT / '.cache') as folder:
            target = Path(folder) / 'key.dpapi'
            bootstrap = Path(folder) / 'bootstrap.json'
            token = 'synthetic-bootstrap-key-for-tests'
            bootstrap.write_text(json.dumps({'gemini_api_key': token}), encoding='utf-8')
            with patch.object(credentials, 'KEY_FILE', target), patch.object(credentials, 'BOOTSTRAP_FILE', bootstrap):
                self.assertEqual(credentials.load_key(target), token)
            self.assertTrue(target.is_file())
            self.assertNotIn(token.encode(), target.read_bytes())


if __name__ == '__main__':
    unittest.main()
