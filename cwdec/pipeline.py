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
            # 慢速门控：CTC 只训练过标准节奏，<15 WPM 时会成片乱码（实测 5/13 WPM
            # 的 ARRL 素材 CER 0.72 / 0.88）。这里先用 DSP 快速测一次速度，
            # 太慢就直接用 DSP 的结果，并在 note 里标注原因。
            slow = False
            try:
                from .dsp import read_wav, resample
                _x, _sr = read_wav(path)
                _probe = decode_gated(resample(_x, _sr), tone=tone, alphabet=alphabet)
                slow = 0 < (_probe.get("wpm") or 0) < 15.0
            except Exception:
                _probe, slow = None, False

            if slow and _probe is not None:
                res = _probe
                res["backend"] = "dsp"
                res["note"] = "slow-speed->dsp"
            else:
                res = ctc.decode_wav(path, get_ctc_model(), tone=tone, gate=gate)
                # CTC 不产生码元间隔：借用 DSP 探针的电平/速度/时序比做"质量指示"
                if _probe:
                    if not res.get("wpm_eff"):
                        res["wpm_eff"] = _probe.get("wpm_eff")
                    res.setdefault("level_dbfs", _probe.get("level_dbfs"))
                    res.setdefault("timing_ratio", _probe.get("timing_ratio"))
                    res.setdefault("regularity", _probe.get("regularity"))
                    # SNR 统一用 DSP 探针的值：
                    # CTC 路径会裁掉长载波、补静音，统计口径不同，数值会偏高 2–5 dB，
                    # 导致切换后端时同一段录音的 SNR 跳变（指标应只有一个来源）
                    if _probe.get("snr_db") is not None:
                        res["snr_db"] = _probe.get("snr_db")
                        res["snr_from"] = "dsp-probe"
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
        #
        # 2026-11-27：词替换（correct_text）默认停用。
        # 实测（ARRL 9 文件 / 10,363 字符）：关闭后 0 错误，开启后 10 个错误，
        # 且麦克风 11 条与 −5…−12 dB 合成扫描均无任何收益。
        # 只保留"空格修复"（不改变字母，只补空格）。
        fixed, sp = correct.repair_spacing(res["text"])
        changes = list(sp)
        if changes:
            res["text_raw"] = res["text"]
            res["text"] = fixed
            res["corrections"] = changes

    res.update({
        "source": os.path.abspath(path),
        "backend": res.get("backend") or backend,     # 慢速门控时保留实际用的后端
        "alphabet": alphabet,                         # 供界面判断（西里尔时不做非 ASCII 惩罚）
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
