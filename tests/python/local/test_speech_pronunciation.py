"""Латинские слова доходят до русских моделей, а не исчезают в их фильтрах."""
import re
from unittest.mock import Mock,patch
import pytest
from local_mode import speech_pronunciation as module


@pytest.fixture(autouse=True)
def reset_cache():
    module.pronounce_word.cache_clear()
    yield
    module.pronounce_word.cache_clear()


def test_russian_text_and_punctuation_are_unchanged():
    text="Привет! Всё готово?\n\nМузыка играет…"
    assert module.russian_pronunciation(text)==text


def test_common_browser_names_and_acronyms_are_not_dropped():
    text="Открой Google Chrome и YouTube через Gemini API."
    spoken=module.russian_pronunciation(text)
    assert spoken=="Открой гугл хром и ютуб через джемини эй пи ай."
    assert not re.search("[A-Za-z]",spoken)
    assert text=="Открой Google Chrome и YouTube через Gemini API."


def test_native_phonemizer_handles_unknown_english_word():
    spoken=module.russian_pronunciation("beautiful music")
    assert re.search("[а-яё]",spoken)
    assert not re.search("[A-Za-z]",spoken)
    assert len(spoken.split())==2


def test_unknown_phoneme_falls_back_to_letters_instead_of_silence():
    fake=Mock();fake.phonemize.return_value=[list("?")]
    with patch.object(module,"_phonemizer",fake):
        assert module.russian_pronunciation("Zoqword")=="зед оу кью дабл ю оу ар ди"


def test_silero_receives_pronounceable_text_inside_generated_ssml():
    from local_mode.silero_voice import RussianVoice
    from local_mode.speech_text import silero_ssml
    engine=RussianVoice.__new__(RussianVoice);engine._ssml_enabled=True;engine.model=Mock()
    engine.audio("Hello world!", "xenia")
    args=engine.model.apply_tts.call_args.kwargs
    assert args["ssml_text"]==silero_ssml("хэллоу ворлд!")
    assert args["speaker"]=="xenia"


def test_silero_plain_fallback_keeps_english_pronunciation():
    from local_mode.silero_voice import RussianVoice
    engine=RussianVoice.__new__(RussianVoice);engine._ssml_enabled=False;engine.model=Mock()
    engine.audio("Google Chrome", "kseniya")
    assert engine.model.apply_tts.call_args.kwargs["text"]=="гугл хром"
