# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA (GitHub: thunder719x)
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live audio capture: microphone (sounddevice) or system audio (soundcard loopback).

Both sources are converted to mono float32 at 8 kHz and pushed to a shared
block callback. The engine also keeps a rolling buffer and a level estimate,
so the GUI can show a meter and save what was captured.
"""
from __future__ import annotations

import queue
import threading
import time
import wave

import numpy as np

# 注意：这两个 import 必须放在模块顶层。
# SciPy 1.18 是惰性加载，写在函数里每次调用会多花约 50 ms，
# 足以让音频回调超时、丢帧。
from math import gcd as _gcd
from scipy.signal import resample_poly as _resample_poly

try:
    import sounddevice as sd
    _HAS_SD = True
except Exception:                                    # pragma: no cover
    sd = None
    _HAS_SD = False

try:
    import soundcard as sc
    _HAS_SC = True
except Exception:                                    # pragma: no cover
    sc = None
    _HAS_SC = False

try:
    import pyaudiowpatch as pyaudio
    _HAS_PYA = True
except Exception:                                    # pragma: no cover
    pyaudio = None
    _HAS_PYA = False

TARGET_SR = 8000


class CaptureEngine:
    """Unified capture front-end (mic or loopback) -> 8 kHz mono blocks."""

    def __init__(self, on_block=None, tail_seconds: float = 120.0):
        self.on_block = on_block
        self.tail_seconds = tail_seconds
        self.level_db = -120.0
        self._buf: list[np.ndarray] = []
        self._buf_samp = 0
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        self._stream = None
        self._tail = None
        self._raw = []            # 重采样用的重叠尾巴
        self._raw: list[np.ndarray] = []
        self._src_rate = 0
        self.rate = TARGET_SR
        self.source_id: str | None = None
        self.error: str | None = None

    # ------------------------------------------------------------ discovery
    @staticmethod
    def list_sources() -> list[dict]:
        out: list[dict] = []
        if _HAS_SD:
            try:
                for i, d in enumerate(sd.query_devices()):
                    if d.get("max_input_channels", 0) > 0 and d.get("hostapi") is not None:
                        # 只保留每个名字的第一个，避免同一设备重复出现
                        name = d["name"]
                        if any(o["name"] == name for o in out):
                            continue
                        out.append({"id": f"mic:{i}", "name": name, "kind": "mic"})
            except Exception:
                pass
        if _HAS_SC:
            try:
                spk = sc.default_speaker().name
                out.insert(0, {"id": "loopback:default", "name": f"系统声音 (回环: {spk})",
                               "kind": "loopback"})
            except Exception:
                pass
        return out

    # ------------------------------------------------------------ control
    def start(self, source_id: str):
        self.stop()
        self.error = None
        self.source_id = source_id
        try:
            kind, _, idx = source_id.partition(":")
            if kind == "loopback":
                self._start_loopback()
            else:
                self._start_mic(int(idx))
            self._running = True
            return True
        except Exception as e:                        # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            self._running = False
            return False

    def stop(self):
        self._running = False
        st, self._stream = self._stream, None
        if st is not None:
            try:
                st.stop()
                st.close()
            except Exception:
                pass
        stop_ev = getattr(self, "_pya_stop", None)
        if stop_ev is not None:
            stop_ev.set()
            self._pya_stop = None
        th, self._thread = self._thread, None
        if th is not None and th.is_alive():
            th.join(timeout=1.0)
        self._flush_raw()
        pa = getattr(self, "_pya_pa", None)
        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass
            self._pya_pa = None

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------ sources
    def _start_mic(self, device: int):
        info = sd.query_devices(device)
        sr = int(round(float(info.get("default_samplerate") or 48000)))
        self._src_rate = sr
        self.rate = sr
        self._tail = None
        self._raw = []

        def cb(indata, frames, tinfo, status):        # noqa: ARG001
            if not self._running:
                return
            mono = indata[:, 0] if indata.ndim > 1 else indata
            self._process(np.asarray(mono, dtype=np.float32).copy(), sr)

        self._stream = sd.InputStream(device=device, channels=1, samplerate=sr,
                                      dtype="float32", blocksize=1024, callback=cb)
        self._stream.start()

    def _start_loopback_pya(self) -> bool:
        if not _HAS_PYA:
            return False
        pa = pyaudio.PyAudio()
        try:
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            spk = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            lb = None
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d.get("isLoopbackDevice") and d["name"].startswith(spk["name"]):
                    lb = d
                    break
            if lb is None:
                pa.terminate()
                return False

            sr = int(lb["defaultSampleRate"])
            ch = min(2, int(lb["maxInputChannels"]))
            self._src_rate = sr
            self.rate = sr                      # 同步告知外部（解码器要用）
            self._tail = None
            self._raw = []
            stop = threading.Event()

            def reader():
                try:
                    st = pa.open(format=pyaudio.paFloat32, channels=ch, rate=sr, input=True,
                                 input_device_index=lb["index"], frames_per_buffer=4096)
                except Exception as e:                       # noqa: BLE001
                    self.error = f"pya-open: {type(e).__name__}: {e}"
                    self._running = False
                    stop.set()
                    return
                try:
                    while not stop.is_set():
                        raw = st.read(4096, exception_on_overflow=False)
                        a = np.frombuffer(raw, dtype=np.float32).reshape(-1, ch)
                        mono = a.mean(axis=1) if ch > 1 else a
                        self._process(np.asarray(mono, dtype=np.float32).copy(), sr)
                finally:
                    try:
                        st.stop_stream(); st.close()
                    except Exception:
                        pass

            rt = threading.Thread(target=reader, daemon=True)
            self._pya_stop = stop
            self._pya_pa = pa
            rt.start()
            self._thread = rt
            return True
        except Exception as e:                                # noqa: BLE001
            self.error = f"pya: {type(e).__name__}: {e}"
            try:
                pa.terminate()
            except Exception:
                pass
            return False

    def _start_loopback(self):
        if self._start_loopback_pya():
            return
        spk = sc.default_speaker()
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        self._src_rate = 48000
        self._tail = None
        self._raw = []

        def run():
            # soundcard 走 WASAPI，需要在线程内初始化 COM（否则 0x800401f0）
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)   # MTA
            except Exception:
                pass
            q: queue.Queue = queue.Queue(maxsize=64)

            def worker():
                while True:
                    try:
                        blk = q.get(timeout=0.2)
                    except queue.Empty:
                        if not self._running:
                            break
                        continue
                    try:
                        mono = blk.mean(axis=1) if blk.ndim > 1 else blk
                        self._process(np.asarray(mono, dtype=np.float32).copy(), 48000)
                    except Exception:
                        pass

            wt = threading.Thread(target=worker, daemon=True)
            wt.start()
            try:
                with mic.recorder(samplerate=48000, channels=2, blocksize=1200) as rec:
                    while self._running:
                        # 只做"取数据 -> 入队"，处理放到 worker，避免读取落后
                        blk = rec.record(numframes=1200)
                        try:
                            q.put_nowait(blk)
                        except queue.Full:
                            # 队列满：丢掉最旧的一块，绝不阻塞读取
                            try:
                                q.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                q.put_nowait(blk)
                            except queue.Full:
                                pass
            except Exception as e:                    # noqa: BLE001
                self.error = f"loopback: {type(e).__name__}: {e}"
                self._running = False
            finally:
                wt.join(timeout=1.0)
                try:
                    import ctypes
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------ dsp
    def _process(self, block: np.ndarray, sr_in: int):
        # 完全不在这里重采样：保留原始采样率，解码时整段转换一次
        # （分块重采样会在块边界引入相位误差，实测会让解码出错）
        y = np.asarray(block, dtype=np.float32)
        if y is None or len(y) == 0:
            return
        self.rate = sr_in
        rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)) + 1e-12)
        self.level_db = 20.0 * np.log10(rms + 1e-12)
        with self._lock:
            self._buf.append(y)
            self._buf_samp += len(y)
            limit = int(self.tail_seconds * (self.rate or TARGET_SR))
            while self._buf_samp > limit and len(self._buf) > 1:
                self._buf_samp -= len(self._buf.pop(0))
        if self.on_block:
            try:
                self.on_block(y)
            except Exception:
                pass

    def _flush_raw(self):
        """把不足 0.5 秒的残余块也转换出来（停止采集时调用）。"""
        raw, self._raw = getattr(self, "_raw", []), []
        if not raw:
            return
        try:
            chunk = np.concatenate(raw)
            y = self._resample(chunk, self._src_rate or TARGET_SR)
            if y is None or len(y) == 0:
                return
            with self._lock:
                self._buf.append(y)
                self._buf_samp += len(y)
            if self.on_block:
                self.on_block(y)
        except Exception:
            pass

    def _resample(self, block: np.ndarray, sr_in: int) -> np.ndarray | None:
        """Block resampling with a 0.1 s overlap to avoid edge artefacts."""
        if sr_in == TARGET_SR:
            return block
        g = _gcd(int(sr_in), TARGET_SR)
        up, down = TARGET_SR // g, int(sr_in) // g

        overlap = int(0.1 * sr_in)
        if self._tail is None:
            buf = block
            skip_src = 0
        else:
            buf = np.concatenate([self._tail, block])
            skip_src = len(self._tail)
        self._tail = block[-overlap:] if len(block) > overlap else block

        out = _resample_poly(buf.astype(np.float32), up, down)
        skip_out = int(round(skip_src * TARGET_SR / sr_in))
        return out[skip_out:]

    # ------------------------------------------------------------ buffer
    def snapshot8k(self) -> np.ndarray:
        y = self.snapshot()
        if y.size == 0 or self.rate == TARGET_SR:
            return y
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(int(self.rate), TARGET_SR)
        return resample_poly(y, TARGET_SR // g, int(self.rate) // g).astype(np.float32)

    def snapshot(self) -> np.ndarray:
        with self._lock:
            if not self._buf:
                return np.zeros(0, dtype=np.float32)
            return np.concatenate(self._buf).astype(np.float32)

    def clear(self):
        with self._lock:
            self._buf = []
            self._buf_samp = 0
        self._tail = None
        self._raw = []

    def save_wav(self, path: str) -> float:
        y = self.snapshot8k()
        pcm = np.clip(y, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TARGET_SR)
            w.writeframes(pcm.tobytes())
        return len(y) / TARGET_SR
