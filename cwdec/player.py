# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Audio playback for the CW decoder (sounddevice based).

The player keeps its own sample buffer at the device rate, exposes an exact
playback position (taken from the callback frame counter, not from a wall
clock), and supports play / pause / stop / seek.
"""
from __future__ import annotations

import threading

import numpy as np

try:
    import sounddevice as sd
    _HAS_SD = True
except Exception:                                   # pragma: no cover
    sd = None
    _HAS_SD = False


def device_samplerate() -> int:
    if not _HAS_SD:
        return 48000
    try:
        dev = sd.default.device[1]
        info = sd.query_devices(dev)
        return int(round(float(info.get("default_samplerate") or 48000)))
    except Exception:
        return 48000


class Player:
    """Plays a mono float array, resampled to the output device rate."""

    def __init__(self):
        self.sr = device_samplerate()
        self._buf: np.ndarray | None = None
        self._pos = 0
        self._stream = None
        self._lock = threading.Lock()
        self._paused = False
        self._done = True
        self.volume = 1.0

    # ------------------------------------------------------------ source
    def set_audio(self, y: np.ndarray, src_sr: int = 8000, gain: float = 1.0):
        """Load a new buffer (mono float). Stops any playback in progress."""
        self.stop()
        y = np.asarray(y, dtype=np.float32)
        if src_sr != self.sr:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(int(src_sr), int(self.sr))
            y = resample_poly(y, self.sr // g, int(src_sr) // g).astype(np.float32)
        y = np.clip(y * float(gain), -1.0, 1.0)
        with self._lock:
            self._buf = np.ascontiguousarray(y)
            self._pos = 0
            self._done = True

    @property
    def duration(self) -> float:
        return 0.0 if self._buf is None else len(self._buf) / self.sr

    @property
    def position(self) -> float:
        return self._pos / self.sr

    @property
    def is_playing(self) -> bool:
        return self._stream is not None and not self._paused and not self._done

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------ control
    def play(self, start: float = 0.0):
        if self._buf is None:
            return
        self.stop()
        self._pos = int(max(0.0, min(start, self.duration)) * self.sr)
        if self._pos >= len(self._buf):
            self._pos = 0
        self._paused = False
        self._done = False
        self._stream = sd.OutputStream(
            samplerate=self.sr, channels=1, dtype="float32",
            blocksize=1024, callback=self._callback)
        self._stream.start()

    def pause(self):
        self._paused = True

    def resume(self):
        if self._stream is not None and not self._done:
            self._paused = False

    def stop(self):
        st, self._stream = self._stream, None
        if st is not None:
            try:
                st.stop()
                st.close()
            except Exception:
                pass
        self._paused = False
        self._done = True
        self.volume = 1.0

    def set_volume(self, v: float):
        self.volume = max(0.0, min(1.0, float(v)))

    def seek(self, seconds: float):
        self._pos = int(max(0.0, min(seconds, self.duration)) * self.sr)

    # ------------------------------------------------------------ callback
    def _callback(self, outdata, frames, time_info, status):   # noqa: ARG002
        if self._buf is None or self._paused or self._done:
            outdata.fill(0)
            return
        with self._lock:
            end = self._pos + frames
            chunk = self._buf[self._pos:end]
            n = len(chunk)
            outdata[:n, 0] = chunk * self.volume
            if n < frames:
                outdata[n:, 0] = 0.0
                self._done = True
            self._pos = end
