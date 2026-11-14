"""Один результат и одно подтверждение, без дублирующего TTS инструментов."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch
import pytest
from google.genai import types
from local_mode.assistant import LocalAgent
from local_mode.config import Settings
from local_mode.results import user_message, spoken_message
from local_mode.speech_numbers import numbers_to_words
from local_mode.tools import ToolRegistry


@pytest.mark.parametrize("text,expected", [
    ("0 2 15", "ноль два пятнадцать"),
    ("1500 рублей", "одна тысяча пятьсот рублей"),
    ("1 500 рублей", "одна тысяча пятьсот рублей"),
    ("2001", "две тысячи один"),
    ("-25%", "минус двадцать пять процентов"),
    ("21%", "двадцать один процент"),
    ("3,14", "три запятая один четыре"),
    ("12:30", "двенадцать тридцать"),
    ("007", "ноль ноль семь"),
    ("Dota 2", "Dota два"),
])
def test_spoken_numbers(text, expected):
    assert numbers_to_words(text) == expected
    assert numbers_to_words(expected) == expected


def test_confirmed_tool_runs_once_and_does_not_speak_its_own_result():
    player=Mock(); speak=Mock(); calls=[]
    def handler(parameters, player=None, speak=None):
        calls.append(parameters)
        player.write_log("[GameUpdater] raw debug")
        speak("raw duplicate")
        return "Steam: Could not start Steam. | Epic: 'Dota 2' not found in Epic."
    response=types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model",parts=[types.Part(function_call=types.FunctionCall(name="game_updater",args={"action":"install","game_name":"Dota 2"}))]))])
    with patch("local_mode.assistant.WindowController"),patch("local_mode.assistant.YouTubePlayer"),patch("local_mode.assistant.gemini.create_client"),patch("local_mode.assistant.gemini.generate",return_value=response) as generate:
        bot=LocalAgent(Settings(),player=player,speak=speak)
        pending=bot.run("Установи Dota 2")
        assert pending["requires_confirmation"]
        assert not calls
        assert "game_name" not in spoken_message(pending) and "install" not in spoken_message(pending)
        with patch("local_mode.tools.importlib.import_module",return_value=SimpleNamespace(game_updater=handler)):
            result=bot.run("подтверждаю")
            again=bot.run("подтверждаю")
        assert len(calls)==1
        assert not result["ok"] and not again["ok"]
        assert "Could not start Steam" in user_message(result)
        assert "not found in Epic" in user_message(result)
        assert "Action completed" not in user_message(result)
        generate.assert_called_once()
        player.write_log.assert_not_called();speak.assert_not_called()


def test_background_notifications_survive_synchronous_feedback_filter():
    player=Mock();speak=Mock();registry=ToolRegistry(player,speak)
    feedback=registry.feedback()
    feedback["speak"]("duplicate")
    def later():
        feedback["player"].write_log("Фоновое событие")
        feedback["speak"]("Загрузка завершена")
    thread=threading.Thread(target=later);thread.start();thread.join(2)
    player.write_log.assert_called_once_with("Фоновое событие")
    speak.assert_called_once_with("Загрузка завершена")


def test_speech_excludes_code_tables_urls_and_long_diagnostics():
    result={"ok":True,"reply":"Готово.\n```powershell\nRemove-Item secret\n```\n| поле | значение |\nhttps://example.com/token=private"}
    spoken=spoken_message(result)
    assert "Готово" in spoken and "Remove-Item" not in spoken and "private" not in spoken and "|" not in spoken
    assert result["reply"].endswith("private")
    assert len(spoken_message({"ok":True,"reply":"Текст. "*500}))<1200
