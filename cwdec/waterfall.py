# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA (GitHub: thunder719x)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rolling waterfall (scrolling spectrogram) for live mode.

Keeps a small RGB image buffer and shifts it one column per processed frame.
Rendering is done by the caller: `to_image()` returns a PIL image.
"""
from __future__ import annotations

import numpy as np

SR = 8000
N_FFT = 512
HOP = 256          # ~32 ms per column -> ~31 columns/s（约 40 秒历史）


def _lut() -> np.ndarray:
    stops = np.array([[0, 0, 0], [16, 32, 96], [32, 140, 190],
                      [250, 220, 60], [255, 255, 255]], float)
    pos = np.linspace(0, 1, len(stops))
    x = np.linspace(0, 1, 256)
    return np.stack([np.interp(x, pos, stops[:, i]) for i in range(3)],
                    axis=1).astype(np.uint8)


_LUT = _lut()


class Waterfall:
    def __init__(self, width: int = 900, height: int = 220,
                 fmin: float = 200.0, fmax: float = 1600.0, src_rate: int = 48000):
        self.width = max(50, int(width))
        self.height = max(20, int(height))
        self.fmin = float(fmin)
        self.fmax = float(fmax)
        self.src_rate = int(src_rate or SR)
        self._buf = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._raw: list[np.ndarray] = []
        self._pending = np.zeros(0, dtype=np.float32)
        self._win = np.hanning(N_FFT).astype(np.float32)
        self._freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
        self._levels = (0.0, 1.0)
        self._new = 0

    # ------------------------------------------------------------ params
    def resize(self, width: int, height: int):
        width, height = max(50, int(width)), max(20, int(height))
        if width == self.width and height == self.height:
            return
        old = self._buf
        self._buf = np.zeros((height, width, 3), dtype=np.uint8)
        h = min(height, old.shape[0])
        w = min(width, old.shape[1])
        self._buf[-h:, -w:] = old[-h:, -w:]
        self.width, self.height = width, height

    def set_range(self, fmin: float, fmax: float):
        self.fmin, self.fmax = float(fmin), float(fmax)
        self._buf[:] = 0

    def clear(self):
        self._buf[:] = 0
        self._raw = []
        self._pending = np.zeros(0, dtype=np.float32)
        self._new = 0

    # ------------------------------------------------------------ input
    def feed(self, block: np.ndarray, sr: int | None = None):
        """Accepts audio at the capture rate; resamples to 8 kHz internally."""
        if block is None or len(block) == 0:
            return
        sr = int(sr or self.src_rate)
        b = np.asarray(block, dtype=np.float32)
        if sr != self.src_rate:
            self.src_rate = sr
        self._raw.append(b)
        total = sum(len(x) for x in self._raw)
        if total < int(0.25 * sr):
            return
        chunk = np.concatenate(self._raw)
        self._raw = []
        if sr != SR:
            from math import gcd
            from scipy.signal import resample_poly
            g = gcd(sr, SR)
            chunk = resample_poly(chunk, SR // g, sr // g).astype(np.float32)

        data = np.concatenate([self._pending, chunk])
        n = 0
        while len(data) - n >= N_FFT:
            frame = data[n:n + N_FFT] * self._win
            self._push_column(frame)
            n += HOP
        self._pending = data[n:]

    # ------------------------------------------------------------ render
    def _push_column(self, frame: np.ndarray):
        spec = np.abs(np.fft.rfft(frame))
        mask = (self._freqs >= self.fmin) & (self._freqs <= self.fmax)
        if not mask.any():
            return
        vals = 20.0 * np.log10(spec[mask] + 1e-9)

        lo = float(np.percentile(vals, 10)) if vals.size else 0.0
        hi = float(np.percentile(vals, 95)) + 1e-6
        if hi - lo < 6.0:
            hi = lo + 6.0
        norm = np.clip((vals - lo) / (hi - lo), 0.0, 1.0)

        # 频率方向插值到 height 行（低频在下）
        idx = np.linspace(0, len(norm) - 1, self.height)
        col = np.interp(idx, np.arange(len(norm)), norm)
        rgb = _LUT[(col * 255).astype(np.uint8)]

        self._buf[:, :-1] = self._buf[:, 1:]
        self._buf[:, -1] = rgb
        self._new += 1

    def to_image(self):
        from PIL import Image
        img = self._buf[::-1, :, :]          # 低频在下
        return Image.fromarray(img)

    @property
    def frames(self) -> int:
        return self._new
