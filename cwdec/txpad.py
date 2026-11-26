# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""CW transmit practice pad ("发送练习").

A small window that turns typed text into Morse audio you can listen to and save.
It reuses :mod:`cwdec.synth` (already used by the test-suite) and
:class:`cwdec.player.Player` for playback, so no new dependency is needed.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from . import synth as synth_mod
from .player import Player

# 20 wpm -> 1.2/20 = 60 ms dot; the slider is limited to the range that is
# actually useful for practice (5 wpm is very slow, 40 wpm is contest speed).
WPM_MIN = 5
WPM_MAX = 40
DEFAULT_WPM = 20

# Sidetone pitches that hams actually use: 600-800 Hz is the common range and
# 700 Hz is what most transceivers ship with.
TONE_CHOICES = ["500", "600", "700", "800", "1000"]
DEFAULT_TONE = "700"

# (中文名, English name, Русское имя, 报文)  —— {call} 会被呼号框替换
TEMPLATES = [
    ("CQ 呼叫", "CQ call", "Общий вызов CQ",
     "CQ CQ CQ DE {call} {call} K"),
    ("CQ DX 远距离", "CQ DX", "CQ DX",
     "CQ DX CQ DX DE {call} {call} K"),
    ("CQ TEST 比赛", "CQ TEST", "CQ TEST",
     "CQ TEST {call} {call} TEST"),
    ("回应对方 CQ", "Answer a CQ", "Ответ на CQ",
     "GE OM TNX FER CALL UR RST 599 599 NAME IS LI LI QTH IS BEIJING K"),
    ("信号报告 + 姓名/地点", "RST + name/QTH", "Рапорт + имя/QTH",
     "UR RST 5NN 5NN NAME IS LI LI QTH IS BEIJING BEIJING K"),
    ("结束联络", "End of QSO", "Конец связи",
     "TNX FER QSO 73 ES GL DE {call} SK"),
    ("请求重复", "Ask to repeat", "Просьба повторить",
     "AGN? AGN? PSE RPT UR RST K"),
    ("请发慢一点", "Please send slower", "Передавайте медленнее",
     "PSE QRS PSE QRS K"),
    ("QRL? 频率是否占用", "QRL? is the frequency free", "QRL? частота занята?",
     "QRL? QRL? DE {call}"),
    ("QRZ? 谁在呼叫我", "QRZ? who is calling me", "QRZ? кто меня зовёт",
     "QRZ? QRZ? DE {call} K"),
    ("比赛交换序号", "Contest exchange", "Обмен в тесте",
     "5NN 001 5NN 001 TU"),
    ("PSE K 请继续", "PSE K please go ahead", "PSE K продолжайте",
     "PSE K"),
    # ---- 俄语报文（字母用西里尔，摩斯码按俄文字母表） ----
    ("RU 问候（ПРИВЕТ）", "RU greeting (PRIVET)", "Приветствие",
     "ПРИВЕТ ПРИВЕТ DE {call} {call} K"),
    ("RU 信号报告 РСТ", "RU signal report (RST)", "Рапорт РСТ",
     "РСТ 599 599 ИМЯ ЛИ КТО ПЕКИН К"),
    ("RU 感谢联络 73", "RU thanks + 73", "Спасибо за связь 73",
     "СПАСИБО ЗА СВЯЗЬ 73 ДО СВИДАНИЯ"),
    ("RU 请重复", "RU ask to repeat", "Повторите пожалуйста",
     "ПОВТОРИТЕ ПОЖАЛУЙСТА РСТ"),
    ("RU 这里是...", "RU here is ...", "Здесь ...",
     "ТУТ {call} ТУТ {call} К"),
    ("RU 祝好", "RU best wishes", "Всего доброго",
     "ХОРОШЕГО ДНЯ И 73"),
]
TPL_LANGS = ("zh", "en", "ru")
TPL_LANG_LABELS = {"zh": "中文", "en": "EN", "ru": "РУ"}


