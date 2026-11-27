# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Streaming (online) DSP decoder for CW.

Consumes 8 kHz mono blocks and emits characters as soon as each one is
finished, so text appears while the audio is still arriving.

Pipeline per sample
-------------------
IIR band-pass (stateful) -> rectify + one-pole envelope -> hysteresis squelch
-> mark/gap timing -> dot/dash -> Morse table -> character

Everything is stateful, so blocks can be any length and the timing stays exact.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt

MORSE = {
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
    "-...-": "=", ".-.-.": "+", "-....-": "-", "..--.-": "_", ".--.-.": "@",
}

SR = 8000


class StreamDecoder:
    """Online CW decoder: feed blocks, get characters as they complete."""

    def __init__(self, f0: float = 700.0, bandwidth: float = 180.0,
                 on_char=None, on_status=None, thr_hi: float = 0.35,
                 thr_lo: float = 0.25, min_event_ms: float = 8.0,
                 env_tau_ms: float = 3.0, squelch_db: float = -6.0):
        self.f0 = float(f0)
        self.bandwidth = float(bandwidth)
        self.on_char = on_char
        self.on_status = on_status

        self._build_filter()

        # envelope / squelch state
        self.thr_hi_ratio = float(thr_hi)
        self.thr_lo_ratio = float(thr_lo)
        self.min_event_ms = float(min_event_ms)
        self.squelch_db = float(squelch_db)
        self._muted = False
        self.carrier = False
        self._ps_ema = 1e-6
        self._pn_ema = 1e-3
        self._env = 0.0
        self._env_alpha = 1.0 - np.exp(-1.0 / (env_tau_ms / 1000.0 * SR))
        self._noise = 1e-4
        self._peak = 1e-3
        self._keyed = False
        self._mark_len = 0
        self._gap_len = 0
        self._pending: list[np.ndarray] = []  # 初始化缓存的原始样本
        self._init_done = False
        self._n_samp = 0

        # timing / text state
        self.unit_ms = 60.0
        self._recent_marks: list[float] = []
        self._recent_gaps: list[float] = []
        self._char_marks: list[float] = []
        self._events: list[tuple[str, float]] = []
        self._warm = True
        self._words: list[str] = []
        self._pending_codes: list[str] = []
        self.text = ""
        self.snr_db = None

    # ------------------------------------------------------------ setup
    def _build_filter(self):
        lo = max(120.0, self.f0 - self.bandwidth / 2)
        hi = min(SR / 2 - 50, self.f0 + self.bandwidth / 2)
        if hi - lo < 25:
            hi = lo + 25
        self._sos = butter(2, [lo, hi], btype="band", fs=SR, output="sos")
        self._zi = np.zeros((self._sos.shape[0], 2))

    def set_tone(self, f0: float, bandwidth: float | None = None):
        self.f0 = float(f0)
        if bandwidth:
            self.bandwidth = float(bandwidth)
        self._build_filter()

    def reset(self):
        self._zi = np.zeros((self._sos.shape[0], 2))
        self._env = 0.0
        self._noise = 1e-4
        self._peak = 1e-3
        self._keyed = False
        self._mark_len = 0
        self._gap_len = 0
        self._recent_marks = []
        self._symbols = []
        self._words = []
        self._pending = []
        self._init_done = False
        self._n_samp = 0
        self.text = ""

    # ------------------------------------------------------------ output
    def _emit(self, chunk: str):
        if not chunk:
            return
        self.text += chunk
        if self.on_char:
            self.on_char(chunk)

    def _flush_char(self):
        if not self._char_marks:
            return
        self._pending_codes.append(list(self._char_marks))
        self._char_marks = []
        self._drain_pending()

    def _drain_pending(self):
        """Emit held characters once the unit estimate has seen enough marks."""
        if len(self._recent_marks) < 4:
            return
        while self._pending_codes:
            marks = self._pending_codes.pop(0)
            if self.snr_db is not None and self.snr_db < self.squelch_db:
                continue                       # 明显是噪声，不输出
            code = "".join("-" if m > 1.5 * self.unit_ms else "." for m in marks)
            ch = MORSE.get(code, "?")
            self._words.append(ch)
            self._emit(ch)

    def _flush_word(self):
        self._flush_char()
        if not self._pending_codes:
            self._words = []
            if self.text and not self.text.endswith(" "):
                self._emit(" ")

    # ------------------------------------------------------------ timing
    def _update_unit(self, mark_ms: float):
        """Estimate the dot length from the *short* marks seen so far.

        Using the 25th percentile of recent marks is scale-free: it converges to
        the dot length after a few characters at any speed, and a single dash can
        never inflate it (which would merge every following character).
        """
        self._recent_marks.append(mark_ms)
        if len(self._recent_marks) > 60:
            self._recent_marks.pop(0)
        if len(self._recent_marks) >= 3:
            arr = np.sort(np.asarray(self._recent_marks))
            take = max(2, int(len(arr) * 0.4))
            self.unit_ms = float(np.median(arr[:take]))
            self.unit_ms = max(25.0, min(200.0, self.unit_ms))
        if self.on_status:
            self.on_status(self)

    def _decode_marks(self, marks: list[float]) -> str:
        code = "".join("-" if m > 1.5 * self.unit_ms else "." for m in marks)
        return MORSE.get(code, "?")

    def _warm_finish(self):
        """First六 marks are enough: decode the buffered events in one pass."""
        self._warm = False
        chars: list[str] = []
        cur: list[float] = []
        for kind, ms in self._events:
            if kind == "mark":
                cur.append(ms)
                continue
            units = ms / max(1e-6, self.unit_ms)
            if units >= 5.0:
                if cur:
                    chars.append(self._decode_marks(cur)); cur = []
                chars.append(" ")
            elif units >= 2.2:
                if cur:
                    chars.append(self._decode_marks(cur)); cur = []
        if cur:
            chars.append(self._decode_marks(cur))
        if self.snr_db is not None and self.snr_db < self.squelch_db:
            self._events = []
            return
        raw = "".join(chars)
        tail_space = raw.endswith(" ")
        text = " ".join(raw.split())
        if tail_space and text:
            text += " "          # 预热正好停在词边界时，这个空格要保留
        self._events = []
        if text:
            self._emit(text)

    def _on_mark_end(self, samples: int):
        ms = samples / SR * 1000.0
        if ms < self.min_event_ms:
            return
        if ms > 2500.0:
            # 长载波（信标前导、啸叫、一直按着键）：
            # 丢弃并重置电平/单位估计，否则后续真实信号会被判成"全是划"
            self._char_marks = []
            self._events = []
            self._recent_marks = []
            self.unit_ms = 60.0
            self._peak = max(self._noise * 5.0, 1e-9)
            self.carrier = True
            return
        self.carrier = False
        self._update_unit(ms)
        if self._warm:
            self._events.append(("mark", ms))
            return
        self._char_marks.append(ms)

    def _on_gap_end(self, samples: int):
        ms = samples / SR * 1000.0
        if ms < self.min_event_ms:
            return
        if self._warm:
            self._events.append(("gap", ms))
            n_marks = sum(1 for k, _ in self._events if k == "mark")
            # 只在"字符/词边界"结束预热，避免把一个字符切成两半
            if n_marks >= 6 and ms / max(1e-6, self.unit_ms) >= 2.2:
                self._warm_finish()
            return
        units = ms / max(1e-6, self.unit_ms)
        if units >= 5.0:
            self._flush_word()
        elif units >= 2.2:
            self._flush_char()

    # ------------------------------------------------------------ main
    def feed(self, block: np.ndarray):
        """Process one block of 8 kHz mono float32 samples."""
        if block is None or len(block) == 0:
            return
        x = np.asarray(block, dtype=np.float64)

        # 初始化：先把前 0.5 秒缓存起来，统计好底噪/峰值后再统一处理，
        # 否则开头几个字符会因为门限未收敛而粘连。
        if not self._init_done:
            self._pending.append(x)
            pending_len = sum(len(p) for p in self._pending)
            if pending_len < 4000:
                return
            buf = np.concatenate(self._pending)
            self._pending = []
            yb = sosfilt(self._sos, buf)
            env_b = np.abs(yb)
            k = max(1, int(0.006 * SR))
            env_b = np.convolve(env_b, np.ones(k) / k, mode="same")
            self._noise = float(np.percentile(env_b, 20)) + 1e-9
            self._peak = float(np.percentile(env_b, 97)) + 1e-9
            if self._peak <= self._noise:
                self._peak = self._noise * 10.0
            # 用初始化窗口顺便估一个"单位时长"，避免慢速时开头误分割
            thr = self._noise + 0.4 * (self._peak - self._noise)
            on = env_b > thr
            runs, cur, state = [], 0, on[0] if len(on) else False
            for v in on:
                if v == state:
                    cur += 1
                else:
                    runs.append((state, cur)); state, cur = v, 1
            runs.append((state, cur))
            marks = [ln / SR * 1000.0 for st0, ln in runs if st0 and ln > 8]
            if marks:
                unit0 = float(np.percentile(marks, 30))
                self.unit_ms = max(25.0, min(200.0, unit0))
                # 不要用这个初值"喂"统计窗口，否则前几个字符会沿用错误的单位

            self._init_done = True
            self._zi = np.zeros((self._sos.shape[0], 2))
            x = buf

        if self.snr_db is not None:
            # 迟滞：进入静噪 10 dB，恢复 11.5 dB，避免临界抖动
            if self._muted:
                self._muted = self.snr_db < self.squelch_db + 2.0
            else:
                self._muted = self.snr_db < self.squelch_db
        else:
            self._muted = True

        y, self._zi = sosfilt(self._sos, x, zi=self._zi)

        for v in np.abs(y):
            self._env += self._env_alpha * (v - self._env)
            env = self._env
            self._n_samp += 1

            # 自适应门限：底噪与峰值各自跟踪
            if self._keyed:
                thr_hi = self._noise + (self.thr_hi_ratio - 0.05) * (self._peak - self._noise)
                thr_lo = self._noise + self.thr_lo_ratio * (self._peak - self._noise)
            else:
                thr_hi = self._noise + self.thr_hi_ratio * (self._peak - self._noise)
                thr_lo = thr_hi

            if not self._keyed:
                if env > thr_hi:
                    # 由间隙进入按键
                    if self._gap_len > 0:
                        self._on_gap_end(self._gap_len)
                    self._gap_len = 0
                    self._keyed = True
                    self._mark_len = 0
                else:
                    self._gap_len += 1
                    self._noise += 0.0002 * (env - self._noise)
                    # 峰值在"未按键"时也要能跟上，否则静噪会把自己锁死
                    if env > self._peak:
                        self._peak = env
                    else:
                        self._peak *= 0.99999 if self._muted else 0.999995
            else:
                if env < thr_lo:
                    self._on_mark_end(self._mark_len)
                    self._keyed = False
                    self._gap_len = 0
                    self._mark_len = 0
                else:
                    self._mark_len += 1
                    if env > self._peak:
                        self._peak = env                      # 瞬时上升
                    else:
                        self._peak *= 0.999995                # 慢速衰减

        # 粗估 SNR（按键/间隙电平比）
        # 带内/带外能量比，做长时平滑（按块，时间常数约 1 秒）
        ps = float(np.mean(y ** 2)) + 1e-16
        pt = float(np.mean(x ** 2)) + 1e-16
        pn = max(pt - ps, 1e-16)
        a = min(1.0, len(x) / (1.0 * SR))          # 每块占 1 秒的比例
        self._ps_ema += a * (ps - self._ps_ema)
        self._pn_ema += a * (pn - self._pn_ema)
        raw = 10.0 * float(np.log10((self._ps_ema + 1e-16) / (self._pn_ema + 1e-16)))
        self.snr_db = round(raw, 1)

    # ------------------------------------------------------------ end
    def flush(self):
        """Call when the audio stops, to emit the trailing character."""
        if self._keyed and self._mark_len:
            self._on_mark_end(self._mark_len)
            self._keyed = False
            self._mark_len = 0
        self._flush_char()
