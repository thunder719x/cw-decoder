# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Channel discovery for multi-signal CW.

Finds persistent tones in the current audio (not just "the loudest peak"), tracks
them across time with hysteresis, and retires them when they disappear.  This is
the same principle used by CW Skimmer / SDRangel: per-frequency detection plus
track association, rather than one global peak pick.

Two-stage confirmation keeps the list stable and cheap:
  · a detection must be seen twice before it becomes a *tentative* channel;
  · only then is the expensive "does it decode?" check run, once.
"""
from __future__ import annotations

import numpy as np

SR = 8000


class Channel:
    __slots__ = ("freq", "level", "hits", "missing", "confirmed", "text", "last_call")

    def __init__(self, freq: float, level: float):
        self.freq = float(freq)
        self.level = float(level)
        self.hits = 1
        self.missing = 0
        self.confirmed = True
        self.text = ""
        self.last_call = ""

    def to_dict(self):
        return {"freq": round(self.freq, 1), "level": round(self.level, 1),
                "confirmed": self.confirmed, "hits": self.hits, "text": self.text}


class ChannelTracker:
    def __init__(self, lo: float = 250.0, hi: float = 2500.0,
                 prom_db: float = 12.0, max_channels: int = 8,
                 match_hz: float = 18.0, min_sep_hz: float = 40.0,
                 confirm_hits: int = 2, retire_misses: int = 5,
                 verify_seconds: float = 6.0):
        self.lo, self.hi = lo, hi
        self.prom_db = prom_db
        self.max_channels = max_channels
        self.match_hz = match_hz
        self.min_sep_hz = min_sep_hz
        self.confirm_hits = confirm_hits
        self.retire_misses = retire_misses
        self.verify_seconds = verify_seconds

        self.channels: list[Channel] = []
        self.tentative: list[dict] = []
        self._buf = np.zeros(0, dtype=np.float32)
        self._verified: dict[int, bool] = {}      # 频率→是否通过解码验证（缓存）
        self._recent: list[dict] = []             # 最近退场的频道，避免"重新建号"

    # ------------------------------------------------------------ detection
    def detect(self, y: np.ndarray) -> list[tuple[float, float]]:
        n = len(y)
        if n < 2048:
            return []
        sp = np.abs(np.fft.rfft(y * np.hanning(n))) + 1e-12
        fr = np.fft.rfftfreq(n, 1.0 / SR)
        band = (fr >= self.lo) & (fr <= self.hi)
        if not band.any():
            return []
        freqs = fr[band]
        vals = 20.0 * np.log10(sp[band])

        if len(vals) >= 5:                       # 3 点平滑
            vals = np.convolve(vals, np.array([0.25, 0.5, 0.25]), mode="same")

        base = float(np.median(vals))
        peaks: list[tuple[float, float]] = []
        for i in range(2, len(vals) - 2):
            if vals[i] < base + self.prom_db:
                continue
            if not (vals[i] >= vals[i - 1] and vals[i] >= vals[i + 1]):
                continue
            peaks.append((float(freqs[i]), float(vals[i] - base)))

        peaks.sort(key=lambda p: -p[1])
        if peaks:
            top = peaks[0][1]
            peaks = [p for p in peaks if p[1] >= top - 30.0]

        # 边带抑制：离更强的峰 <150 Hz 且弱 8 dB 以上
        chosen: list[tuple[float, float]] = []
        for f, p in peaks:
            if any(abs(f - c[0]) < 150.0 and p < c[1] - 8.0 for c in chosen):
                continue
            if all(abs(f - c[0]) >= self.min_sep_hz for c in chosen):
                chosen.append((f, p))
            if len(chosen) >= self.max_channels:
                break

        # 只对前几个做强判定，控制耗时
        return [(f, p) for f, p in chosen if self.verify_cw(y, f)]

    # ------------------------------------------------------------ checks
    @staticmethod
    def verify_cw(y: np.ndarray, f: float) -> bool:
        from . import dsp
        try:
            band = dsp.bandpass(y.astype(np.float32), SR, f, 120.0)
            env = dsp.envelope(band, SR)
        except Exception:
            return False
        if env.size < 2000:
            return False
        lo = float(np.percentile(env, 20))
        hi = float(np.percentile(env, 90))
        if hi <= 1e-9:
            return False
        modulation = 20.0 * float(np.log10((hi + 1e-12) / (lo + 1e-12)))
        duty = float(np.mean(env > (lo + 0.45 * (hi - lo))))
        return modulation >= 8.0 and 0.05 <= duty <= 0.85

    @staticmethod
    def verify_decode(y: np.ndarray, f: float) -> bool:
        from . import dsp
        try:
            r = dsp.decode_samples(y.astype(np.float32), tone=f)
        except Exception:
            return False
        text = (r.get("text") or "").replace(" ", "")
        if len(text) < 2:
            return False
        try:
            reg = dsp.cw_regularity(y.astype(np.float32), f, r.get("unit_ms"))
        except Exception:
            reg = 1.0
        return reg >= 0.6

    # ------------------------------------------------------------ tracking
    def update(self, y: np.ndarray) -> list[Channel]:
        y = np.asarray(y, dtype=np.float32)
        self._buf = np.concatenate([self._buf, y])
        keep = int(self.verify_seconds * SR)
        if len(self._buf) > keep:
            self._buf = self._buf[-keep:]

        dets = self.detect(y)
        used_ch, used_t = set(), set()

        # 1) 先匹配已有频道 / 试探频道
        for f, lvl in dets:
            matched = False
            for i, ch in enumerate(self.channels):
                if i in used_ch:
                    continue
                if abs(ch.freq - f) <= self.match_hz:
                    ch.freq = 0.75 * ch.freq + 0.25 * f
                    ch.level = 0.75 * ch.level + 0.25 * lvl
                    ch.hits += 1
                    ch.missing = 0
                    used_ch.add(i)
                    matched = True
                    break
            if matched:
                continue
            for j, t in enumerate(self.tentative):
                if j in used_t:
                    continue
                if abs(t["freq"] - f) <= self.match_hz:
                    t["freq"] = 0.75 * t["freq"] + 0.25 * f
                    t["level"] = lvl
                    t["hits"] += 1
                    t["missing"] = 0
                    used_t.add(j)
                    matched = True
                    break
            if not matched:
                self.tentative.append({"freq": f, "level": lvl, "hits": 1, "missing": 0})

        # 2) 试探频道满 2 次 → 解码验证 → 转正
        for j, t in enumerate(self.tentative):
            if j in used_t:
                continue
            t["missing"] = t.get("missing", 0) + 1
        for t in list(self.tentative):
            if t["hits"] >= self.confirm_hits and t.get("missing", 0) == 0:
                key = int(round(t["freq"]))
                ok = self._verified.get(key)
                if ok is None:
                    ok = len(self._buf) >= 2 * SR and self.verify_decode(self._buf, t["freq"])
                    self._verified[key] = bool(ok)
                if ok:
                    self.channels.append(Channel(t["freq"], t["level"]))
                self.tentative.remove(t)
        self.tentative = [t for t in self.tentative if t.get("missing", 0) <= 2]

        # 3) 频道超时退场（记住频率，方便复现）
        for i, ch in enumerate(self.channels):
            if i not in used_ch:
                ch.missing += 1
        gone = [c for c in self.channels if c.missing >= self.retire_misses]
        for c in gone:
            self._recent.append({"freq": c.freq, "level": c.level, "text": c.text})
        self._recent = self._recent[-32:]
        self.channels = [c for c in self.channels if c.missing < self.retire_misses]

        # 3b) 退场的台又出现了 → 复用原来的频率与文本（防止抖动导致"新频道"）
        for d in list(self._recent):
            if any(abs(d["freq"] - c.freq) <= self.match_hz for c in self.channels):
                self._recent.remove(d)
                continue
            if any(abs(d["freq"] - f) <= self.match_hz for f, _ in dets):
                ch = Channel(d["freq"], d["level"])
                ch.text = d.get("text", "")
                self.channels.append(ch)
                self._recent.remove(d)

        # 4) 合并靠得太近的频道（保留强的）
        self.channels.sort(key=lambda c: -c.level)
        merged: list[Channel] = []
        for c in self.channels:
            if all(abs(c.freq - m.freq) >= self.min_sep_hz for m in merged):
                merged.append(c)
        self.channels = merged[: self.max_channels]
        return self.channels

    def confirmed(self) -> list[Channel]:
        return [c for c in self.channels if c.confirmed]

    def clear(self):
        self.channels = []
        self.tentative = []
        self._buf = np.zeros(0, dtype=np.float32)
        self._verified = {}
