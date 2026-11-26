# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA (GitHub: thunder719x)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live decoding by rolling re-decode (robust, reuses the file decoder).

Instead of a fragile per-sample state machine, keep a rolling window of audio
(e.g. 20 s) and re-run the proven offline decoder every ~0.8 s.  Only the part
of the text that changed is applied to the UI:

    new_text = decode(window)
    L        = longest common prefix(old_text, new_text)
    if L < len(old_text): retract (len(old_text) - L) characters
    append new_text[L:]

This is the same "local agreement" idea used by streaming ASR systems, and it
inherits the accuracy of the batch decoder (adaptive unit estimation, global
thresholds, k-means dot/dash clustering).
"""
from __future__ import annotations

import threading
import time

import numpy as np

from . import correct, dsp


class WindowDecoder:
    def __init__(self, f0: float = 700.0, window_s: float = 1e9,
                 hop_s: float = 0.8, on_update=None, min_snr_db: float = -6.0,
                 alphabet: str = "latin",
                 on_tone=None, src_rate: int = 8000, backend: str = "dsp",
                 fix_text: bool = True):
        self.f0 = float(f0)
        self.src_rate = int(src_rate)
        self.window_s = float(window_s)
        self.hop_s = float(hop_s)
        self.on_update = on_update
        self.min_snr_db = float(min_snr_db)
        self.on_tone = on_tone
        self.backend = backend
        self.alphabet = alphabet
        self._windowed = float(window_s) < 1e6
        self.fix_text = fix_text
        if backend == "ctc" and min_snr_db == -6.0:
            self.min_snr_db = -13.5
        self._last_tone_try = 0.0
        self._last_tone_check = 0.0
        self.quality = 0.0
        self.regularity = 1.0
        self._stable_len = 0    # 已弃用（保留变量兼容）
        self.committed = ""
        self._prev_hyp = ""
        self._raw_carry = np.zeros(0, dtype=np.float32)
        self._trimmed = False
        self.corrections = 0
        self.tonality = 0.0

        self._buf: list[np.ndarray] = []
        self._samples = 0
        self._since = 0
        self._evict: list[np.ndarray] = []
        self.committed = ""                 # 已经滑出窗口、定稿的文本
        self.text = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.snr_db: float | None = None
        self.wpm: float | None = None

    # ------------------------------------------------------------ input
    def set_tone(self, f0: float):
        self.f0 = float(f0)

    def reseed(self, audio):
        """清空文本，但用给定音频作为新的起点（换台时补回开头几个字）。"""
        self.reset()
        try:
            import numpy as _np
            a = _np.asarray(audio, dtype=_np.float32)
            if a.size:
                # feed() 期望的是"设备采样率"的音频，而调用方给的是 8 kHz，
                # 不转换就会被当成 48 kHz 降采样 → 音调变 6 倍 → 解出乱码
                sr = int(self.src_rate or 8000)
                if sr != 8000:
                    from math import gcd
                    from scipy.signal import resample_poly
                    g = gcd(8000, sr)
                    a = resample_poly(a, sr // g, 8000 // g).astype(_np.float32)
                self.feed(a)
        except Exception:
            pass

    def reset(self):
        self._buf = []
        self._samples = 0
        self._since = 0
        self._evict = []
        self.committed = ""
        self.text = ""
        self.corrections = 0
        self.snr_db = None
        self.wpm = None

    def feed(self, block: np.ndarray):
        if block is None or len(block) == 0:
            return
        with self._lock:
            self._buf.append(np.asarray(block, dtype=np.float32))
            self._samples += len(block)
            self._since += len(block)

        limit = int(self.window_s * 8000)
        while self._samples > limit and len(self._buf) > 1:
            blk = self._buf.pop(0)
            self._samples -= len(blk)
            self._evict.append(blk)

    def _audio(self) -> np.ndarray:
        with self._lock:
            raw = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        if raw.size == 0 or self.src_rate == 8000:
            return raw
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(self.src_rate, 8000)
        return resample_poly(raw, 8000 // g, self.src_rate // g).astype(np.float32)

    def _decode_ctc(self, y: np.ndarray, tone: float) -> dict:
        from . import ctc as ctc_mod
        from .pipeline import get_ctc_model
        m = get_ctc_model()
        feats = ctc_mod.compute_features(y.astype(np.float64), tone, m.agc_tau_s)
        lp = m.log_probs(feats)
        toks = ctc_mod.greedy_tokens(lp, m.blank_id)
        txt, codes, detail = ctc_mod.tokens_to_text(m, toks)
        return {"text": txt, "codes": codes, "detail": detail,
                "snr_db": self.snr_db, "wpm": self.wpm, "unit_ms": None}

    def _reprocess(self):
        """Decode the whole buffer and merge with a simple LCP rule.

        Costs grow with buffer length, so live mode keeps a bounded window
        (see `window_s`); the merge rule never rewrites text that already matched.
        """
        t_start = time.time()
        y = self._audio()
        if len(y) < 0.8 * 8000:
            return

        self.tonality = dsp.tonality(y[-min(len(y), 20 * 8000):], f0=self.f0)
        if self.tonality < 25.0:
            return

        try:
            if self.backend == "ctc":
                try:
                    res = self._decode_ctc(y, self.f0)
                except ImportError:
                    self.backend = "dsp"        # 缺依赖 → 退回规则算法
                    res = dsp.decode_samples(y, tone=self.f0, alphabet=self.alphabet)
            else:
                res = dsp.decode_samples(y, tone=self.f0, alphabet=self.alphabet)
        except Exception:
            return

        self.snr_db = res.get("snr_db")
        self.wpm = res.get("wpm")
        new_text = (res.get("text") or "").strip()
        if not new_text:
            return

        if self.fix_text:
            try:
                from . import correct
                new_text, _ = correct.correct_text(new_text, enabled=True)
                new_text, _ = correct.repair_spacing(new_text)
            except Exception:
                pass

        self._apply(new_text)
        self.hop_s = max(self.hop_s, (time.time() - t_start) * 2.5)

    def _apply(self, new_text: str):
        """LCP merge: replace only the part that changed (keeps copied text)."""
        if not new_text:
            return
        old = self.text
        if new_text == old:
            return
        n = 0
        lim = min(len(old), len(new_text))
        while n < lim and old[n] == new_text[n]:
            n += 1
        back = len(old) - n
        add = new_text[n:]
        self.text = old[:n] + add
        if self.on_update:
            self.on_update(back, add)

    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()
        th = threading.Thread(target=self._loop, daemon=True)
        self._thread = th
        th.start()

    def stop(self):
        self._stop.set()
        th, self._thread = self._thread, None
        if th is not None:
            th.join(timeout=1.0)

    def _loop(self):
        while not self._stop.is_set():
            with self._lock:
                dur = self._samples / 8000.0
            hop = max(self.hop_s, dur * 0.012)
            if self._stop.wait(hop):
                break
            try:
                self._reprocess()
            except Exception:
                import traceback
                traceback.print_exc()

    def flush(self):
        self._reprocess()
