# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Classic DSP backend for CW decoding (no machine learning).

Pipeline
--------
WAV -> mono -> resample 8 kHz -> remove DC -> estimate tone
    -> FFT band-pass around the tone -> envelope -> adaptive threshold
    -> run-length encoding (marks / gaps) -> estimate unit length
    -> dot/dash classification -> Morse table -> text

The module is deliberately dependency-light: numpy only.
"""
from __future__ import annotations

import wave
import numpy as np

TARGET_SR = 8000

CHAR_GAP_UNITS = 2.0      # 字符内/字符间分界
WORD_GAP_UNITS = 4.5      # 字符间/词间分界（手键常偏短，4.5 比 5.0 更贴合现实）

MORSE: dict[str, str] = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", ".----.": "'",
    "-.-.--": "!", "-..-.": "/", "-.--.": "(", "-.--.-": ")",
    ".-...": "&", "---...": ":", "-.-.-.": ";", "-...-": "=",
    ".-.-.": "+", "-....-": "-", "..--.-": "_", ".-..-.": '"',
    "...-..-": "$", ".--.-.": "@",
}

# 俄文里"没有拉丁对应"的几个码：国际表中未使用，直接输出西里尔字母，
# 这样解出来的文本不会变成 "?"（有歧义的 Щ/Ж/Ь 仍按拉丁输出）
MORSE.update({
    "----": "Ш", "---.": "Ч", "..--": "Ю", "..-..": "Э", "--.--": "Ъ",
    ".-.-": "Я",
})


# ---------------------------------------------------------------- audio I/O
def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Read a PCM WAV file, return (mono float32 in [-1,1], sample rate)."""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())

    if sw == 1:      # 8-bit unsigned
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:    # 16-bit signed
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:    # 24-bit signed
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        data = (v.astype(np.float32) / 8388608.0)
    elif sw == 4:    # 32-bit signed
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sample width: {sw} bytes")

    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data.astype(np.float32), sr


def resample(x: np.ndarray, sr: int, target: int = TARGET_SR) -> np.ndarray:
    """Resample to `target` Hz.

    Decimation needs an anti-aliasing low-pass first, otherwise high-frequency
    images fold back into the CW band and destroy the signal.
    """
    x = x.astype(np.float32)
    if sr == target:
        return x

    if sr > target:                      # 降采样：先抗混叠
        try:
            from scipy.signal import butter, sosfiltfilt
            cutoff = 0.45 * target          # 3.6 kHz for 8 kHz target
            sos = butter(6, cutoff, btype="low", fs=sr, output="sos")
            x = sosfiltfilt(sos, x).astype(np.float32)
        except Exception:
            pass

    n_out = int(round(len(x) * target / sr))
    idx = np.linspace(0, len(x) - 1, n_out)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


# ---------------------------------------------------------------- analysis
def _parabolic_peak(freqs: np.ndarray, spec: np.ndarray, k: int) -> float:
    """Sub-bin peak position from a 3-point parabolic fit on log magnitude."""
    if k <= 0 or k >= len(spec) - 1:
        return float(freqs[k])
    a, b, c = (np.log(spec[k - 1] + 1e-20), np.log(spec[k] + 1e-20),
               np.log(spec[k + 1] + 1e-20))
    denom = a - 2.0 * b + c
    if denom == 0:
        return float(freqs[k])
    delta = float(np.clip(0.5 * (a - c) / denom, -1.0, 1.0))
    return float(freqs[k] + delta * (freqs[1] - freqs[0]))


