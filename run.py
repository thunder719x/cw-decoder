# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Launcher: python run.py  (or double-click run.bat on Windows).

Single-instance guard: if a decoder window is already open, bring it to the front
instead of starting a second copy.  Also installs a crash log writer, because a
windowed (pythonw / --noconsole) build shows no console output.
"""
import ctypes
import os
import sys
import traceback
from ctypes import wintypes

TITLES = ("CW 解码器", "CW Decoder", "CW декодер")


def _log_path() -> str:
    """Where to write crash.log (must be writable even in Program Files)."""
    if getattr(sys, "frozen", False):        # 打包后：用 exe 所在目录，而不是临时解包目录
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    for cand in (base, os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                                    "CWDecoder")):
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".wtest")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return os.path.join(cand, "crash.log")
        except Exception:
            continue
    return os.path.join(os.path.expanduser("~"), "cwdecoder_crash.log")


def _install_crash_log():
    path = _log_path()

    def hook(exc_type, exc, tb):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("\n===== " + __import__("time").strftime("%Y-%m-%d %H:%M:%S") + " =====\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook
    return path


def _existing_window():
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value in TITLES:
            found.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else None


def _selftest() -> int:
    """--selftest：不开界面，解一个样例并写出结果（用于验证打包产物）。"""
    import time
    out = os.path.join(os.path.dirname(_log_path()), "self-test.txt")
    lines = []
    try:
        from cwdec import pipeline
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
        samples = ["sample_cq.wav", "sample_noisy.wav"]
        for name in samples:
            path = os.path.join(base, name)
            if not os.path.exists(path):
                lines.append(f"{name}: MISSING")
                continue
            for backend in ("dsp", "ctc"):
                t0 = time.time()
                try:
                    res = pipeline.decode_file(path, backend=backend)
                    lines.append(f"{name} / {backend}: {res.get('text','')!r} "
                                 f"[{time.time()-t0:.2f}s]")
                except Exception as e:
                    lines.append(f"{name} / {backend}: ERROR {type(e).__name__}: {e}")
    except Exception as e:
        lines.append(f"IMPORT ERROR {type(e).__name__}: {e}")
    # 界面语言自检（确认多语言资源确实被打进包里）
    try:
        from cwdec.gui import STRINGS
        lines.append("ui_langs=" + ",".join(STRINGS))
        for lang in STRINGS:
            lines.append(f"ui_{lang}_title={STRINGS[lang].get('title', '')}")
    except Exception as e:
        lines.append(f"UI ERROR {type(e).__name__}: {e}")

    lines.append(f"python={sys.version.split()[0]} frozen={getattr(sys,'frozen',False)}")
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    return 0


def main():
    _install_crash_log()

    if "--selftest" in sys.argv:
        return _selftest()

    hwnd = _existing_window()
    if hwnd:
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 6)      # 最小化
        user32.ShowWindow(hwnd, 9)      # 还原（能把它带到前台）
        user32.SetForegroundWindow(hwnd)
        print("已有窗口，已切到前台。")
        return 0

    from cwdec.gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main() or 0)
