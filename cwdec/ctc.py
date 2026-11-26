# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""CTC backend (ONNX) for CW decoding.

The acoustic model is trained on 11-dim features at 200 frames/s:
    col 0  : log envelope after AGC
    col 1  : derivative of col 0
    col 2..10 : 9 log-power spectral bins around the tone

The model outputs CTC log-probabilities over *Morse tokens* (".-", "-...", ...),
not over letters; letters are produced by a lookup table afterwards.

Feature extraction follows the reference implementation (morsedesk) so that the
model sees exactly what it was trained on.
"""
from __future__ import annotations

import json
import os

import numpy as np

try:
    from scipy.signal import butter, lfilter, lfilter_zi, sosfiltfilt
except Exception as exc:  # pragma: no cover
    raise ImportError("scipy is required for the CTC backend") from exc

SAMPLE_RATE = 8000
HOP = 40
N_FFT = 256
FRAME_RATE = SAMPLE_RATE // HOP
FEAT_DIM = 11
TONE_MIN_HZ = 300.0
TONE_MAX_HZ = 1500.0

MORSE_TO_CHAR = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", ".----.": "'", "-.-.--": "!",
    "-..-.": "/", "-.--.": "(", "-.--.-": ")", ".-...": "&", "---...": ":",
    "-.-.-.": ";", "-...-": "=", ".-.-.": "+", "-....-": "-", "..--.-": "_",
    ".-..-.": '"', "...-..-": "$", ".--.-.": "@",
}


# ---------------------------------------------------------------- features
def _stft_power(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(x)
    n_frames = n // HOP
    pad = N_FFT // 2
    xp = np.pad(np.asarray(x, dtype=np.float64), (pad, pad + N_FFT))
    idx = np.arange(n_frames)[:, None] * HOP + np.arange(N_FFT)[None, :]
    frames = xp[idx] * np.hanning(N_FFT)[None, :]
    spec = np.fft.rfft(frames, axis=1)
    return (np.abs(spec) ** 2).T, np.fft.rfftfreq(N_FFT, 1.0 / SAMPLE_RATE)


def estimate_tone(x: np.ndarray) -> float:
    power, freqs = _stft_power(x)
    if power.size == 0:
        return 700.0
    mean_p = power.mean(axis=1)
    band = (freqs >= TONE_MIN_HZ) & (freqs <= TONE_MAX_HZ)
    if not band.any():
        return 700.0
    k = int(np.argmax(np.where(band, mean_p, 0.0)))
    if 0 < k < len(mean_p) - 1:                      # 抛物插值，取亚 bin 精度
        a, b, c = (np.log(mean_p[k - 1] + 1e-20), np.log(mean_p[k] + 1e-20),
                   np.log(mean_p[k + 1] + 1e-20))
        denom = a - 2.0 * b + c
        if denom != 0:
            delta = float(np.clip(0.5 * (a - c) / denom, -1.0, 1.0))
            return float(freqs[k] + delta * (freqs[1] - freqs[0]))
    return float(freqs[k])


def _mix_envelope(x: np.ndarray, f0: float, lowpass_hz: float = 200.0) -> np.ndarray:
    n = len(x)
    n_frames = n // HOP
    if n_frames == 0:
        return np.zeros(0)
    sos = butter(4, lowpass_hz, btype="low", fs=SAMPLE_RATE, output="sos")
    t = np.arange(n, dtype=np.float64) / SAMPLE_RATE
    y = x.astype(np.float64) * np.exp(-2j * np.pi * f0 * t)
    yi = sosfiltfilt(sos, y.real)
    yq = sosfiltfilt(sos, y.imag)
    env = np.sqrt(yi * yi + yq * yq)
    env_p = np.pad(env, (HOP // 2, HOP))
    return env_p[: n_frames * HOP].reshape(n_frames, HOP).mean(axis=1)


def _ema(v: np.ndarray, alpha: float, init: float) -> np.ndarray:
    b = np.array([alpha])
    a = np.array([1.0, -(1.0 - alpha)])
    zi = lfilter_zi(b, a) * init
    out, _ = lfilter(b, a, v, zi=zi)
    return out


def _agc_log(env: np.ndarray, tau_s: float = 1.0, init_s: float = 1.0) -> np.ndarray:
    if len(env) == 0:
        return np.zeros(0)
    log_env = np.log(env + 1e-6)
    alpha = 1.0 / (tau_s * FRAME_RATE)
    head = log_env[: max(1, min(len(log_env), int(round(init_s * FRAME_RATE))))]
    mean = _ema(log_env, alpha, float(np.mean(head)))
    dev = log_env - mean
    var = _ema(dev * dev, alpha, float(np.mean(dev[: len(head)] ** 2)))
    out = dev / (np.sqrt(np.maximum(var, 0.0)) + 0.1)
    return np.clip(out, -6.0, 6.0)


def _spectral_bins(x: np.ndarray, f0: float, n_bins: int = 9) -> np.ndarray:
    n_frames = len(x) // HOP
    if n_frames == 0:
        return np.zeros((0, n_bins), dtype=np.float32)
    power, freqs = _stft_power(x)
    bin_hz = float(freqs[1] - freqs[0])
    k0 = int(round(f0 / bin_hz))
    half = n_bins // 2
    ks = np.clip(np.arange(k0 - half, k0 - half + n_bins), 0, len(freqs) - 1)
    logp = np.log10(power[ks, :] + 1e-12).T
    med = float(np.median(logp))
    scale = float(np.percentile(logp, 95)) - med + 1e-3
    return ((logp - med) / scale).astype(np.float32)


def compute_features(x: np.ndarray, f0: float, agc_tau_s: float = 1.0) -> np.ndarray:
    n_frames = len(x) // HOP
    if n_frames == 0:
        return np.zeros((0, FEAT_DIM), dtype=np.float32)
    env = _mix_envelope(x, f0)
    a = _agc_log(env, agc_tau_s)
    d = np.diff(a, prepend=a[0] if len(a) else 0.0)
    bins = _spectral_bins(x, f0)
    feats = np.concatenate([a[:, None], d[:, None], bins], axis=1).astype(np.float32)
    return feats[:n_frames]


# ---------------------------------------------------------------- model
class CtcModel:
    def __init__(self, model_path: str, meta_path: str | None = None):
        import onnxruntime as ort

        self.meta = json.load(open(meta_path or os.path.splitext(model_path)[0] + ".json",
                                   encoding="utf-8"))
        self.vocab: list[str] = self.meta["vocab"]
        self.blank_id = int(self.meta.get("blank_id", 0))
        self.space_id = int(self.meta.get("space_id", 1))
        self.input_name = self.meta.get("input_name", "features")
        self.output_name = self.meta.get("output_name", "log_probs")
        self.time_stride = int(self.meta.get("time_stride", 2))
        self.agc_tau_s = float((self.meta.get("config") or {}).get("agc_tau_s", 1.0))

        so = ort.SessionOptions()
        so.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
        self.sess = ort.InferenceSession(model_path, so, providers=["CPUExecutionProvider"])

    def log_probs(self, feats: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(feats, dtype=np.float32)[None]
        return self.sess.run([self.output_name], {self.input_name: x})[0][0]


def greedy_tokens(log_probs: np.ndarray, blank_id: int = 0) -> list[tuple[int, int, float]]:
    """CTC collapse; returns (token_id, frame, prob)."""
    if log_probs.shape[0] == 0:
        return []
    ids = log_probs.argmax(axis=-1)
    out: list[tuple[int, int, float]] = []
    prev = -1
    for t, i in enumerate(ids.tolist()):
        if i != prev and i != blank_id:
            out.append((int(i), t, float(np.exp(log_probs[t, i]))))
        prev = i
    return out


def tokens_to_text(model: CtcModel, toks: list[tuple[int, int, float]],
                   suspect_frames: set[int] | None = None, t_offset_s: float = 0.0):
    chars: list[str] = []
    codes: list[str] = []
    detail: list[dict] = []
    stride = getattr(model, "time_stride", 1)
    for tid, frame, prob in toks:
        t_s = max(0.0, frame * stride / FRAME_RATE - t_offset_s)
        if tid == model.space_id:
            chars.append(" ")
            codes.append(" ")
            d = {"token": "<space>", "char": " ", "conf": round(prob, 3), "t": round(t_s, 3)}
            if suspect_frames and frame in suspect_frames:
                d["suspect"] = True
            detail.append(d)
            continue

        tok = model.vocab[tid] if 0 <= tid < len(model.vocab) else ""
        ch = MORSE_TO_CHAR.get(tok, "?")
        chars.append(ch)
        codes.append(tok)
        d = {"token": tok, "char": ch, "conf": round(prob, 3), "t": round(t_s, 3)}
        if suspect_frames and frame in suspect_frames:
            d["suspect"] = True
        detail.append(d)
    text = " ".join("".join(chars).split())
    return text, codes, detail



def trim_long_carrier(x: np.ndarray, f0: float, max_carrier_s: float = 2.0) -> np.ndarray:
    """Trim leading/trailing un-keyed carrier.

    Beacon recordings often start with several seconds of steady tone; the AGC
    then treats that tone as the reference level and the keyed part is lost.
    """
    from . import dsp
    mask = dsp.binarize(dsp.envelope(dsp.bandpass(x.astype(np.float32), SAMPLE_RATE, f0, 180.0),
                                     SAMPLE_RATE))
    if mask.size == 0:
        return x
    runs = [(st, ln) for st, ln in dsp._runs(mask) if ln > 0]
    limit = int(max_carrier_s * SAMPLE_RATE)
    start, end = 0, len(x)

    on_idx = [i for i, (st, _) in enumerate(runs) if st]
    if on_idx:
        i0 = on_idx[0]
        if runs[i0][1] > limit:
            start = sum(ln for _, ln in runs[:i0 + 1])
        i1 = on_idx[-1]
        if runs[i1][1] > limit:
            end = len(x) - sum(ln for _, ln in runs[i1:])

    if end - start < SAMPLE_RATE:
        return x
    return x[start:end]


def activity_mask(x: np.ndarray, f0: float) -> np.ndarray:
    """Per-sample boolean mask: is there CW energy at this instant?

    Reuses the DSP front-end (band-pass + envelope + k-means threshold); this is
    the cheap stand-in for a DeepCW-style presence mask.
    """
    from . import dsp
    y = dsp.bandpass(x.astype(np.float32), SAMPLE_RATE, f0, 180.0)
    env = dsp.envelope(y, SAMPLE_RATE)
    return dsp.binarize(env)


def _snr(x: np.ndarray, f0: float):
    from . import dsp
    try:
        return dsp.estimate_snr(x.astype(np.float32), f0)
    except Exception:
        return None


def decode_wav(path: str, model: CtcModel, tone: float | None = None,
               gate: bool = True, gate_pad_s: float = 0.12) -> dict:
    from .dsp import read_wav, resample

    x, sr = read_wav(path)
    x = resample(x, sr)
    x = x - x.mean()
    peak = float(np.max(np.abs(x)) or 1.0)
    x = (x / peak).astype(np.float64)

    f0 = tone or estimate_tone(x)
    x = trim_long_carrier(x, f0)          # 去掉开头/结尾的长载波

    # 补静音上下文：模型在"直接从第一个点划开始"时容易输出全空
    pad = int(0.4 * SAMPLE_RATE)
    pad_s = pad / SAMPLE_RATE
    x = np.concatenate([np.zeros(pad, dtype=np.float64), x,
                        np.zeros(pad, dtype=np.float64)])
    feats = compute_features(x, f0, model.agc_tau_s)
    if feats.shape[0] < 8:
        return {"text": "", "tokens": [], "detail": [], "tone_hz": round(f0, 1),
                "duration_s": round(len(x) / SAMPLE_RATE, 2)}

    from . import dsp as _dsp
    mask = activity_mask(x, f0)

    # WPM 估计：用同一套"按键/间隙"分析
    runs = [(st, ln) for st, ln in _dsp._runs(mask) if ln > 0]
    marks_ms = [ln / SAMPLE_RATE * 1000.0 for st, ln in runs if st]
    unit_ms = _dsp.estimate_unit(marks_ms)
    wpm = round(1200.0 / unit_ms, 1) if unit_ms else None
    if wpm is not None and not (5.0 <= wpm <= 60.0):      # 超出业余常用范围，判定不可靠
        wpm = None

    log_p = model.log_probs(feats)
    toks = greedy_tokens(log_p, model.blank_id)

    suspects = 0
    suspect_frames: set[int] = set()
    if gate:
        stride = getattr(model, "time_stride", 1)
        pad = int(gate_pad_s * SAMPLE_RATE)
        pre = int(0.25 * SAMPLE_RATE)
        for tid, frame, prob in toks:
            if tid == model.space_id:
                continue
            center = int(frame * stride / FRAME_RATE * SAMPLE_RATE)
            if center < int(0.30 * SAMPLE_RATE):
                continue
            lo = max(0, center - pre)
            hi = min(len(mask), center + pad)
            active = hi > lo and bool(mask[lo:hi].any())
            # 只标记，不删除：无信号且置信度低 -> 可疑
            if (not active) and prob < 0.75:
                suspects += 1
                suspect_frames.add(frame)
        if suspect_frames:
            toks = [(t, f, p) for (t, f, p) in toks]

    text, codes, detail = tokens_to_text(model, toks, suspect_frames, pad_s)

    return {
        "text": text,
        "codes": codes,
        "tokens": [t[0] for t in toks],
        "detail": detail,
        "gate_suspects": suspects,
        "wpm": wpm,
        "snr_db": _snr(x, f0),
        "tone_hz": round(f0, 1),
        "duration_s": round(len(x) / SAMPLE_RATE, 2),
        "frames": int(log_p.shape[0]),
    }