def estimate_tone(x: np.ndarray, sr: int = TARGET_SR,
                  lo: float = 250.0, hi: float = 1600.0) -> float:
    """Find the dominant CW tone by spectral peak (sub-bin accurate)."""
    n = len(x)
    if n < 1024:
        return 700.0

    # 用"能量最高的若干帧"做平均谱：避开间隙，峰值更干净
    n_fft = 512
    hop = max(80, n // 400)
    frames_idx = np.arange(0, max(1, n - n_fft), hop)
    if frames_idx.size > 4000:
        frames_idx = frames_idx[:: frames_idx.size // 4000 + 1]
    seg = x[frames_idx[:, None] + np.arange(n_fft)[None, :]] * np.hanning(n_fft)[None, :]
    spec_mat = np.abs(np.fft.rfft(seg, axis=1))
    energy = (spec_mat ** 2).sum(axis=1)
    keep = max(3, int(len(energy) * 0.25))
    top = np.argsort(energy)[-keep:]
    spec = spec_mat[top].mean(axis=0)

    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    band = (freqs >= lo) & (freqs <= hi)
    if not band.any():
        return 700.0
    masked = np.where(band, spec, 0.0)
    k = int(np.argmax(masked))
    if float(masked[k]) <= 1e-12:          # 整段静音：别返回 0 Hz
        return 700.0
    return _parabolic_peak(freqs, spec, k)


def bandpass(x: np.ndarray, sr: int, center: float, width: float = 180.0) -> np.ndarray:
    """FFT brick-wall band-pass around `center` (offline, whole buffer)."""
    n = len(x)
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    mask = np.abs(freqs - center) <= width / 2.0
    spec = spec * mask
    return np.fft.irfft(spec, n=n).astype(np.float32)


def envelope(x: np.ndarray, sr: int = TARGET_SR, win_ms: float = 6.0) -> np.ndarray:
    """Rectify + moving-average low-pass -> envelope."""
    k = max(3, int(sr * win_ms / 1000.0))
    a = np.abs(x)
    ker = np.ones(k, dtype=np.float32) / k
    return np.convolve(a, ker, mode="same")


def _kmeans2(v: np.ndarray, iters: int = 25) -> tuple[float, float]:
    """1-D k-means with 2 clusters; returns (low_center, high_center)."""
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-9:
        return lo, hi
    c1, c2 = lo, hi
    for _ in range(iters):
        d1 = np.abs(v - c1)
        d2 = np.abs(v - c2)
        g1 = v[d1 <= d2]
        g2 = v[d1 > d2]
        if len(g1) == 0 or len(g2) == 0:
            break
        n1, n2 = g1.mean(), g2.mean()
        if abs(n1 - c1) < 1e-6 and abs(n2 - c2) < 1e-6:
            c1, c2 = n1, n2
            break
        c1, c2 = n1, n2
    return (min(c1, c2), max(c1, c2))


def binarize(env: np.ndarray) -> np.ndarray:
    """Adaptive threshold based on percentiles (robust to impulsive noise).

    CW is roughly 50% key-down, so the 20th percentile is a good noise floor and
    the 90th a good mark level. Percentiles ignore short spikes, which is exactly
    what QRN does to a mean-based threshold.
    """
    if env.size == 0:
        return np.zeros(0, dtype=bool)
    lo = float(np.percentile(env, 20))
    hi = float(np.percentile(env, 90))
    # 如果两档差别太小（比如长时间载波占了 60% 时间），百分位法失效，退回聚类
    if hi <= lo * 1.6:
        lo2, hi2 = _kmeans2(env.reshape(-1))
        if hi2 <= lo2:
            return np.zeros(env.size, dtype=bool)
        lo, hi = lo2, hi2

    # 自适应门限：动态范围被压缩时（系统响度均衡、AGC），
    # 按键尾部会被拖长，固定的 45% 门限会把尾部算成信号 → 全成"划"
    ratio_db = 20.0 * float(np.log10((hi + 1e-12) / (lo + 1e-12)))
    if ratio_db >= 13.0:
        frac = 0.45
    elif ratio_db >= 9.0:
        frac = 0.62
    elif ratio_db >= 5.0:
        frac = 0.72
    else:
        frac = 0.80
    thr = lo + (hi - lo) * frac
    return env > thr


def _runs(mask: np.ndarray) -> list[tuple[bool, int]]:
    """Run-length encode a boolean array."""
    if mask.size == 0:
        return []
    change = np.flatnonzero(np.diff(mask.astype(np.int8)) != 0) + 1
    bounds = np.concatenate(([0], change, [mask.size]))
    return [(bool(mask[bounds[i]]), int(bounds[i + 1] - bounds[i]))
            for i in range(len(bounds) - 1)]


def clean_runs(runs: list[tuple[bool, int]], sr: int,
               min_ms: float = 8.0) -> list[tuple[bool, int]]:
    """Drop key clicks / fill micro-gaps shorter than `min_ms`."""
    min_len = int(sr * min_ms / 1000.0)
    out: list[tuple[bool, int]] = []
    for state, ln in runs:
        if out and out[-1][0] == state:
            out[-1] = (state, out[-1][1] + ln)
        elif ln < min_len:
            # too short: flip into the surrounding state
            if out:
                out[-1] = (out[-1][0], out[-1][1] + ln)
            continue
        else:
            out.append((state, ln))
    return out


# ---------------------------------------------------------------- decoding
def estimate_unit(marks_ms: list[float]) -> float:
    """Estimate one Morse unit (ms) from mark durations.

    The median is used deliberately: dots normally outnumber dashes, and the median
    is immune to the handful of 8-20 ms glitches that a compressor or a bad
    connection injects (those used to drive the unit to ~16 ms and turn everything
    into "T").
    """
    if not marks_ms:
        return 60.0
    v = np.sort(np.asarray(marks_ms, dtype=np.float32))
    if v.size == 1:
        return float(v[0])
    med = float(np.median(v))
    # 如果中位数明显是"划"（点位不到 1/4），用 25 分位兜底
    p25 = float(np.percentile(v, 25))
    if p25 >= 15.0 and med > p25 * 1.9:
        med = p25
    return float(max(15.0, min(200.0, med)))


def _kmeans1d(v: np.ndarray, k: int, iters: int = 30) -> list[float]:
    """Simple 1-D k-means; returns sorted cluster centres."""
    if v.size == 0:
        return []
    qs = np.linspace(0, 100, k + 2)[1:-1]
    centers = np.percentile(v, qs).astype(float).tolist()
    for _ in range(iters):
        assign = np.argmin(np.abs(v[:, None] - np.array(centers)[None, :]), axis=1)
        new = []
        for i in range(k):
            part = v[assign == i]
            new.append(float(part.mean()) if part.size else centers[i])
        if np.allclose(new, centers):
            break
        centers = new
    return sorted(centers)


def gap_thresholds(gaps_ms: list[float], unit_ms: float) -> tuple[float, float]:
    """返回 (字母边界, 词边界)，单位是"单位时长"。

    元素间隔 ≈ 1 单位，所以字母边界固定在 2 附近。
    字母间隔和词间隔因人/因速度而异：
        · 标准 3 / 7 单位
        · 手键 5 / 9 单位
        · Farnsworth 慢速 18 / 36 单位（整体放大约 4 倍）
    因此词边界不适合写死，改为"跟着字母间隔走"：
        词边界 = 2.2 × 字母间隔中心      （3/7 与 18/36 都满足 ≈2.3 的比例）
    这样标准码、手键、Farnsworth 用同一套逻辑，不再依赖固定的 18 单位上限。
    """
    char_thr = 2.0
    if not gaps_ms or unit_ms <= 0:
        return char_thr, 4.5

    r = np.asarray(gaps_ms, dtype=float) / unit_ms

    # 长间隙的上限也随之放宽：Farnsworth 5 WPM 的词间隔能到 60+ 单位
    long_gaps = r[(r >= 2.0) & (r <= 90.0)]
    if long_gaps.size < 8:
        return char_thr, 4.5

    cen = np.exp(np.array(_kmeans1d(np.log(long_gaps), 2)))
    cen = sorted(float(c) for c in cen if c > 1.0)

    if len(cen) == 2 and cen[1] / max(cen[0], 1e-6) > 1.35:
        # 两簇分得开：低簇 = 字母间隔，高簇 = 词间隔
        # 分界取对数中点（几何平均）——对 3/7、5/9、18/36 都落在两簇正中间
        letter_c, word_c = cen[0], cen[1]
        # 分界：取几何平均，但不超过"字母间隔 × 1.25"。
        # 原因：少数词间隔会比正常值短很多（实测 ARRL 5 WPM 里出现 25 单位，
        # 而字母 19、词 43）。只用几何平均会把这个短词间隔并进上一个词。
        word_thr = float(min(np.sqrt(letter_c * word_c), 1.25 * letter_c))
    elif len(cen) == 2:
        # 两簇靠得太近（例如只有一个词的报文）：保守处理
        word_thr = max(4.5, 1.5 * cen[0])
    else:
        word_thr = max(4.5, 1.5 * (cen[0] if cen else 3.0))

    word_thr = max(word_thr, 3.8)

    char_thr = float(min(max(char_thr, 1.8), 3.2))
    # 上限从 18 放宽到 45：Farnsworth 5 WPM 的字母/词间隔在 18/36 单位
    word_thr = float(min(max(word_thr, char_thr + 1.4), 45.0))
    return char_thr, word_thr


def decode_symbols(runs: list[tuple[bool, int]], sr: int,
                   alphabet: str = "latin") -> dict:
    """Convert runs into Morse text plus debug data.

    Returns `codes`: the Morse pattern of every emitted character, aligned with
    `text` (space entries are " ").  Keeping the codes lets us re-render the same
    result as Cyrillic without re-decoding.
    """
    marks = [ln / sr * 1000.0 for st, ln in runs if st]
    gaps_ms = [ln / sr * 1000.0 for st, ln in runs if not st]
    unit = estimate_unit(marks)
    if unit <= 0:
        unit = 60.0

    # 全是"划"的文本（俄文 Ш=---- 、---. 等）没有短簇，
    # 单位会被估成"划长" → 划被当成点。用最短那簇"元素间隔"反推单位。
    try:
        if gaps_ms:
            g = np.sort(np.asarray(gaps_ms, dtype=float))
            g_short = float(np.median(g[: max(1, len(g) // 3)]))
            if g_short > 0 and 2.2 * g_short < unit <= 4.2 * g_short:
                unit = unit / 3.0
    except Exception:
        pass

    # 受限惯性跟踪（借鉴 fldigi）：单位时长只能慢慢跟随，且不能离谱
    #  · 只有"像点"的时长参与更新
    #  · 步长很小（0.06），避免被单个噪声带偏
    #  · 超出 0.5–1.8 倍的样本直接丢弃
    local_units: list[float] = []
    u = float(unit)
    for d in marks:
        if 0.5 * u <= d <= 1.8 * u:
            u += 0.06 * (d - u)
            u = max(20.0, min(200.0, u))
        local_units.append(u)
    unit = float(np.median(local_units)) if local_units else unit

    char_thr, word_thr = gap_thresholds(gaps_ms, unit)

    # ---- 有效速度（Farnsworth） ----
    # 字母间隔 = 3 个"有效单位"。标准码时字距没被拉长，
    # 算出来的有效速度 = 字符速度；Farnsworth 慢速时算出来就是 5 WPM 这类数值。
    # 有效速度用 PARIS 标准反算：把整段换算成"标准单位数"，
    # 再除以真实时长。这样与"数每个字母间隔"无关，标准码与 Farnsworth 都适用。
    #   WPM_eff = 1.2 × 总单位数 / 时长(秒)
    wpm_eff = None
    try:
        total_units = 0.0
        for st, ln in runs:
            d_units = (ln / sr * 1000.0) / unit
            if st:
                total_units += 1.0 if d_units < 1.8 else 3.0
            else:
                if d_units < char_thr:
                    total_units += 1.0
                elif d_units < word_thr:
                    total_units += 3.0
                else:
                    total_units += 7.0
        total_s = sum(ln for _, ln in runs) / sr
        if total_s > 1.0 and total_units > 10:
            wpm_eff = round(1.2 * total_units / total_s, 1)
    except Exception:
        wpm_eff = None

    idx_mark = 0
    symbols: list[str] = []
    chars: list[str] = []
    codes: list[str] = []
    marks_csv: list[dict] = []

    table = MORSE
    if alphabet == "ru":
        try:
            from .morse_ru import RU_MORSE
            table = RU_MORSE     # 真正的俄文字母表：Q→Щ、X→Ь、V→Ж、H→Х …
        except Exception:
            table = MORSE

    def flush_letter():
        nonlocal symbols
        token = "".join(symbols)
        symbols = []
        if not token:
            return
        chars.append(table.get(token, "?"))
        codes.append(token)

    def flush_word():
        # trim a trailing space so we do not accumulate blanks
        if chars and chars[-1] != " ":
            chars.append(" ")
            codes.append(" ")

    for state, ln in runs:
        dur_ms = ln / sr * 1000.0
        if state:
            kind = "-" if dur_ms > unit * 1.8 else "."
            symbols.append(kind)
            marks_csv.append({"mark_ms": round(dur_ms, 1), "kind": kind})
        else:
            gaps = dur_ms / unit
            if gaps >= word_thr:
                flush_letter()
                flush_word()
            elif gaps >= char_thr:
                flush_letter()

    flush_letter()

    text = "".join(chars).strip()
    text = " ".join(text.split())
    # keep codes aligned with the final text (drop leading/trailing blanks)
    trimmed: list[str] = []
    trimmed_chars: list[str] = []
    for ch, code in zip(chars, codes):
        trimmed_chars.append(ch)
        trimmed.append(code)
    while trimmed and trimmed[0] == " ":
        trimmed.pop(0)
    while trimmed and trimmed[-1] == " ":
        trimmed.pop()


    return {
        "text": text,
        "codes": trimmed,
        "unit_ms": round(unit, 1),
        "wpm": round(1200.0 / unit, 1) if unit else 0.0,
        "wpm_eff": wpm_eff,       # Farnsworth 有效速度；标准码时与 wpm 相同
        "marks": marks_csv,
    }



def estimate_snr(x: np.ndarray, f0: float, width: float = 180.0) -> float | None:
    """Estimate SNR (dB) of a CW signal from mark/gap energy.

    Uses the keyed envelope: marks contain signal+noise, gaps contain noise only.
    Reference bandwidth is the band-pass width (`width`), typically 180-500 Hz.
    """
    y = bandpass(x.astype(np.float32), TARGET_SR, f0, width)
    env = envelope(y, TARGET_SR)
    mask = binarize(env)
    n_on, n_off = int(mask.sum()), int((~mask).sum())
    if n_on < TARGET_SR // 50 or n_off < TARGET_SR // 50:      # 需要足够的按键/间隙样本
        return None
    ps = float(np.mean(env[mask] ** 2))
    pn = float(np.mean(env[~mask] ** 2))
    if pn <= 1e-12:
        return None
    p_sig = max(1e-12, ps - pn)
    return round(10.0 * float(np.log10(p_sig / pn)), 1)


def decode_samples(x: np.ndarray, tone: float | None = None,
                   width: float = 180.0, min_duration_s: float = 1.0,
                   alphabet: str = "latin") -> dict:
    """DSP decode of an 8 kHz mono float array (already normalised or not)."""
    x = np.asarray(x, dtype=np.float32)
    x = x - x.mean()
    if x.size == 0:
        return {"text": "", "codes": [], "unit_ms": 0.0, "wpm": 0.0, "wpm_eff": None, "marks": [],
                "tone_hz": 0.0, "duration_s": 0.0, "snr_db": None}
    peak = float(np.max(np.abs(x)) or 1.0)
    x = x / peak

    # 去掉首尾多余静音：否则统计量（分位数门限、单位时长）会被大段静音带偏
    if len(x) > 8000:
        thr = 0.02
        idx = np.flatnonzero(np.abs(x) > thr)
        if idx.size > 100:
            a = max(0, int(idx[0]) - 2400)          # 留 0.3 s 余量
            b = min(len(x), int(idx[-1]) + 2400)
            if b - a > 4000:
                x = x[a:b]

    if len(x) / TARGET_SR < min_duration_s:
        return {"text": "", "codes": [], "unit_ms": 0.0, "wpm": 0.0, "wpm_eff": None, "marks": [],
                "tone_hz": 0.0, "duration_s": round(len(x) / TARGET_SR, 2), "snr_db": None}

    f0 = tone if tone else estimate_tone(x)
    y = bandpass(x, TARGET_SR, f0, width)
    env = envelope(y, TARGET_SR)
    mask = binarize(env)
    runs = clean_runs(_runs(mask), TARGET_SR)
    res = decode_symbols(runs, TARGET_SR, alphabet=alphabet)
    res.update({"tone_hz": round(f0, 1), "duration_s": round(len(x) / TARGET_SR, 2),
                "snr_db": estimate_snr(x, f0)})
    return res


def decode_wav(path: str, tone: float | None = None, width: float = 180.0) -> dict:
    """DSP decode of a WAV file."""
    x, sr = read_wav(path)
    return decode_samples(resample(x, sr), tone=tone, width=width)


def _text_score(text: str) -> float:
    """Rough plausibility score used only to choose between candidate tones."""
    t = (text or "").upper()
    body = t.replace(" ", "")
    if not body:
        return -1.0
    unknown = body.count("?") / len(body)
    words = [w for w in t.split() if w]
    mean_len = sum(len(w) for w in words) / max(1, len(words))
    single = sum(1 for w in words if len(w) == 1) / max(1, len(words))
    return (1.0 - unknown) * 2.0 + min(1.0, mean_len / 4.0) - single


def pick_tone(x: np.ndarray, lo: float = 250.0, hi: float = 1600.0) -> float:
    """Choose the tone by trying a few candidates and keeping the best decode.

    Spectral peaks alone can be fooled by a carrier or by keying sidebands; trying
    to actually decode a short slice is a much stronger signal.
    """
    n = len(x)
    if n < TARGET_SR:
        return estimate_tone(x)

    probe = x[-min(n, TARGET_SR * 6):]          # 用最后 6 秒试解
    base = estimate_tone(probe, lo=lo, hi=hi)
    cands = [base]
    for d in (-60.0, -30.0, 30.0, 60.0):
        cands.append(base + d)

    scores: dict[float, float] = {}
    for f in cands:
        if not (lo <= f <= hi):
            continue
        try:
            r = decode_samples(probe, tone=f)
            sc = _text_score(r.get("text", ""))
            if r.get("snr_db") is not None:
                sc += min(3.0, max(-3.0, r["snr_db"] / 10.0))
            scores[f] = sc
        except Exception:
            continue

    if not scores:
        return float(base)
    best = max(scores, key=scores.get)
    base_s = scores.get(base, -99.0)
    # 只有"明显更好"才离开频谱峰值，避免被错误候选带偏
    if best != base and scores[best] - base_s < 0.8:
        return float(base)
    return float(best)


def cw_regularity(x: np.ndarray, f0: float, unit_ms: float | None = None,
                  width: float = 180.0) -> float:
    """How CW-like the keying is (0..1): marks should sit near 1x/3x one unit."""
    if unit_ms is None:
        r = decode_samples(x, tone=f0)
        unit_ms = r.get("unit_ms") or 0.0
    if not unit_ms or unit_ms < 10:
        return 0.0
    y = bandpass(np.asarray(x, dtype=np.float32), TARGET_SR, f0, width)
    env = envelope(y, TARGET_SR)
    mask = binarize(env)
    runs = clean_runs(_runs(mask), TARGET_SR)
    marks = [ln / TARGET_SR * 1000.0 for st, ln in runs if st]
    gaps = [ln / TARGET_SR * 1000.0 for st, ln in runs if not st]
    if len(marks) < 6:
        return 0.0

    def fit(vals):
        r = np.abs(np.asarray(vals) / unit_ms - np.round(np.asarray(vals) / unit_ms))
        return float(np.mean(r < 0.28))

    return 0.7 * fit(marks) + 0.3 * fit(gaps)


def tonality(x: np.ndarray, f0: float | None = None,
             lo: float = 300.0, hi: float = 1500.0) -> float:
    """Peak-to-median level (dB) inside the CW band: high for a pure tone.

    CW: 40-80 dB.  Speech/music/noise: 10-35 dB.  This separates "there is a
    carrier here" from "there is some sound here", which energy alone cannot.
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    if n < 2048:
        return 0.0
    sp = np.abs(np.fft.rfft(x * np.hanning(n))) + 1e-12
    fr = np.fft.rfftfreq(n, 1.0 / TARGET_SR)
    if f0 is not None:
        # 已知音调时：只看该音调附近 ±20 Hz 与 ±300 Hz 邻域的对比
        near = np.abs(fr - f0) <= 20.0
        neigh = (np.abs(fr - f0) <= 300.0) & (np.abs(fr - f0) > 40.0)
        if near.any() and neigh.any():
            return float(20.0 * np.log10(sp[near].max() / (np.median(sp[neigh]) + 1e-12)))
    band = (fr >= lo) & (fr <= hi)
    if not band.any():
        return 0.0
    vals = 20.0 * np.log10(sp[band])
    return float(vals.max() - np.median(vals))


def pick_tone_robust(x: np.ndarray, win_s: float = 10.0) -> float:
    """Pick the tone from the most CW-like window (skips speech/music intros)."""
    n = len(x)
    sr = TARGET_SR
    size = int(win_s * sr)
    if n <= size:
        return pick_tone(x)
    best_f, best_t = None, -1e9
    for start in range(0, n - size + 1, size):
        seg = x[start:start + size]
        t = tonality(seg)
        if t > best_t:
            best_t, best_f = t, start
    if best_f is None:
        return pick_tone(x)
    return pick_tone(x[best_f:best_f + size])


def find_onset(y, tone: float, sr: int = 8000, search_s: float = 20.0,
               min_gap_s: float = 0.80) -> float | None:
    """Return the time (seconds, relative to `y`) where the last silence ends.

    Used when following a station that changes frequency: the new transmission
    always starts after a short gap, so resuming decoding from that point keeps
    the first character intact instead of cutting into it.
    """
    y = np.asarray(y, dtype=np.float32)
    if y.size < sr:
        return None
    band = bandpass(y, sr, tone, 180.0)
    env = envelope(band, sr)
    peak = float(np.max(env))
    if peak <= 1e-9:
        return None
    thr = max(float(np.percentile(env, 10)) * 3.0, peak * 0.15)
    on = env > thr

    n = env.size
    start = max(0, n - int(search_s * sr))
    last_gap_end = None
    i = start
    while i < n:
        if not on[i]:
            j = i
            while j < n and not on[j]:
                j += 1
            if (j - i) >= int(min_gap_s * sr):
                # 只有当这段静音"后面确实还有信号"时，才算是报文起点
                k = j
                k_end = min(n, j + int(0.6 * sr))
                has_after = any(on[k:k_end])
                if has_after:
                    last_gap_end = j
            i = j
        else:
            i += 1
    if last_gap_end is None:
        return None
    return last_gap_end / float(sr)


def codes_to_text(codes, alphabet: str = "latin") -> str:
    """Render a list of Morse codes with the chosen alphabet.

    File mode keeps `codes`, so switching Latin/Cyrillic is a pure re-render:
    Q --.- becomes Щ in Russian, and switching back returns Q.
    """
    table = MORSE
    if alphabet == "ru":
        try:
            from .morse_ru import RU_MORSE
            table = RU_MORSE
        except Exception:
            table = MORSE
    out = []
    for c in codes:
        if not c:
            continue
        if c == " ":
            out.append(" ")
        else:
            out.append(table.get(c, "?"))
    return " ".join("".join(out).split())
