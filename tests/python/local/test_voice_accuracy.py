"""Регрессии контекста STT и доступности кнопок без Computer Use."""
from unittest.mock import Mock
import numpy as np
import pytest
from local_mode.whisper_voice import AccurateRussianRecognizer


@pytest.mark.parametrize("text", ["Включи музыку на YouTube.", "Не включай музыку на YouTube.", "Расскажи о погоде в Киеве."])
def test_context_preserves_decoded_meaning_and_audio_edges(text):
    recognizer = AccurateRussianRecognizer.__new__(AccurateRussianRecognizer)
    recognizer.model = Mock()
    recognizer.model.transcribe.return_value = (iter([Mock(text=text)]), None)
    samples = np.arange(3200, dtype=np.int16)
    assert recognizer.transcribe(samples.tobytes(), 16000) == text
    audio = recognizer.model.transcribe.call_args.args[0]
    np.testing.assert_array_equal(audio[6400:-6400], samples.astype(np.float32)/32768)
    assert not audio[:6400].any() and not audio[-6400:].any()
    options = recognizer.model.transcribe.call_args.kwargs
    assert "Включи музыку на YouTube." in options["initial_prompt"]
    assert options["vad_filter"] and not options["condition_on_previous_text"]