class TxPad(tk.Toplevel):
    """Editor + player for generated CW audio."""

    def __init__(self, master, tr, lang: str = "zh"):
        super().__init__(master)
        self.tr = tr                      # translation function (key -> str)
        self._lang = lang
        self.player = Player()
        self._audio = None
        self._audio_key = None

        self.title(self.tr("txpad_title"))
        self.geometry("760x520")
        self.minsize(640, 440)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self.tpl_box.current(0)
        self._apply_template()            # 预填一条 CQ，打开即可用

    # ------------------------------------------------------------ layout
    def _build(self):
        pad = ttk.Frame(self, padding=10)
        pad.pack(fill="both", expand=True)

        # 模板 + 呼号
        top = ttk.Frame(pad)
        top.pack(fill="x")
        self.tpl_lang_var = tk.StringVar(value=getattr(self, "_lang", "zh"))
        self.tpl_lang_box = ttk.Combobox(
            top, textvariable=self.tpl_lang_var, state="readonly", width=4,
            values=[TPL_LANG_LABELS[k] for k in TPL_LANGS])
        self.tpl_lang_box.current(TPL_LANGS.index(self.tpl_lang_var.get())
                                  if self.tpl_lang_var.get() in TPL_LANGS else 0)
        self.tpl_lang_box.pack(side="left", padx=(0, 10))
        self.tpl_lang_box.bind("<<ComboboxSelected>>", lambda e: self._on_tpl_lang())

        ttk.Label(top, text=self.tr("txpad_template")).pack(side="left")
        self.tpl_var = tk.StringVar()
        self.tpl_box = ttk.Combobox(top, textvariable=self.tpl_var, state="readonly",
                                    width=26, values=[self._label(t) for t in TEMPLATES])
        self.tpl_box.pack(side="left", padx=(6, 12))
        self.tpl_box.bind("<<ComboboxSelected>>", lambda e: self._apply_template())

        ttk.Label(top, text=self.tr("txpad_call")).pack(side="left")
        self.call_var = tk.StringVar(value="BD6LBA")
        ttk.Entry(top, textvariable=self.call_var, width=12).pack(side="left", padx=(6, 0))

        # 文本
        ttk.Label(pad, text=self.tr("txpad_text")).pack(anchor="w", pady=(10, 2))
        self.text = tk.Text(pad, height=7, wrap="word", font=("Consolas", 11))
        self.text.pack(fill="both", expand=True)
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<Control-Return>", lambda e: (self.on_play(), "break")[1])

        # 参数
        par = ttk.Frame(pad)
        par.pack(fill="x", pady=(10, 0))

        ttk.Label(par, text=self.tr("txpad_tone")).pack(side="left")
        self.tone_var = tk.StringVar(value=DEFAULT_TONE)
        ttk.Combobox(par, textvariable=self.tone_var, width=6,
                     values=TONE_CHOICES).pack(side="left", padx=(6, 16))

        ttk.Label(par, text=self.tr("txpad_wpm")).pack(side="left")
        self.wpm_var = tk.IntVar(value=DEFAULT_WPM)
        self.wpm_scale = ttk.Scale(par, from_=WPM_MIN, to=WPM_MAX, length=200,
                                   command=self._on_wpm)
        self.wpm_scale.set(DEFAULT_WPM)
        self.wpm_scale.pack(side="left", padx=(6, 6))
        self.lbl_wpm = ttk.Label(par, text=f"{DEFAULT_WPM} WPM", width=8)
        self.lbl_wpm.pack(side="left")

        # 按钮
        btns = ttk.Frame(pad)
        btns.pack(fill="x", pady=(12, 0))
        self.btn_play = ttk.Button(btns, text=self.tr("txpad_play"), command=self.on_play)
        self.btn_play.pack(side="left")
        self.btn_stop = ttk.Button(btns, text=self.tr("txpad_stop"), command=self.on_stop)
        self.btn_stop.pack(side="left", padx=(6, 0))
        self.btn_save = ttk.Button(btns, text=self.tr("txpad_save"), command=self.on_save)
        self.btn_save.pack(side="left", padx=(12, 0))
        ttk.Button(btns, text=self.tr("txpad_close"), command=self._on_close).pack(side="right")

        self.lbl_info = ttk.Label(pad, text="", foreground="#666")
        self.lbl_info.pack(anchor="w", pady=(8, 0))

        for w in (self.text,):
            w.bind("<KeyRelease>", lambda e: self._update_info())
        self._update_info()

    # ------------------------------------------------------------ helpers
    def _on_modified(self, _evt=None):
        self.text.edit_modified(False)
        self._update_info()

    def _on_wpm(self, _value=None):
        v = int(round(float(self.wpm_scale.get())))
        self.wpm_var.set(v)
        if hasattr(self, "lbl_wpm"):
            self.lbl_wpm.configure(text=f"{v} WPM")
        if hasattr(self, "lbl_info"):
            self._update_info()

    def _params(self):
        try:
            tone = float(self.tone_var.get() or DEFAULT_TONE)
        except ValueError:
            tone = float(DEFAULT_TONE)
        tone = min(max(tone, 200.0), 2000.0)
        wpm = min(max(int(self.wpm_var.get()), WPM_MIN), WPM_MAX)
        return tone, float(wpm)

    def _current_text(self) -> str:
        return self.text.get("1.0", "end").strip()

    def _build_audio(self):
        """(Re)generate audio only when text/tone/wpm changed."""
        text = self._current_text()
        tone, wpm = self._params()
        key = (text, tone, wpm)
        if self._audio is not None and key == self._audio_key:
            return self._audio
        self._audio = synth_mod.synth(text, wpm=wpm, tone=tone, sr=8000) if text else None
        self._audio_key = key
        return self._audio

    def _update_info(self):
        text = self._current_text()
        chars = sum(1 for ch in text.upper() if ch in synth_mod.MORSE)
        tone, wpm = self._params()
        audio = self._build_audio() if text else None
        dur = (len(audio) / 8000.0) if audio is not None else 0.0
        self.lbl_info.configure(
            text=self.tr("txpad_info").format(chars=chars, wpm=int(wpm),
                                              tone=int(tone), dur=f"{dur:.1f}"))

    def _tpl_lang(self) -> str:
        label = self.tpl_lang_var.get()
        for k, v in TPL_LANG_LABELS.items():
            if v == label:
                return k
        return "zh"

    def _on_tpl_lang(self):
        self._lang = self._tpl_lang()
        self.tpl_box.configure(values=[self._label(t) for t in TEMPLATES])
        self.tpl_box.current(0)
        self._apply_template()

    def _label(self, item) -> str:
        zh, en, ru, _body = item
        return {"zh": zh, "en": en, "ru": ru}.get(self._tpl_lang(), zh)

    def _apply_template(self):
        labels = [self._label(t) for t in TEMPLATES]
        name = self.tpl_var.get() or labels[0]
        try:
            idx = labels.index(name)
        except ValueError:
            idx = 0
        call = (self.call_var.get() or "N0CALL").upper()
        body = TEMPLATES[idx][3].replace("{call}", call)
        self.text.delete("1.0", "end")
        self.text.insert("1.0", body)
        self._update_info()

    # ------------------------------------------------------------ actions
    def on_play(self):
        audio = self._build_audio()
        if audio is None:
            messagebox.showinfo(self.tr("txpad_title"), self.tr("txpad_empty"))
            return
        self.player.set_audio(audio, src_sr=8000, gain=0.85)
        self.player.play()

    def on_stop(self):
        self.player.stop()

    def on_save(self):
        audio = self._build_audio()
        if audio is None:
            messagebox.showinfo(self.tr("txpad_title"), self.tr("txpad_empty"))
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".wav",
            filetypes=[("WAV", "*.wav")],
            initialfile="cq.wav")
        if not path:
            return
        synth_mod.write_wav(path, np.asarray(audio, dtype=np.float32), 8000)
        messagebox.showinfo(self.tr("txpad_title"), self.tr("txpad_saved").format(path=path))

    def _on_close(self):
        try:
            self.player.stop()
        except Exception:
            pass
        self.destroy()
