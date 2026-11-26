# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA (GitHub: thunder719x)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto backend: run DSP and CTC, compare, pick the more plausible one.

The decision is deliberately conservative and explainable:

1. agreement  = 1 - edit_distance(a, b) / max(len(a), len(b))
2. structure  = heuristic "does this look like real traffic" score (0..1)
3. rule       = agreement >= 0.85 -> CTC (both agree, CTC is finer)
                agreement >= 0.60 -> higher structure score wins
                else              -> DSP if SNR >= 10 dB else CTC

Nothing here makes a wrong decoder right; it only avoids picking the clearly
worse of the two.
"""
from __future__ import annotations

import re

from . import pipeline


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").upper()).strip()


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def agreement(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a and not b:
        return 1.0
    return 1.0 - edit_distance(a, b) / max(len(a), len(b))


def structure_score(text: str) -> dict:
    """Heuristic 0..1: does the output look like real radio traffic?"""
    t = _norm(text)
    if not t:
        return {"total": 0.0, "unknown": 1.0, "space": 0.0, "word": 0.0, "single": 0.0}

    body = t.replace(" ", "")
    unknown = 1.0 - (body.count("?") / max(1, len(body)))
    space_ratio = t.count(" ") / max(1, len(t))
    space = 1.0 - min(1.0, abs(space_ratio - 0.16) / 0.16)

    words = [w for w in t.split(" ") if w]
    mean_len = sum(len(w) for w in words) / max(1, len(words))
    word = 1.0 - min(1.0, abs(mean_len - 3.5) / 3.5)

    single = sum(1 for w in words if len(w) == 1) / max(1, len(words))
    single_score = 1.0 - min(1.0, single / 0.30)

    total = 0.40 * unknown + 0.20 * space + 0.20 * word + 0.20 * single_score
    return {"total": round(total, 3), "unknown": round(unknown, 3),
            "space": round(space, 3), "word": round(word, 3), "single": round(single_score, 3)}


def decode_auto(path: str, tone: float | None = None, out_dir: str | None = None,
                gate: bool = True) -> dict:
    dsp_res = pipeline.decode_file(path, backend="dsp", tone=tone, out_dir=out_dir)

    # 快速通道：长文件 + 干净 + 强信号 -> 不必再跑 CTC
    d_text = dsp_res.get("text", "")
    d_body = d_text.replace(" ", "")
    if (dsp_res.get("duration_s", 0) or 0) >= 90 and d_body and "?" not in d_body \
            and (dsp_res.get("snr_db") or 0) >= 15.0:
        out = dict(dsp_res)
        out.update({"backend": "auto", "winner": "dsp", "reason": "fast",
                    "agreement": None, "dsp_text": d_text, "ctc_text": "",
                    "dsp_score": structure_score(d_text)["total"], "ctc_score": None})
        return out
    ctc_res = pipeline.decode_file(path, backend="ctc", tone=tone, out_dir=out_dir,
                                   gate=gate)

    a_dsp, a_ctc = dsp_res.get("text", ""), ctc_res.get("text", "")
    agree = round(agreement(a_dsp, a_ctc), 3)
    s_dsp = structure_score(a_dsp)
    s_ctc = structure_score(a_ctc)
    snr = dsp_res.get("snr_db")

    if agree >= 0.85:
        winner, reason = "ctc", "agree"
    elif agree >= 0.60:
        winner, reason = ("ctc" if s_ctc["total"] >= s_dsp["total"] else "dsp"), "structure"
    else:
        if snr is not None and snr < 10.0:
            winner, reason = "ctc", "weak"
        else:
            winner, reason = "dsp", "disagree"

    best = ctc_res if winner == "ctc" else dsp_res
    out = dict(best)
    out.update({
        "backend": "auto",
        "elapsed_s": round(float(dsp_res.get("elapsed_s", 0)) + float(ctc_res.get("elapsed_s", 0)), 3),
        "winner": winner,
        "reason": reason,
        "agreement": agree,
        "dsp_text": a_dsp,
        "ctc_text": a_ctc,
        "dsp_score": s_dsp["total"],
        "ctc_score": s_ctc["total"],
        "dsp_detail_score": s_dsp,
        "ctc_detail_score": s_ctc,
    })
    return out
