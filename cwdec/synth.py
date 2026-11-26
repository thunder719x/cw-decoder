# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA (GitHub: thunder719x)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate synthetic CW audio for testing (no external deps)."""
from __future__ import annotations

import wave
import numpy as np

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    "/": "-..-.", "?": "..--..", ".": ".-.-.-", ",": "--..--",
}

# 俄文字母的摩斯码（与拉丁字母不完全相同，例如 Ч=---.、Ш=----、Ю=..--）
MORSE_CYRILLIC = {
    "А": ".-", "Б": "-...", "В": ".--", "Г": "--.", "Д": "-..", "Е": ".",
    "Ж": "...-", "З": "--..", "И": "..", "Й": ".---", "К": "-.-", "Л": ".-..",
    "М": "--", "Н": "-.", "О": "---", "П": ".--.", "Р": ".-.", "С": "...",
    "Т": "-", "У": "..-", "Ф": "..-.", "Х": "....", "Ц": "-.-.", "Ч": "---.",
    "Ш": "----", "Щ": "--.-", "Ъ": "--.--", "Ы": "-.--", "Ь": "-..-",
    "Э": "..-..", "Ю": "..--", "Я": ".-.-",
    "1": ".----", "2": "..---", "3": "...--", "4": "....-", "5": ".....",
    "6": "-....", "7": "--...", "8": "---..", "9": "----.", "0": "-----",
}
MORSE.update(MORSE_CYRILLIC)


def synth(text: str, wpm: float = 20.0, tone: float = 700.0,
          sr: int = 8000, snr_db: float | None = None,
          rise_ms: float = 5.0) -> np.ndarray:
    unit = 1.2 / wpm                      # seconds per dot
    parts: list[np.ndarray] = []
    t = 0.0

    def silence(dur: float) -> np.ndarray:
        return np.zeros(int(sr * dur), dtype=np.float32)

    def tone_burst(dur: float) -> np.ndarray:
        n = int(sr * dur)
        tt = np.arange(n) / sr
        sig = np.sin(2 * np.pi * tone * tt).astype(np.float32)
        k = max(1, int(sr * rise_ms / 1000.0))
        ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
        sig[:k] *= ramp
        sig[-k:] *= ramp[::-1]
        return sig

    first = True
    for word in text.upper().split():
        if not first:
            parts.append(silence(unit * 7))
        first = False
        for ci, ch in enumerate(word):
            code = MORSE.get(ch)
            if code is None:
                continue
            if ci:
                parts.append(silence(unit * 3))
            for si, sym in enumerate(code):
                if si:
                    parts.append(silence(unit))
                parts.append(tone_burst(unit if sym == "." else unit * 3))
        t += 0.0

    audio = np.concatenate([silence(unit * 2)] + parts + [silence(unit * 2)]) if parts else np.zeros(sr, np.float32)

    if snr_db is not None:
        p_sig = float(np.mean(audio ** 2) or 1e-12)
        p_noise = p_sig / (10 ** (snr_db / 10.0))
        audio = audio + np.random.normal(0, np.sqrt(p_noise), audio.size).astype(np.float32)

    peak = float(np.max(np.abs(audio)) or 1.0)
    return (audio / peak * 0.8).astype(np.float32)


def write_wav(path: str, audio: np.ndarray, sr: int = 8000) -> str:
    data = np.clip(audio, -1.0, 1.0)
    pcm = (data * 32767.0).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path
