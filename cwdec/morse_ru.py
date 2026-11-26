# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Russian Morse table: Morse code -> Cyrillic letter.

Russian amateur practice sends Cyrillic text using the same dot/dash patterns as
the phonetically similar Latin letters, plus a few extra patterns that have no
Latin letter equivalent (Ж, З, Х, Ц, Ч, Ш, Щ, Ъ, Ы, Ь, Э, Ю, Я).

We therefore convert at the *code* level, not the letter level: the same code can
mean "H" in Latin and "Х" in Russian, and only the code is unambiguous.
"""
from __future__ import annotations

RU_MORSE = {
    ".-": "А", "-...": "Б", ".--": "В", "--.": "Г", "-..": "Д",
    ".": "Е", "...-": "Ж", "--..": "З", "..": "И", ".---": "Й",
    "-.-": "К", ".-..": "Л", "--": "М", "-.": "Н", "---": "О",
    ".--.": "П", ".-.": "Р", "...": "С", "-": "Т", "..-": "У",
    "..-.": "Ф", "....": "Х", "-.-.": "Ц", "---.": "Ч", "----": "Ш",
    "--.-": "Щ", "--.--": "Ъ", "--.--": "Ъ", "-..-.": "Ъ", "-.--": "Ы", "-..-": "Ь", "..-..": "Э",
    "..--": "Ю", ".-.-": "Я",
    # digits and punctuation are shared with the international table
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", ".----.": "'",
    "-.-.--": "!", "-..-.": "/", "-.--.": "(", "-.--.-": ")",
    "---...": ":", "-.-.-.": ";", "-...-": "=", ".-.-.": "+",
    "-....-": "-", "..--.-": "_", ".-..-.": '"',
}


def to_cyrillic(codes: list[str]) -> str:
    """Render a list of Morse codes as Cyrillic text (unknown codes become '?')."""
    out: list[str] = []
    for code in codes:
        if not code:
            continue
        if code == " ":
            out.append(" ")
            continue
        ch = RU_MORSE.get(code)
        out.append(ch if ch else "?")
    return " ".join("".join(out).split())


# 拉丁字母 -> 西里尔：按"同一个摩斯码"对应，而不是按字母形状
def _build_latin_map() -> dict[str, str]:
    from .synth import MORSE
    out: dict[str, str] = {}
    for ch, code in MORSE.items():
        if ch.isascii() and ch.isalpha():
            ru = RU_MORSE.get(code)
            if ru and ru.isalpha():
                out[ch] = ru
    return out


LATIN_TO_CYRILLIC = _build_latin_map()


# 呼号、Q 简语、常用英文缩写保持拉丁不变（俄国火腿也用拉丁发呼号）
_KEEP = {
    "CQ", "DE", "K", "KN", "R", "BK", "SK", "AR", "TU", "TNX", "RST", "PSE",
    "QRL", "QRZ", "QSY", "QRS", "QRM", "QRN", "QTH", "QSL", "QRP", "QRO",
    "GE", "GM", "GA", "GN", "OM", "YL", "XYL", "ES", "GL", "GD", "HR", "UR",
    "AGN", "RPT", "TEST", "DX", "VY", "FB", "CU", "HPE", "ANT", "RIG", "WX",
}


def latin_to_cyrillic(text: str) -> str:
    """按"同码"把拉丁文本转成西里尔（仅显示用）。

    呼号（含数字）、Q 简语和常用缩写保持拉丁，符合实际通联习惯。
    """
    out: list[str] = []
    for tok in text.split(" "):
        if not tok or any(ch.isdigit() for ch in tok) or tok.upper() in _KEEP:
            out.append(tok)
        else:
            out.append("".join(LATIN_TO_CYRILLIC.get(ch, ch) for ch in tok))
    return " ".join(out)
