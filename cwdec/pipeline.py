# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pipeline: decode a file and persist results (json + marks.csv)."""
from __future__ import annotations

import csv
import json
import os
import time

import numpy as np

from . import correct, ctc, dsp

_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "ctc")
_ctc_model: ctc.CtcModel | None = None


def _split_sessions(x: np.ndarray, min_gap_s: float = 2.5):
    """Split audio at long silences (natural boundaries between overs)."""
    env = dsp.envelope(dsp.bandpass(np.asarray(x, dtype=np.float32),
                                    dsp.TARGET_SR, 700.0, 2500.0), dsp.TARGET_SR)
    if env.size == 0:
        return [(0, len(x))]
    thr = float(np.percentile(env, 20)) + 0.05 * float(env.max() - np.percentile(env, 20))
    silent = env < thr
    runs = dsp._runs(silent)
    spans, pos = [], 0
    min_gap = int(min_gap_s * dsp.TARGET_SR)
    for st, ln in runs:
        if st and ln >= min_gap:
            spans.append((pos, pos + 0))
            pos = pos + ln
        else:
            pos += ln
    # 简化：按静音段切
    spans, start = [], 0
    pos = 0
    for st, ln in runs:
        if st and ln >= min_gap:
            if pos - start > dsp.TARGET_SR:
                spans.append((start, pos))
            start = pos + ln
        pos += ln
    if len(x) - start > dsp.TARGET_SR:
        spans.append((start, len(x)))
    return spans or [(0, len(x))]


def decode_gated(x: np.ndarray, tone: float | None = None,
                 scan_s: float = 5.0, min_tonality_db: float = 22.0,
                 min_regularity: float = 0.45, pad_s: float = 0.3,
                 alphabet: str = "latin") -> dict:
    """Decode a long recording, skipping a non-CW intro (speech/music).

    Finds where the CW starts, then decodes everything from there in ONE pass.
    Single pass keeps the timing statistics global and avoids seam errors; a short
    interference burst in the middle is left to the caller's quality gates.
    """
    x = np.asarray(x, dtype=np.float32)
    dur = len(x) / dsp.TARGET_SR

    if tone is None:
        tone = dsp.pick_tone_robust(x)

    if dur <= 25.0:
        r = dsp.decode_samples(x, tone=tone, alphabet=alphabet)
        text = r.get("text", "")
        if text:
            reg = dsp.cw_regularity(x, tone, r.get("unit_ms"))
            ton = dsp.tonality(x, f0=tone)
            r["tonality"] = round(ton, 1)
            r["regularity"] = round(reg, 2)
            if reg < min_regularity or ton < min_tonality_db:
                r["text"] = ""
                r["gated"] = True
        return r

    step = int(scan_s * dsp.TARGET_SR)
    begin = 0
    for st in range(0, max(1, len(x) - step), step):
        seg = x[st:st + step]
        if len(seg) < dsp.TARGET_SR:
            break
        if dsp.tonality(seg, f0=tone) >= min_tonality_db:
            begin = max(0, st - int(pad_s * dsp.TARGET_SR))
            break

    seg = x[begin:]
    r = dsp.decode_samples(seg, tone=tone, alphabet=alphabet)
    text = (r.get("text") or "").strip()
    reg = dsp.cw_regularity(seg, tone, r.get("unit_ms")) if text else 0.0
    ton = dsp.tonality(seg, f0=tone)
    if text and (reg < min_regularity or ton < min_tonality_db):
        r["text"] = ""
        r["gated"] = True
    r["regularity"] = round(reg, 2)
    r["tonality"] = round(ton, 1)
    r["tone_hz"] = round(float(tone), 1)
    r["duration_s"] = round(dur, 2)
    r["sessions"] = 1
    return r


def get_ctc_model() -> ctc.CtcModel:
    """Lazily create (and cache) the CTC model instance."""
    global _ctc_model
    if _ctc_model is None:
        onnx_path = os.path.join(_MODEL_DIR, "acoustic.onnx")
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(
                f"CTC model not found: {onnx_path}\n"
                "Download it from the morsedesk repository into models/ctc/"
            )
        _ctc_model = ctc.CtcModel(onnx_path)
    return _ctc_model


def decode_file(path: str, backend: str = "dsp", tone: float | None = None,
                out_dir: str | None = None, gate: bool = True,
                fix_text: bool = True, alphabet: str = "latin") -> dict:
    t0 = time.time()

    if backend == "dsp":
        from .dsp import read_wav, resample
        x, sr = read_wav(path)
        y = resample(x, sr)
        res = decode_gated(y, tone=tone, alphabet=alphabet)
    elif backend == "ctc":
        try:
            res = ctc.decode_wav(path, get_ctc_model(), tone=tone, gate=gate)
        except ImportError:
            # 没装 onnxruntime（或模型加载失败）→ 退回 DSP，并在状态里标注
            from .dsp import read_wav, resample
            x, sr = read_wav(path)
            res = decode_gated(resample(x, sr), tone=tone, alphabet=alphabet)
            res["backend"] = "dsp"
            res["note"] = "ctc-unavailable"
    elif backend == "auto":
        from .auto import decode_auto
        try:
            res = decode_auto(path, tone=tone, out_dir=out_dir, gate=gate)
            return res                  # auto 已经写过文件，避免重复
        except ImportError:
            from .dsp import read_wav, resample
            x, sr = read_wav(path)
            res = decode_gated(resample(x, sr), tone=tone, alphabet=alphabet)
            res["backend"] = "dsp"
            res["note"] = "ctc-unavailable"
    else:
        raise ValueError(f"unknown backend: {backend}")

    if res.get("text") and fix_text and alphabet == "latin":
        # 俄文模式不做英文单词纠错（白名单是英文的，会把西里尔文本搞乱）
        fixed, changes = correct.correct_text(res["text"])
        fixed, sp = correct.repair_spacing(fixed)
        changes = list(changes) + list(sp)
        if changes:
            res["text_raw"] = res["text"]
            res["text"] = fixed
            res["corrections"] = changes

    res.update({
        "source": os.path.abspath(path),
        "backend": backend,
        "elapsed_s": round(time.time() - t0, 3),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    if out_dir:
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            out_dir = None          # 目录不可写（例如装在 Program Files）时跳过写盘
    if out_dir:
        stem = os.path.splitext(os.path.basename(path))[0]
        stem = f"{stem}.{backend}"

        try:
            with open(os.path.join(out_dir, f"{stem}.json"), "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        marks = res.get("marks")
        if marks:
            try:
                with open(os.path.join(out_dir, f"{stem}.marks.csv"), "w", newline="",
                          encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(["mark_ms", "kind"])
                    for m in marks:
                        w.writerow([m.get("mark_ms", ""), m.get("kind", "")])
            except Exception:
                pass

        detail = res.get("detail")
        if detail:
            try:
                fh = open(os.path.join(out_dir, f"{stem}.tokens.csv"), "w", newline="",
                          encoding="utf-8")
            except Exception:
                fh = None
            if fh is not None:
                with fh as f:
                    w = csv.writer(f)
                    w.writerow(["t_s", "token", "char", "conf", "suspect"])
                    for d in detail:
                        w.writerow([d.get("t", ""), d.get("token", ""), d.get("char", ""),
                                    d.get("conf", ""), d.get("suspect", "")])

    return res
