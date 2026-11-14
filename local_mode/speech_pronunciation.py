"""Произносимая латиница для русских TTS: локальный eSpeak, без облака."""
from __future__ import annotations
from functools import lru_cache
import re
import unicodedata
from local_mode.speech_numbers import numbers_to_words

_WORDS = {
    "youtube":"ютуб", "google":"гугл", "chrome":"хром", "gemini":"джемини",
    "jarvis":"джарвис", "silero":"силеро", "supertonic":"супертоник",
    "piper":"пайпер", "playwright":"плейрайт", "windows":"виндоус",
    "microsoft":"майкрософт", "github":"гитхаб", "python":"пайтон",
    "vulkan":"вулкан", "openai":"оупен эй ай", "chatgpt":"чат джи пи ти",
    "api":"эй пи ай", "tts":"ти ти эс", "gpu":"джи пи ю", "cpu":"си пи ю",
    "url":"ю ар эл", "http":"эйч ти ти пи", "https":"эйч ти ти пи эс",
    "hello":"хэллоу", "world":"ворлд",
}
_LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz", (
    "эй", "би", "си", "ди", "и", "эф", "джи", "эйч", "ай", "джей", "кей", "эл", "эм",
    "эн", "оу", "пи", "кью", "ар", "эс", "ти", "ю", "ви", "дабл ю", "экс", "уай", "зед")))
_IPA = {
    "ju":"ю", "aɪ":"ай", "aʊ":"ау", "eɪ":"эй", "oʊ":"оу", "ɔɪ":"ой", "dʒ":"дж", "tʃ":"ч",
    "ɚ":"эр", "ɝ":"ёр", "ɜ":"ё", "ə":"э", "æ":"э", "ɛ":"э", "ɪ":"и", "ᵻ":"и",
    "ʊ":"у", "ʌ":"а", "ɑ":"а", "ɒ":"о", "ɔ":"о", "ɐ":"а", "ɡ":"г", "ɹ":"р",
    "ʃ":"ш", "ʒ":"ж", "θ":"с", "ð":"з", "ŋ":"нг", "ɾ":"р", "ɫ":"л", "ʔ":"",
    "a":"а", "b":"б", "d":"д", "e":"э", "f":"ф", "g":"г", "h":"х", "i":"и", "j":"й",
    "k":"к", "l":"л", "m":"м", "n":"н", "o":"о", "p":"п", "r":"р", "s":"с", "t":"т",
    "u":"у", "v":"в", "w":"у", "x":"х", "z":"з", "c":"к", "q":"к", "y":"и",
}
_phonemizer = None


def _spell(word: str) -> str:
    return " ".join(_LETTERS[letter.lower()] for letter in word if letter.isascii() and letter.isalpha())


@lru_cache(maxsize=2048)
def pronounce_word(word: str) -> str:
    global _phonemizer
    lower = word.lower().replace("’", "'")
    if lower in _WORDS:
        return _WORDS[lower]
    if word.isupper() and len(word) <= 8:
        return _spell(word)
    try:
        from piper.phonemize_espeak import EspeakPhonemizer
        from piper.voice import _ESPEAK_PHONEMIZER_LOCK
        # eSpeak использует глобальное состояние: тот же lock, что у Piper.
        with _ESPEAK_PHONEMIZER_LOCK:
            if _phonemizer is None:
                _phonemizer = EspeakPhonemizer()
            phones = " ".join("".join(part) for part in _phonemizer.phonemize("en-us",lower))
        phones = "".join(c for c in unicodedata.normalize("NFD",phones)
                         if not unicodedata.combining(c) and c not in "ˈˌːˑʲ")
        output=[]
        index=0
        while index<len(phones):
            pair=phones[index:index+2]
            if pair in _IPA:
                output.append(_IPA[pair]);index+=2
            elif phones[index] in _IPA:
                output.append(_IPA[phones[index]]);index+=1
            elif phones[index].isspace() or phones[index] in ",.!?-":
                output.append(phones[index]);index+=1
            else:
                # Неизвестный звук не выбрасывает слово: читаем его по буквам.
                return _spell(word)
        value="".join(output).strip().replace("нгг","нг")
        return value if re.search("[а-яё]",value) else _spell(word)
    except (ImportError, OSError, RuntimeError, ValueError):
        return _spell(word)


def russian_pronunciation(text: str) -> str:
    """Меняем только текст для синтеза; русский текст и пунктуация сохраняются."""
    return numbers_to_words(re.sub(r"[A-Za-z]+(?:['’][A-Za-z]+)*",lambda match:pronounce_word(match[0]),text))
