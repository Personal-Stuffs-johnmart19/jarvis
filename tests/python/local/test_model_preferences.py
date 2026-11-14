"""Сохранение предпочтений модели и сброс истории без UI и сети."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.routing import ModelRouter


class ApplySettingsTests(unittest.TestCase):
    def bot(self):
        bot = LocalAgent.__new__(LocalAgent)
        bot.settings = Settings()
        bot.router = ModelRouter(bot.settings.model)
        bot.turns = {"local": ["old history"]}
        bot.history = ["old history"]
        return bot

    def test_save_apply_and_reload(self):
        with tempfile.TemporaryDirectory() as folder, patch("local_mode.config.ROOT", Path(folder)), patch.dict("os.environ", {}, clear=True):
            bot = self.bot()
            updated = bot.configure_models("gemini-3.6-flash", False)
            self.assertEqual(updated, bot.settings)
            self.assertEqual(bot.router.models, ("gemini-3.6-flash",))
            self.assertEqual(bot.turns, {})
            self.assertEqual(bot.history, [])
            self.assertEqual(Settings.from_env().model, "gemini-3.6-flash")
            with patch.dict("os.environ", {}, clear=True):
                reloaded = Settings.from_env()
                self.assertEqual(reloaded.model, "gemini-3.6-flash")
                self.assertFalse(reloaded.auto_fallback)

    def test_write_failure_keeps_current_settings_and_history(self):
        bot = self.bot()
        router = bot.router
        with patch("local_mode.config.save_model_preferences", side_effect=PermissionError("synthetic")):
            with self.assertRaises(PermissionError):
                bot.configure_models("gemini-3.6-flash", False)
        self.assertIs(bot.router, router)
        self.assertEqual(bot.settings, Settings())
        self.assertTrue(bot.history)

    def test_invalid_choice_never_saved(self):
        bot = self.bot()
        with patch("local_mode.config.save_model_preferences") as save:
            with self.assertRaises(ValueError):
                bot.configure_models("unsupported-model", False)
            save.assert_not_called()
