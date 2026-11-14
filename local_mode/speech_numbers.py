"""Числа для русских TTS без сетевых запросов и дополнительных зависимостей."""
from __future__ import annotations
import re

_ONES = ("ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять")
_TEENS = ("десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать")
_TENS = ("", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто")
_HUNDREDS = ("", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот")
_GROUPS = (("", "", ""), ("тысяча", "тысячи", "тысяч"), ("миллион", "миллиона", "миллионов"), ("миллиард", "миллиарда", "миллиардов"), ("триллион", "триллиона", "триллионов"))


def _form(number: int, forms: tuple[str, str, str]) -> str:
    return forms[2] if 11 <= number % 100 <= 14 else forms[0] if number % 10 == 1 else forms[1] if 2 <= number % 10 <= 4 else forms[2]


def _integer(value: str) -> str:
    # Длинные идентификаторы и ведущие нули читаем по цифрам без потери данных.
    if len(value) > 15 or (len(value) > 1 and value.startswith("0")):
        return " ".join(_ONES[int(char)] for char in value)
    number = int(value)
    if not number:
        return _ONES[0]
    result = []
    group = 0
    while number:
        part = number % 1000
        if part:
            words = []
            if part >= 100: words.append(_HUNDREDS[part // 100])
            rest = part % 100
            if 10 <= rest < 20:
                words.append(_TEENS[rest - 10])
            else:
                if rest >= 20: words.append(_TENS[rest // 10])
                if rest % 10:
                    words.append(({1: "одна", 2: "две"}.get(rest % 10, _ONES[rest % 10])) if group == 1 else _ONES[rest % 10])
            if group: words.append(_form(part, _GROUPS[group]))
            result.insert(0, " ".join(words))
        number //= 1000
        group += 1
    return " ".join(result)


def numbers_to_words(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match[0]
        percent = token.rstrip().endswith("%")
        if percent: token = token.rstrip()[:-1].rstrip()
        token = re.sub(r"(?<=[0-9])[ \u00a0\u202f](?=[0-9])", "", token)
        sign = ""
        if token[0] in "+-−":
            sign = "плюс " if token[0] == "+" else "минус "
            token = token[1:]
        parts = re.split(r"([.,:])", token)
        words = [_integer(parts[0])]
        for index in range(1, len(parts), 2):
            separator, digits = parts[index:index+2]
            if separator == ":":
                words.append(_integer(digits))
            else:
                words.extend(["точка" if separator == "." else "запятая", " ".join(_ONES[int(char)] for char in digits)])
        if percent:
            words.append("процента" if len(parts) > 1 else _form(int(token[-2:]), ("процент", "процента", "процентов")))
        return sign + " ".join(words)
    return re.sub(r"[+−-]?(?:[0-9]{1,3}(?:[ \u00a0\u202f][0-9]{3})+|[0-9]+)(?:[.,:][0-9]+)*(?:[ \t]*%)?", replace, text)
