# CW Decoder — 摩斯码解码器
# Copyright (c) 2026 BD6LBA <https://github.com/thunder719x>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tk GUI for CW decoding — WAV input, DSP/CTC backends, clickable spectrum.

Features
--------
* Spectrogram with frequency (Hz) and time (s) axes
* Click the spectrogram, or type a frequency, to decode that tone
* Only one selection marker is kept at a time
* Backend switch (DSP / CTC), Cyrillic rendering, gate toggle
* Every decode is appended to out/clicklog.csv (ground truth for later
  automatic channel detection)
"""
from __future__ import annotations

import csv
import os
import time
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

from . import dsp, pipeline
from .player import Player
from .capture import CaptureEngine
from .window_dec import WindowDecoder
from .waterfall import Waterfall
from .channels import ChannelTracker

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:                                    # pragma: no cover
    _HAS_PIL = False

STRINGS = {
    "zh": {
        "title": "CW 解码器", "open": "打开 WAV", "close_file": "关闭文件",
        "decode": "开始解码", "save": "另存文本", "clear": "清空",
        "cyr": "西里尔", "latin": "拉丁", "gate": "门控(文件CTC)",
        "lang": "语言", "backend": "后端", "src": "文件",
        "tone": "音调", "wpm": "速度", "dur": "时长",
        "elapsed": "耗时", "none": "—", "no_file": "请先选择一个 WAV 文件",
        "error": "错误", "hint": "点击频谱选频率，或在右侧输入 Hz 后回车", "spectrum": "频谱（横轴时间 s，纵轴频率 Hz）",
        "selected": "已选", "go": "解码", "hz": "Hz",
        "bad_freq": "频率需在 100–3000 Hz 之间", "decoding": "正在解码…", "auto": "自动",
        "cmp": "对比", "be_dsp": "DSP（规则算法）", "be_ctc": "CTC（神经网络）",
        "be_auto": "自动（两个都跑）", "play": "▶ 播放", "pause": "⏸ 暂停",
        "stop": "⏹ 停止", "reset": "复位", "ch_only": "只播放选中频道",
        "bw": "带宽", "vol": "音量", "band": "播放带宽",
        "gate_hint": "门控：仅文件模式 + CTC（标记可疑字符）", "fix_hint": "纠错：文件模式与实时模式都生效", "live": "实时输入",
        "cap_start": "开始监听", "cap_stop": "停止", "cap_save": "保存录音(≤120s)",
        "cap_saved": "已保存", "cap_running": "监听中", "live_dec": "实时解码",
        "live_be": "实时算法", "fix": "纠错(通用)", "multi": "多频道",
        "live_click_hint": "实时模式：用「多频道」列表或手动输入 Hz 来换台", "follow": "跟随频率", "fx_hint": "提示：系统音频增强正在压缩动态范围，建议在声音设置里关闭它",
        "cap_idle": "未监听", "cap_none": "没有可用的输入设备", "stop_hint": "已停止监听 · 文本已保留（如需按其他频率重解，请保存录音后用文件模式）",
        "cap_stopped": "已停止监听 · 录音可保存最近 {sec} 秒", "dsp_desc": "规则算法：包络阈值 + 点划时长判定", "ctc_desc": "神经网络：11 维声学特征 + CTC 解码",
        "cmp_title": "两后端对比", "agree": "一致度", "snr_high": "高",
        "snr_good": "良好", "snr_low": "低", "snr_vlow": "极低",
        "cand": "频道", "cand_tip": "解码质量", "txtcap": "解码结果",
        "alpha": "字母表", "alpha_la": "拉丁", "alpha_ru": "西里尔",
        "empty_file": "打开 WAV 点「开始解码」，或点「开始监听」实时接收", "empty_live": "正在等待信号…（锁定音调后开始出字）", "txpad": "发送练习",
        "menu_tools": "工具", "menu_file": "文件", "txpad_title": "CW 发送练习",
        "txpad_template": "模板", "txpad_call": "呼号", "txpad_text": "要发送的内容",
        "txpad_tone": "音调 (Hz)", "txpad_wpm": "速度", "txpad_play": "▶ 播放",
        "txpad_stop": "⏹ 停止", "txpad_save": "💾 保存 WAV", "txpad_close": "关闭",
        "txpad_empty": "请先输入要发送的内容", "txpad_saved": "已保存到：{path}", "txpad_info": "{chars} 个字符 · {wpm} WPM · {tone} Hz · 约 {dur} 秒",
        "tpl_cq": "CQ CQ CQ DE {call}", "tpl_cq_test": "CQ TEST", "tpl_rst": "信号报告 RST",
        "tpl_73": "TU 73 SK", "tpl_qrl": "QRL?（频率占用询问）", "tpl_qrz": "QRZ?（谁在呼叫我）",
        "tpl_info": "NAME / QTH 介绍", "tpl_pse_k": "PSE K（请继续）", "col_freq": "频率 Hz",
        "col_dbfs": "强度 dB", "col_wpm": "速度", "col_call": "呼号 / 片段",
        "q_signal": "信号", "q_level": "电平", "q_snr": "SNR",
        "q_ratio": "时序", "q_decode": "解码", "q_hold": "（保持）",
        "q_stopped": "—（已停止）", "q_none": "无", "q_yes": "有",
        "q_fix": "纠错", "q_lvl_low": "⚠电平", "q_lvl": "电平",
        "q_good": "可信", "q_suspect": "可疑", "q_bad": "不可信",
        "tip_live_title": "实时信号质量（最近 10 秒）", "tip_stop_title": "实时信号质量（已停止 · 最后更新 {ts}）", "tip_lvl_rule": "≥-28 绿 · -33~-28 黄 · <-33 红",
        "tip_snr_rule": "≥18 绿 · 12~18 黄 · <12 红 · 参考带宽 180 Hz", "tip_sig_rule": "电平 > -60 dBFS 且音调性 ≥ 15（取最近 2 秒）", "tip_live_foot": "数值每秒更新、约 3 秒平滑；颜色需连续 2 秒确认；停止监听后冻结。",
        "tip_stop_foot": "以上为最后数值；复位后清除。", "tip_file_title": "信号质量（详细）", "tip_ratio_rule": "0.80~1.35 正常 · >1.55 混响拉伸 · <0.62 码元碎裂",
        "tip_decode_rule": "综合文本可信度与码元规律性", "tip_reg": "码元落在 1×/3× 单位网格的比例", "tip_textline": "? {q:.1f}% · 单字 {single:.1f}% · 非 ASCII {nonascii:.1f}%",
        "tip_no_data": "—（暂无数据）", "q_reg": "规律", "q_text": "文本"
    },
    "en": {
        "title": "CW Decoder", "open": "Open WAV", "close_file": "Close",
        "decode": "Decode", "save": "Save text", "clear": "Clear",
        "cyr": "Cyrillic", "latin": "Latin", "gate": "Gate (file/CTC)",
        "lang": "Language", "backend": "Backend", "src": "File",
        "tone": "Tone", "wpm": "Speed", "dur": "Duration",
        "elapsed": "Elapsed", "none": "—", "no_file": "Please choose a WAV file first",
        "error": "Error", "hint": "Click the spectrogram, or type a frequency and press Enter", "spectrum": "Spectrogram (x: time s, y: frequency Hz)",
        "selected": "selected", "go": "Decode", "hz": "Hz",
        "bad_freq": "Frequency must be 100–3000 Hz", "decoding": "Decoding…", "auto": "Auto",
        "cmp": "Compare", "be_dsp": "DSP (rules)", "be_ctc": "CTC (neural net)",
        "be_auto": "Auto (run both)", "play": "▶ Play", "pause": "⏸ Pause",
        "stop": "⏹ Stop", "reset": "Reset", "ch_only": "Selected channel only",
        "bw": "BW", "vol": "Vol", "band": "passband",
        "gate_hint": "Gate: file mode + CTC only (flags suspect chars)", "fix_hint": "Fix: applies to both file and live modes", "live": "Live input",
        "cap_start": "Start", "cap_stop": "Stop", "cap_save": "Save WAV (≤120s)",
        "cap_saved": "Saved", "cap_running": "listening", "live_dec": "Live decode",
        "live_be": "Engine", "fix": "Fix (all)", "multi": "Multi-CH",
        "live_click_hint": "Live mode: pick a channel from the list or type Hz", "follow": "Follow", "cap_idle": "idle",
        "cap_none": "no input device", "stop_hint": "Stopped · text kept (save the recording, then use File mode to decode another frequency)", "cap_stopped": "Stopped · recording keeps the last {sec}s",
        "dsp_desc": "Rule-based: envelope threshold + dot/dash timing", "ctc_desc": "Neural net: 11-D acoustic features + CTC decoding", "cmp_title": "Backend comparison",
        "agree": "Agreement", "snr_high": "high", "snr_good": "good",
        "snr_low": "low", "snr_vlow": "very low", "cand": "Channels",
        "cand_tip": "quality", "fx_hint": "Hint: Windows audio enhancements are compressing the signal — turn them off in Sound settings", "txtcap": "Decoded text",
        "alpha": "Alphabet", "alpha_la": "Latin", "alpha_ru": "Cyrillic",
        "empty_file": "Open a WAV and press Decode, or press Start to listen live", "empty_live": "Waiting for a signal… (text appears after the tone locks)", "txpad": "TX practice",
        "menu_tools": "Tools", "menu_file": "File", "txpad_title": "CW Transmit Practice",
        "txpad_template": "Template", "txpad_call": "Call", "txpad_text": "Text to send",
        "txpad_tone": "Tone (Hz)", "txpad_wpm": "Speed", "txpad_play": "▶ Play",
        "txpad_stop": "⏹ Stop", "txpad_save": "💾 Save WAV", "txpad_close": "Close",
        "txpad_empty": "Type something to send first", "txpad_saved": "Saved to: {path}", "txpad_info": "{chars} chars · {wpm} WPM · {tone} Hz · about {dur} s",
        "tpl_cq": "CQ CQ CQ DE {call}", "tpl_cq_test": "CQ TEST", "tpl_rst": "Signal report RST",
        "tpl_73": "TU 73 SK", "tpl_qrl": "QRL? (is this frequency in use)", "tpl_qrz": "QRZ? (who is calling me)",
        "tpl_info": "NAME / QTH intro", "tpl_pse_k": "PSE K (please go ahead)", "col_freq": "Freq Hz",
        "col_dbfs": "Level dB", "col_wpm": "Speed", "col_call": "Call / excerpt",
        "q_signal": "Signal", "q_level": "Level", "q_snr": "SNR",
        "q_ratio": "Timing", "q_decode": "Decode", "q_hold": "(hold)",
        "q_stopped": "— (stopped)", "q_none": "no", "q_yes": "yes",
        "q_fix": "fix", "q_lvl_low": "⚠Level", "q_lvl": "Level",
        "q_good": "ok", "q_suspect": "questionable", "q_bad": "unreliable",
        "tip_live_title": "Live signal quality (last 10 s)", "tip_stop_title": "Live signal quality (stopped · last update {ts})", "tip_lvl_rule": "≥-28 green · -33~-28 yellow · <-33 red",
        "tip_snr_rule": "≥18 green · 12~18 yellow · <12 red · ref. BW 180 Hz", "tip_sig_rule": "level > -60 dBFS and tonality ≥ 15 (last 2 s)", "tip_live_foot": "Updated once per second, ~3 s smoothing; colour needs 2 s confirmation; frozen after stop.",
        "tip_stop_foot": "Values above are the last measurement; cleared on reset.", "tip_file_title": "Signal quality (details)", "tip_ratio_rule": "0.80~1.35 normal · >1.55 reverb stretch · <0.62 chopped",
        "tip_decode_rule": "text confidence + element regularity", "tip_reg": "share of elements on the 1×/3× grid", "tip_textline": "? {q:.1f}% · single-letter {single:.1f}% · non-ASCII {nonascii:.1f}%",
        "tip_no_data": "— (no data)", "q_reg": "Reg", "q_text": "Text"
    },
    "ru": {
        "title": "CW декодер", "open": "Открыть WAV", "close_file": "Закрыть",
        "decode": "Декодировать", "save": "Сохранить текст", "clear": "Очистить",
        "cyr": "Кириллица", "latin": "Латиница", "gate": "Шлюз (файл/CTC)",
        "lang": "Язык", "backend": "Движок", "src": "Источник",
        "tone": "Тон", "wpm": "Скорость", "dur": "Длительность",
        "elapsed": "Время", "none": "—", "no_file": "Сначала выберите файл WAV",
        "error": "Ошибка", "hint": "Щёлкните по спектрограмме или введите частоту и нажмите Enter", "spectrum": "Спектрограмма (X: время, с; Y: частота, Гц)",
        "selected": "выбрано", "go": "Декодировать", "hz": "Гц",
        "bad_freq": "Частота должна быть в диапазоне 100–3000 Гц", "decoding": "Декодирование…", "auto": "Авто",
        "cmp": "Сравнить", "be_dsp": "DSP (правила)", "be_ctc": "CTC (нейросеть)",
        "be_auto": "Авто (оба)", "play": "▶ Пуск", "pause": "⏸ Пауза",
        "stop": "⏹ Стоп", "reset": "Сброс", "ch_only": "Только выбранный канал",
        "bw": "Полоса", "vol": "Громк.", "band": "Полоса воспр.",
        "gate_hint": "Шлюз: только файл + CTC (помечает сомнительные символы)", "fix_hint": "Исправления: работают в файловом и живом режиме", "live": "Живой вход",
        "cap_start": "Слушать", "cap_stop": "Стоп", "cap_save": "Сохранить WAV (≤120 с)",
        "cap_saved": "Сохранено", "cap_running": "приём", "live_dec": "Живое декодирование",
        "live_be": "Движок", "fix": "Исправления (все)", "multi": "Мультиканал",
        "live_click_hint": "Живой режим: выберите канал в списке или введите Гц", "follow": "Следить за частотой", "fx_hint": "Подсказка: улучшения звука Windows сжимают динамический диапазон — отключите их",
        "cap_idle": "не слушает", "cap_none": "нет доступного устройства ввода", "stop_hint": "Приём остановлен · текст сохранён (сохраните запись и декодируйте в файловом режиме)",
        "cap_stopped": "Приём остановлен · запись хранит последние {sec} с", "dsp_desc": "Правила: порог огибающей + длительности точек и тире", "ctc_desc": "Нейросеть: 11 акустических признаков + CTC",
        "cmp_title": "Сравнение движков", "agree": "Согласие", "snr_high": "высокий",
        "snr_good": "хороший", "snr_low": "низкий", "snr_vlow": "очень низкий",
        "cand": "Каналы", "cand_tip": "качество", "txtcap": "Результат",
        "alpha": "Алфавит", "alpha_la": "Латиница", "alpha_ru": "Кириллица",
        "empty_file": "Откройте WAV и нажмите «Декодировать» или «Слушать»", "empty_live": "Ожидание сигнала… (текст появится после захвата тона)", "txpad": "Передача",
        "menu_tools": "Инструменты", "menu_file": "Файл", "txpad_title": "Тренажёр передачи CW",
        "txpad_template": "Шаблон", "txpad_call": "Позывной", "txpad_text": "Текст для передачи",
        "txpad_tone": "Тон (Гц)", "txpad_wpm": "Скорость", "txpad_play": "▶ Пуск",
        "txpad_stop": "⏹ Стоп", "txpad_save": "💾 Сохранить WAV", "txpad_close": "Закрыть",
        "txpad_empty": "Сначала введите текст", "txpad_saved": "Сохранено: {path}", "txpad_info": "{chars} симв. · {wpm} WPM · {tone} Гц · около {dur} с",
        "tpl_cq": "CQ CQ CQ DE {call}", "tpl_cq_test": "CQ TEST", "tpl_rst": "Отчёт RST",
        "tpl_73": "TU 73 SK", "tpl_qrl": "QRL? (частота занята?)", "tpl_qrz": "QRZ? (кто меня зовёт?)",
        "tpl_info": "ИМЯ / QTH", "tpl_pse_k": "PSE K (продолжайте)", "col_freq": "Частота, Гц",
        "col_dbfs": "Уровень, дБ", "col_wpm": "Скорость", "col_call": "Позывной / фрагмент",
        "q_signal": "Сигнал", "q_level": "Уровень", "q_snr": "SNR",
        "q_ratio": "Тайминг", "q_decode": "Декод", "q_hold": "(удерж.)",
        "q_stopped": "— (остановлено)", "q_none": "нет", "q_yes": "есть",
        "q_fix": "испр.", "q_lvl_low": "⚠Уровень", "q_lvl": "Уровень",
        "q_good": "достоверно", "q_suspect": "сомнительно", "q_bad": "недостоверно",
        "tip_live_title": "Качество сигнала (последние 10 с)", "tip_stop_title": "Качество сигнала (остановлено · обновлено {ts})", "tip_lvl_rule": "≥-28 зелёный · -33~-28 жёлтый · <-33 красный",
        "tip_snr_rule": "≥18 зелёный · 12~18 жёлтый · <12 красный · полоса 180 Гц", "tip_sig_rule": "уровень > -60 dBFS и тональность ≥ 15 (последние 2 с)", "tip_live_foot": "Обновление раз в секунду, сглаживание ~3 с; цвет — после 2 с подтверждения; после остановки значения фиксируются.",
        "tip_stop_foot": "Выше — последние значения; сбрасываются кнопкой «Сброс».", "tip_file_title": "Качество сигнала (подробно)", "tip_ratio_rule": "0.80~1.35 норма · >1.55 растяжение · <0.62 дробление",
        "tip_decode_rule": "достоверность текста + регулярность элементов", "tip_reg": "доля элементов на сетке 1×/3×", "tip_textline": "? {q:.1f}% · одиночных {single:.1f}% · не-ASCII {nonascii:.1f}%",
        "tip_no_data": "— (нет данных)", "q_reg": "Регул.", "q_text": "Текст"
    },
}

F_MIN, F_MAX = 200.0, 1600.0
MARGIN_L, MARGIN_B, MARGIN_T, MARGIN_R = 52, 26, 10, 10


def _lut() -> np.ndarray:
    stops = np.array([[0, 0, 0], [16, 32, 96], [32, 140, 190], [250, 220, 60], [255, 255, 255]], float)
    pos = np.linspace(0, 1, len(stops))
    x = np.linspace(0, 1, 256)
    return np.stack([np.interp(x, pos, stops[:, i]) for i in range(3)], axis=1).astype(np.uint8)



def cw_regularity(audio: np.ndarray, f0: float) -> float:
    """How regular the keying is at this frequency (0..1).

    Real CW has marks and gaps that are near-integer multiples of one unit;
    noise-driven "channels" do not. Used to rank candidates honestly.
    """
    y = dsp.bandpass(audio.astype(np.float32), 8000, f0, 180.0)
    env = dsp.envelope(y, 8000)
    mask = dsp.binarize(env)
    runs = dsp.clean_runs(dsp._runs(mask), 8000)
    marks = [ln / 8.0 for st, ln in runs if st]
    gaps = [ln / 8.0 for st, ln in runs if not st]
    if len(marks) < 8 or len(gaps) < 4:
        return 0.0
    unit = dsp.estimate_unit(marks)
    if not unit:
        return 0.0

    def fit(v):
        r = np.abs(np.asarray(v) / unit - np.round(np.asarray(v) / unit))
        return float(np.mean(r < 0.25))

    return 0.6 * fit(marks) + 0.4 * fit(gaps)


def detect_candidates(spec: np.ndarray, freqs: np.ndarray, audio: np.ndarray | None = None,
                      max_n: int = 4, min_sep_hz: float = 70.0,
                      min_depth_db: float = 4.0, above_floor_db: float = 10.0,
                      min_score: float = 0.45):
    """Find candidate channels and rank them by keying regularity.

    1) physical filter: keyed (modulation depth) and above the noise floor;
    2) rank by how regular the keying is at that frequency;
    3) drop weak candidates unless nothing better exists.
    """
    p90 = np.percentile(spec, 90, axis=1)
    p50 = np.percentile(spec, 50, axis=1)
    depth = 20.0 * np.log10((p90 + 1e-9) / (p50 + 1e-9))
    level = 20.0 * np.log10(p90 + 1e-9)
    floor = float(np.median(level))

    peaks = []
    for i in range(3, len(freqs) - 3):
        if not (F_MIN <= freqs[i] <= F_MAX):     # 只认界面上显示的范围，避免把工频/低频隆隆当频道
            continue
        if depth[i] < min_depth_db or level[i] < floor + above_floor_db:
            continue
        if level[i] >= level[i - 1] and level[i] >= level[i + 1]:
            peaks.append((float(freqs[i]), float(depth[i]), float(level[i])))

    peaks.sort(key=lambda c: -(c[2] + c[1]))
    picked = []
    for f, d, lv in peaks:
        if all(abs(f - o[0]) >= min_sep_hz for o in picked):
            picked.append((f, d, lv))
        if len(picked) >= 8:
            break

    # 单独再取"最强电平"的前几个：持续载波（调制深度≈0）会被上面的深度过滤掉，
    # 但它常常就是真实信号（例如信标前的长载波），必须给它一个位置。
    strong = []
    for i in range(3, len(freqs) - 3):
        if not (F_MIN <= freqs[i] <= F_MAX):
            continue
        if level[i] < floor + 10.0:
            continue
        if level[i] >= level[i - 1] and level[i] >= level[i + 1]:
            strong.append((float(freqs[i]), float(depth[i]), float(level[i])))
    strong.sort(key=lambda c: -c[2])
    for f, d, lv in strong[:3]:
        if all(abs(f - o[0]) >= min_sep_hz for o in picked):
            picked.append((f, d, lv))

    if audio is None or not picked:
        return picked[:max_n]

    scored = [(f, d, lv, cw_regularity(audio, f)) for f, d, lv in picked]
    good = [c for c in scored if c[3] >= min_score]
    if not good:
        good = [max(scored, key=lambda c: c[3])]
    good.sort(key=lambda c: (-c[3], -c[2]))
    out = good[:max_n]

    # 保底：把"最强电平"的那个频道也放进来（往往是真实信号）
    if strong:
        top_level = strong[0][0]
        if all(abs(top_level - c[0]) > 40 for c in out):
            best = max(scored, key=lambda c: c[2])
            out = out[: max_n - 1] + [best]

    return [(f, d, lv) for f, d, lv, _ in out]



class VerticalSlider(tk.Canvas):
    """A slim, dark-themed vertical slider drawn on a canvas (no ttk chrome)."""

    def __init__(self, master, width=16, minimum=50.0, maximum=3500.0, command=None, **kw):
        kw.setdefault("bg", "#0f1216")
        kw.setdefault("highlightthickness", 0)
        super().__init__(master, width=width, **kw)
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.value = (self.minimum + self.maximum) / 2.0
        self.command = command
        self.enabled = True
        self.span = None            # 当前可见跨度（None=整段）
        self._drag = False
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._redraw())

    # ---- geometry
    def _h(self):
        return max(20, self.winfo_height())

    def _val_to_y(self, v):
        f = (v - self.minimum) / max(1e-9, self.maximum - self.minimum)
        return (self._h() - 2) - f * (self._h() - 4)

    def _y_to_val(self, y):
        f = ((self._h() - 2) - y) / max(1, self._h() - 4)
        f = min(1.0, max(0.0, f))
        return self.minimum + f * (self.maximum - self.minimum)

    # ---- api
    def set(self, v, notify=False):
        self.value = max(self.minimum, min(self.maximum, float(v)))
        self._redraw()
        if notify and self.command:
            self.command(self.value)

    def get(self):
        return self.value

    def set_range(self, lo: float, hi: float):
        if hi - lo < 1.0:
            return
        self.minimum, self.maximum = float(lo), float(hi)
        self.value = max(self.minimum, min(self.maximum, self.value))
        self._redraw()

    def set_enabled(self, on: bool):
        self.enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self._redraw()

    # ---- drawing
    def _redraw(self):
        self.delete("all")
        w, h = self.winfo_width() or 16, self._h()
        cx = w / 2
        track = "#243040" if self.enabled else "#1d2632"
        self.create_line(cx, 2, cx, h - 2, fill=track, width=6)
        y = self._val_to_y(self.value)
        fill = "#8fa6bf" if self.enabled else "#39434f"
        outline = "#c2d4e6" if self.enabled else "#2a3340"
        # 手柄长度 = 可见窗口占全段的比例（最少 12 px，便于抓取）
        total = max(1e-9, self.maximum - self.minimum)
        half = 7.0
        self.create_rectangle(3, max(3, y - half), w - 3, min(h - 3, y + half),
                              fill=fill, outline=outline, width=1)


    # ---- events
    def _press(self, evt):
        if not self.enabled:
            return
        self._drag = True
        self.set(self._y_to_val(evt.y), notify=True)

    def _motion(self, evt):
        if not self.enabled or not self._drag:
            return
        self.set(self._y_to_val(evt.y), notify=True)

    def _release(self, _evt):
        self._drag = False


def _pick_out_dir(base: str | None = None) -> str:
    """选一个可写目录保存解码结果（装在 Program Files 时退回用户目录）。"""
    base = base or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".wtest")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return base
    except Exception:
        alt = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                           "CWDecoder", "out")
        try:
            os.makedirs(alt, exist_ok=True)
        except Exception:
            pass
        return alt



_ERR_LOG_MAX = 256 * 1024      # 日志上限 256 KB，超出自动截断（只留后半段）
_ERR_LOG_LAST: dict[str, float] = {}   # 同一条错误 60 秒内只记一次，避免刷屏


def _ui_err_log(msg: str) -> None:
    """把界面层的异常写进日志，便于排查。

    · 位置：%LOCALAPPDATA%\\CWDecoder\\ui_errors.log（每个用户各自一份）
    · 只记录异常堆栈，不记录音频、文本或任何用户输入
    · 单条错误 60 秒内重复出现只记一次；文件超过 256 KB 自动截断
    · 任何情况下都不会抛错（记录失败就放弃）
    """
    try:
        import os, time
        # 限流键：整条内容（截 200 字符）。同一函数下的不同错误仍会分别记录
        key = (msg or "").strip()[:200] or "?"
        now = time.time()
        if now - _ERR_LOG_LAST.get(key, 0.0) < 60.0:
            return
        _ERR_LOG_LAST[key] = now
        if len(_ERR_LOG_LAST) > 200:
            _ERR_LOG_LAST.clear()

        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "CWDecoder")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "ui_errors.log")
        try:
            if os.path.exists(path) and os.path.getsize(path) > _ERR_LOG_MAX:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    tail = f.read()[-_ERR_LOG_MAX // 2:]
                with open(path, "w", encoding="utf-8") as f:
                    f.write("（日志超过上限，已截断）\n" + tail)
        except Exception:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{msg}\n")
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.lang = "zh"
        self.path: str | None = None
        self.result: dict | None = None
        self.alphabet = "latin"      # latin / ru：真正切换"字母表"，不只是显示
        self.show_cyr = False
        self.show_cyr_var = tk.BooleanVar(value=False)
        self.selected_hz: float | None = None
        self.duration = 0.0
        self._img_ref = None
        self._spec = None
        self._cands: list[tuple[float, float, float]] = []
        self._wf: Waterfall | None = None
        self._wf_mode = False
        self._wf_frozen = False
        self.tracker: ChannelTracker | None = None
        self._mc_last = 0.0
        self._mc_busy = False
        self._mc_q: queue.Queue = queue.Queue()
        self._wf_item = None
        self._wf_job = None
        self._y8k = None
        self.view = {"f0": F_MIN, "f1": F_MAX, "t0": 0.0, "t1": 0.0}
        self._player = Player()
        self._plot = None            # (x0, y0, w, h) of the image area
        self.out_dir = _pick_out_dir()
        self._q: queue.Queue = queue.Queue()
        self._decoding = False
        self._busy_job = None
        self._busy_win = None
        self._busy_start = 0.0

        # 按"工作区"（排除任务栏）来定位，避免窗口底部被任务栏挡住
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            wa_left, wa_top, wa_right, wa_bottom = rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            wa_left, wa_top = 0, 0
            wa_right, wa_bottom = self.winfo_screenwidth(), self.winfo_screenheight()

        wa_w = max(600, wa_right - wa_left)
        wa_h = max(400, wa_bottom - wa_top)
        # 默认尺寸（用于还原时的大小），随后最大化铺满工作区
        w = min(1380, wa_w - 24)
        h = min(790, wa_h - 46)
        x = wa_left + (wa_w - w) // 2
        y = wa_top + max(0, (wa_h - h) // 2 - 10)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(min(1200, wa_w - 20), min(640, wa_h - 40))
        self._work_area = (wa_left, wa_top, wa_w, wa_h)
        # 启动后铺满工作区（最大化，保留任务栏）
        self.after(0, self._maximize_start)

        # 再按"实际窗口底边"校正一次（标题栏/边框会额外占高度）
        try:
            self.update_idletasks()
            bottom = self.winfo_rooty() + self.winfo_height() + 14
            if bottom > wa_bottom:
                y = max(wa_top, y - (bottom - wa_bottom))
                self.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass
        self._build()
        self._update_alpha_label()
        self._update_empty_hint()          # 启动时显示空状态插图
        try:
            self._sync_channel_panels()      # 启动时无文件：隐藏空的频道面板
        except Exception:
            pass

    # ------------------------------------------------------------ helpers
    def t(self, key: str) -> str:
        d = STRINGS.get(self.lang) or STRINGS["en"]
        return d.get(key) or STRINGS["en"].get(key) or key

    def _maximize_start(self):
        """启动时铺满工作区（不覆盖任务栏）。

        优先用 Windows 的 zoomed 状态（系统会自己避开任务栏）；
        若当前 Tk 不支持，则按工作区矩形铺满。
        """
        try:
            self.state("zoomed")
            return
        except Exception:
            pass
        try:
            wa_left, wa_top, wa_w, wa_h = getattr(self, "_work_area", (0, 0, 0, 0))
            if wa_w > 0 and wa_h > 0:
                self.geometry(f"{wa_w}x{wa_h}+{wa_left}+{wa_top}")
        except Exception:
            pass

    def _build(self):
        self.title(self.t("title"))

        menubar = tk.Menu(self)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label=self.t("open"), command=self.on_open)
        m_file.add_command(label=self.t("save"), command=self.on_save)
        m_file.add_separator()
        m_file.add_command(label=self.t("close_file"), command=self.on_close_file)
        menubar.add_cascade(label=self.t("menu_file"), menu=m_file)

        m_tools = tk.Menu(menubar, tearoff=0)
        m_tools.add_command(label=self.t("txpad"), command=self.on_txpad)
        menubar.add_cascade(label=self.t("menu_tools"), menu=m_tools)
        self.config(menu=menubar)

        st = ttk.Style(self)
        try:
            st.configure("TButton", padding=(7, 3))
            st.configure("TCombobox", padding=(3, 2))
            st.configure("Muted.TLabel", foreground="#5f7186")
            st.configure("Strong.TLabel", font=("Segoe UI", 9, "bold"))
            st.configure("Head.TLabel", font=("Segoe UI", 9, "bold"), foreground="#33475c")
            st.configure("TSeparator", background="#d5dde6")
        except Exception:
            pass

        self.configure(background="#f4f7fa")
        top = ttk.Frame(self, padding=(12, 6, 12, 2))
        top.pack(fill="x")

        # 第二行：解码选项（原来挤在工具栏里，会把右侧按钮挤出窗口）
        self.bar2 = ttk.Frame(self, padding=(12, 0, 12, 2))
        self.bar2.pack(fill="x")

        # --- 右侧分组：先把子控件建好，再整体放进工具栏，避免宽度算成 0 ---
        self.tail = ttk.Frame(top)

        self.btn_cyr = ttk.Button(self.tail, text=self.t("cyr"), width=8, command=self.on_cyr)
        self.btn_cyr.pack(side="left", padx=(0, 6))
        self.btn_tx = ttk.Button(self.tail, text=self.t("txpad"), width=8, command=self.on_txpad)
        self.btn_tx.pack(side="left", padx=(0, 10))

        self.sep_right = ttk.Separator(self.tail, orient="vertical")
        self.sep_right.pack(side="left", fill="y", padx=(0, 10))

        self.lang_var = tk.StringVar(value="中文")
        self.lbl_lang = ttk.Label(self.tail, text=self.t("lang"))
        self.lbl_lang.pack(side="left", padx=(0, 6))
        self.cmb_lang = ttk.Combobox(self.tail, textvariable=self.lang_var, width=6,
                                     values=["中文", "English", "Русский"], state="readonly")
        self.cmb_lang.pack(side="left")
        self.cmb_lang.bind("<<ComboboxSelected>>", self.on_lang)

        self.tail.pack(side="right", padx=(14, 0))

        # --- 左侧：文件操作 ---
        self.btn_open = ttk.Button(top, text=self.t("open"), command=self.on_open)
        self.btn_open.pack(side="left")
        self.btn_close_file = ttk.Button(top, text=self.t("close_file"), command=self.on_close_file)
        self.btn_close_file.pack(side="left", padx=(4, 0))
        self.btn_decode = ttk.Button(top, text=self.t("decode"), command=self.on_decode)
        self.btn_decode.pack(side="left", padx=(8, 0))
        self.btn_save = ttk.Button(top, text=self.t("save"), command=self.on_save)
        self.btn_save.pack(side="left", padx=(8, 0))
        self.btn_clear = ttk.Button(top, text=self.t("clear"), command=self.on_clear)
        self.btn_clear.pack(side="left", padx=(8, 0))
        self.btn_cmp = ttk.Button(top, text=self.t("cmp"), command=self.on_compare,
                                  state="disabled")
        self.btn_cmp.pack(side="left", padx=(8, 0))

        self.gate_var = tk.BooleanVar(value=True)
        self.chk_gate = ttk.Checkbutton(self.bar2, text=self.t("gate"), variable=self.gate_var)
        self.fix_var = tk.BooleanVar(value=True)
        self.chk_fix = ttk.Checkbutton(self.bar2, text=self.t("fix"), variable=self.fix_var)
        self.chk_gate.bind("<Enter>", lambda e: self.status.configure(text=self.t("gate_hint")))
        self.chk_gate.bind("<Leave>", lambda e: self.status.configure(text=self.t("hint")))
        self.chk_fix.bind("<Enter>", lambda e: self.status.configure(text=self.t("fix_hint")))
        self.chk_fix.bind("<Leave>", lambda e: self.status.configure(text=self.t("hint")))
        self.chk_gate.pack(side="left")
        self.chk_fix.pack(side="left", padx=(12, 0))

        self.be_var = tk.StringVar(value=self.t("be_dsp"))
        self.lbl_be = ttk.Label(self.bar2, text=self.t("backend"))
        self.lbl_be.pack(side="left", padx=(14, 4))
        self.cmb_be = ttk.Combobox(self.bar2, textvariable=self.be_var, width=16,
                                   values=[self.t("be_dsp"), self.t("be_ctc"), self.t("be_auto")],
                                   state="readonly")
        self.cmb_be.pack(side="left")



        info = ttk.Frame(self, padding=(12, 1, 12, 1))
        info.pack(fill="x")
        self.lbl_src = ttk.Label(info, style="Strong.TLabel",
                                 text=f"{self.t('src')}: {self.t('none')}")
        self.lbl_src.pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=(2, 2))

        # ---- 实时输入 ----
        live = ttk.Frame(self, padding=(12, 1, 12, 1))
        live.pack(fill="x")
        ttk.Label(live, text=self.t("live"), style="Strong.TLabel").pack(side="left", padx=(0, 6))

        self._srcs = CaptureEngine.list_sources()
        self.src_var = tk.StringVar(value=self._srcs[0]["name"] if self._srcs else "")
        self.cmb_src = ttk.Combobox(live, textvariable=self.src_var, width=28, state="readonly",
                                    values=[s2["name"] for s2 in self._srcs])
        self.cmb_src.pack(side="left")

        self.btn_cap_start = ttk.Button(live, text=self.t("cap_start"), command=self.on_cap_start)
        self.btn_cap_start.pack(side="left", padx=(6, 0))
        self.btn_cap_stop = ttk.Button(live, text=self.t("cap_stop"), command=self.on_cap_stop,
                                       state="disabled")
        self.btn_cap_stop.pack(side="left", padx=(4, 0))
        self.btn_cap_save = ttk.Button(live, text=self.t("cap_save"), command=self.on_cap_save,
                                       state="disabled")
        self.btn_cap_save.pack(side="left", padx=(4, 0))

        ttk.Label(live, text=self.t("live_be")).pack(side="left", padx=(8, 2))
        self.live_be_var = tk.StringVar(value="DSP")
        self.cmb_live_be = ttk.Combobox(live, textvariable=self.live_be_var, width=7,
                                        values=["DSP", "CTC"], state="readonly")
        self.cmb_live_be.pack(side="left")

        self.mc_var = tk.BooleanVar(value=False)
        self.chk_mc = ttk.Checkbutton(live, text=self.t("multi"), variable=self.mc_var,
                                      command=self._on_mc_toggle)
        self.chk_mc.pack(side="left", padx=(8, 0))

        self.follow_var = tk.BooleanVar(value=False)
        self.chk_follow = ttk.Checkbutton(live, text=self.t("follow"), variable=self.follow_var)
        self.chk_follow.pack(side="left", padx=(8, 0))

        self.live_var = tk.BooleanVar(value=True)
        self.chk_live = ttk.Checkbutton(live, text=self.t("live_dec"), variable=self.live_var)
        self.chk_live.pack(side="left", padx=(4, 0))


        self.meter = tk.Canvas(live, width=150, height=16, bg="#101418",
                               highlightthickness=1, highlightbackground="#33404f")
        self.meter.pack(side="left", padx=(10, 6))
        self.lbl_cap = ttk.Label(live, text=self.t("cap_idle"), font=("Consolas", 9))
        self.lbl_cap.pack(side="left")

        self.capture = CaptureEngine()
        self.stream_dec: StreamDecoder | None = None
        self._live_q: queue.Queue = queue.Queue()
        self._evt_q: queue.Queue = queue.Queue()
        self._live_raw = ""
        self._live_tone_done = False
        self._tone_manual = False
        self._live_last_retune = 0.0
        self._tone_hist: list[float] = []
        self._last_len = 0
        self._last_grow = time.time()
        self._live_prefix = ""
        self._cand_tone = 0.0
        self._cand_n = 0

        # ---- 频谱 ----
        spec_frame = ttk.Frame(self, padding=(12, 4, 12, 2))
        spec_frame.pack(fill="x")
        head = ttk.Frame(spec_frame)
        head.pack(fill="x")
        self.btn_reset = ttk.Button(head, text=self.t("reset"), width=6, command=self.reset_view)
        self.btn_reset.pack(side="left")
        self.lbl_spec = ttk.Label(head, style="Muted.TLabel", text=self.t("spectrum"))
        self.lbl_spec.pack(side="left", padx=(10, 0))
        self.ent_hz = ttk.Entry(head, width=7)
        self.ent_hz.pack(side="right")
        self.ent_hz.bind("<Return>", self.on_manual_freq)
        self.lbl_hz = ttk.Label(head, text=self.t("hz"))
        self.lbl_hz.pack(side="right", padx=(0, 4))
        row = tk.Frame(spec_frame, background="#0f1216")
        row.pack(fill="x", pady=(4, 0))
        self.vs = VerticalSlider(row, width=18, minimum=50, maximum=3500,
                                 command=self.on_vscroll)
        self.vs.place(x=0, y=MARGIN_T, width=18, height=200)
        self._sync_guard = False
        # 频谱容器：高度由 _balance_split() 自动配平到与文本框等高
        holder = tk.Frame(row, height=260, background="#0f1216")
        holder.pack(side="left", fill="x", expand=True, anchor="n", padx=(18, 0))
        holder.pack_propagate(False)
        self.canvas = tk.Canvas(holder, background="#0f1216",
                                highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self.on_wheel)

        # 时间轴（与频谱宽度严格一致：同样的左右边距）
        self.timeline = tk.Canvas(spec_frame, height=26, background="#0f1216",
                                  highlightthickness=0)
        self.timeline.pack(fill="x")
        self.timeline.bind("<Button-1>", self.on_timeline_click)

        play_bar = ttk.Frame(spec_frame)
        play_bar.pack(fill="x", pady=(4, 0))
        self.btn_play = ttk.Button(play_bar, text=self.t("play"), width=9, command=self.on_play_toggle)
        self.btn_play.pack(side="left")
        self.btn_stop = ttk.Button(play_bar, text=self.t("stop"), width=9, command=self.on_stop)
        self.btn_stop.pack(side="left", padx=(4, 0))
        self.lbl_time = ttk.Label(play_bar, text="0.0 / 0.0 s", width=16,
                                  font=("Consolas", 9))
        self.lbl_time.pack(side="left", padx=(10, 0))

        self.ch_var = tk.BooleanVar(value=False)
        self.chk_ch = ttk.Checkbutton(play_bar, text=self.t("ch_only"), variable=self.ch_var,
                                      command=self._refresh_play_audio)
        self.chk_ch.pack(side="left", padx=(12, 0))
        ttk.Label(play_bar, text=self.t("bw")).pack(side="left", padx=(8, 2))
        self.bw_var = tk.StringVar(value="250")
        self.cmb_bw = ttk.Combobox(play_bar, textvariable=self.bw_var, width=5,
                                   values=["100", "250", "500"], state="readonly")
        self.cmb_bw.pack(side="left")
        self.cmb_bw.bind("<<ComboboxSelected>>", self._on_bw_change)
        ttk.Label(play_bar, text="Hz").pack(side="left", padx=(2, 0))

        ttk.Label(play_bar, text=self.t("vol")).pack(side="left", padx=(12, 4))
        self.vol_var = tk.DoubleVar(value=70)
        self.scl_vol = ttk.Scale(play_bar, from_=0, to=100, length=110,
                                 variable=self.vol_var, command=self._on_volume)
        self.scl_vol.pack(side="left")
        self.lbl_vol = ttk.Label(play_bar, text="70%", width=5, font=("Consolas", 9))
        self.lbl_vol.pack(side="left", padx=(4, 0))

        self.mc_frame = ttk.Frame(spec_frame)
        self.mc_tree = ttk.Treeview(self.mc_frame,
                                    columns=("freq", "lvl", "wpm", "call"),
                                    show="headings", height=3)
        self.mc_tree.heading("freq", text=self.t("col_freq"))
        self.mc_tree.heading("freq", text=self.t("col_freq"))
        self.mc_tree.heading("lvl", text=self.t("col_dbfs"))
        self.mc_tree.heading("wpm", text=self.t("col_wpm"))
        self.mc_tree.heading("call", text=self.t("col_call"))
        self.mc_tree.column("freq", width=90, anchor="w")
        self.mc_tree.column("lvl", width=70, anchor="w")
        self.mc_tree.column("wpm", width=60, anchor="w")
        self.mc_tree.column("call", width=360, anchor="w")
        self.mc_sb = ttk.Scrollbar(self.mc_frame, orient="vertical",
                                   command=self.mc_tree.yview)
        self.mc_tree.configure(yscrollcommand=self.mc_sb.set)
        self.mc_sb.pack(side="right", fill="y")
        self.mc_tree.pack(side="left", fill="x", expand=True)
        self.mc_tree.bind("<ButtonRelease-1>", self._mc_pick)
        self._mc_shown = False

        # 频道面板：浅灰底 + 边框，和下面的"解码结果"明确分开
        self.cand_panel = tk.Frame(spec_frame, background="#eef2f6",
                                   highlightthickness=1, highlightbackground="#dbe4ee")
        self.cand_panel.pack(fill="x", pady=(6, 8))
        self.cand_bar = tk.Frame(self.cand_panel, background="#eef2f6")
        self.cand_bar.pack(fill="x", padx=8, pady=5)
        self.lbl_cand = tk.Label(self.cand_bar, text=self.t("cand"), background="#eef2f6",
                                 foreground="#4b5b6b", font=("Segoe UI", 9))
        self.lbl_cand.pack(side="left", padx=(0, 8))

        # ---- 文本 ----
        mid = ttk.Frame(self, padding=(12, 4, 12, 4))
        mid.pack(fill="both", expand=True)
        self.txt = tk.Text(mid, wrap="word", font=("Consolas", 13),
                           width=1, height=1, state="disabled",
                           padx=10, pady=8, spacing1=2, spacing3=4,
                           relief="flat", borderwidth=0,
                           background="#fbfcfe", highlightthickness=1,
                           highlightbackground="#d5dde6", highlightcolor="#d5dde6")
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("sus", foreground="#ff5252")

        # 频谱与文本框等高：窗口变化或加载文件后自动配平
        self._balance_job = None
        self._balancing = False
        self._spec_holder = holder
        self.bind("<Configure>", self._schedule_balance, add="+")
        # 载入文件后"候选频道"面板出现，也会改变可用高度 → 同样触发配平
        spec_frame.bind("<Configure>", self._schedule_balance, add="+")
        mid.bind("<Configure>", self._schedule_balance, add="+")

        # 停止监听后的说明条（在文本框下沿，不写入正文）
        self.lbl_stop_hint = tk.Label(mid,
                                      text=self.t("stop_hint"),
                                      background="#f4f7fb", foreground="#6b7c8f",
                                      font=("Segoe UI", 9), anchor="w",
                                      padx=10, pady=4)

        # 空状态：一张淡化的插图 + 一行提示（有内容时自动隐藏）
        self.lbl_empty_img = tk.Label(self.txt, background="#fbfcfe", bd=0)
        self._empty_img_cache = {}
        self._empty_img_job = None
        self.txt.bind("<Configure>", self._on_txt_resize, add="+")

        # 空状态提示（浮在文本框中央，不写入正文，避免污染内容）
        self.lbl_empty = tk.Label(self.txt, text=self.t("empty_file"),
                                  background="#fbfcfe", foreground="#94a3b3",
                                  font=("Segoe UI", 10))
        self.lbl_empty.place(relx=0.5, rely=0.45, anchor="center")

        bar = tk.Frame(self, relief="groove", borderwidth=1, background="#f0f0f0")
        bar.pack(side="bottom", fill="x")
        self.status = tk.Label(bar, text=self.t("hint"), anchor="w", padx=12, pady=7,
                               background="#f0f0f0", foreground="#222222")
        self.status.pack(side="left")

        # 右侧统一容器：质量指示灯 / suspect / 字母表 依次排列，
        # 避免它们各自 side="right" 时互相挤压或重叠
        self.right_bar = tk.Frame(bar, background="#f0f0f0")
        self.right_bar.pack(side="right", padx=(0, 6))

        # 信号质量指示灯：4 个独立 Label，各自着色（绿/黄/红）
        self.quality_frame = tk.Frame(self.right_bar, background="#f0f0f0")
        self.q_labels = {}
        for key in ("signal", "level", "snr", "ratio", "decode"):
            lb = tk.Label(self.quality_frame, text="", background="#f0f0f0",
                          foreground="#8a9aab", font=("Consolas", 9))
            lb.pack(side="left", padx=(0, 8))
            self.q_labels[key] = lb
        # "（保持）"小标记：只在停止监听后显示，贴在"信号"灯后面
        self.lbl_hold = tk.Label(self.quality_frame, text=self.t("q_hold"), background="#f0f0f0",
                                 foreground="#8a9aab", font=("Microsoft YaHei", 7))
        self._q_tip = None
        for lb in self.q_labels.values():
            lb.bind("<Enter>", self._show_quality_tip)
            lb.bind("<Leave>", self._hide_quality_tip)

        self.lbl_suspect = tk.Label(self.right_bar, text="", anchor="e", padx=8, pady=6,
                                    background="#f0f0f0", foreground="#cc0000",
                                    font=("Segoe UI", 9, "bold"))
        self.lbl_suspect.pack(side="left")

        self.lbl_alpha = tk.Label(self.right_bar, text="", anchor="e", padx=8, pady=6,
                                  background="#f0f0f0", foreground="#5f7186",
                                  font=("Segoe UI", 9))
        self.lbl_alpha.pack(side="left", padx=(0, 4))

        self._play_enabled = False
        self._set_play_enabled(False)
        self._jobs: list[str] = []
        self.protocol("WM_DELETE_WINDOW", self._on_close)



    def _on_close(self):
        # 关闭前取消所有排程的 after 任务，避免销毁后回调报 "invalid command name"
        for job in ("_wf_job", "_live_job", "_tick_job",
                    "_balance_job", "_empty_img_job", "_busy_job"):
            j = getattr(self, job, None)
            if j:
                try:
                    self.after_cancel(j)
                except Exception:
                    pass
        try:
            self.capture.stop()
        except Exception:
            pass
        try:
            if self.stream_dec is not None:
                self.stream_dec.stop()
        except Exception:
            pass
        self.destroy()

    def _schedule_balance(self, _evt=None):
        """窗口/面板变化后，延迟配平一次（避免拖动时反复计算）。"""
        if getattr(self, "_balancing", False):
            return
        if self._balance_job is not None:
            try:
                self.after_cancel(self._balance_job)
            except Exception:
                pass
        self._balance_job = self.after(80, self._balance_split)

    def _balance_split(self):
        """让频谱画布与文本框高度尽量相等（误差 4 px 内）。"""
        self._balance_job = None
        if getattr(self, "_balancing", False):
            return
        self._balancing = True
        try:
            for _ in range(4):
                self.update_idletasks()
                ch = self.canvas.winfo_height()
                th = self.txt.winfo_height()
                if ch <= 1 or th <= 1:
                    break
                diff = ch - th
                if abs(diff) <= 4:
                    break
                h = self._spec_holder.winfo_height() - diff / 2
                h = max(120, min(900, h))
                self._spec_holder.configure(height=h)
        except Exception:
            pass
        finally:
            self._balancing = False

    def _on_canvas_configure(self, _evt=None):
        # 竖向滑块的高度跟随画布（窗口变大时一起变长）
        try:
            h = max(60, self.canvas.winfo_height() - MARGIN_T - MARGIN_B)
            self.vs.place_configure(y=MARGIN_T, width=18, height=h)
        except Exception:
            pass
        if getattr(self, "_wf_mode", False) and self._wf is not None:
            cw = max(200, self.canvas.winfo_width())
            ch = max(120, self.canvas.winfo_height())
            pw = max(50, cw - MARGIN_L - MARGIN_R)
            ph = max(50, ch - MARGIN_T - MARGIN_B)
            self._plot = (MARGIN_L, MARGIN_T, pw, ph)
            self._spec_h = ph
            self._wf.resize(pw, ph)
            if self._wf_item is not None:
                try:
                    self._wf_img = ImageTk.PhotoImage(self._wf.to_image())
                    self.canvas.itemconfig(self._wf_item, image=self._wf_img)
                except Exception:
                    pass
            self._wf_draw_axes()
            return
        self._draw_spectrum()

    # ------------------------------------------------------------ waterfall
    def _wf_start(self):
        """切到瀑布模式（清空画布，重画坐标）。"""
        if not _HAS_PIL:
            return
        self._wf_mode = True
        self._wf_frozen = False
        # 实时模式视图固定为全频段，滑块置灰（避免"能拖但无效"的假按钮）
        self.view["f0"], self.view["f1"] = F_MIN, F_MAX
        try:
            self.vs.set((F_MIN + F_MAX) / 2.0)
            self.vs.set_enabled(False)
        except Exception:
            pass
        x0, y0 = MARGIN_L, MARGIN_T
        cw = max(200, self.canvas.winfo_width())
        ch = max(120, self.canvas.winfo_height())
        pw = max(50, cw - MARGIN_L - MARGIN_R)
        ph = max(50, ch - MARGIN_T - MARGIN_B)
        self._plot = (x0, y0, pw, ph)
        self._spec_h = ph
        self._wf = Waterfall(pw, ph, self.view["f0"], self.view["f1"],
                             self.capture.rate or 48000)
        self._wf.clear()

        self.canvas.delete("all")
        self._wf_item = self.canvas.create_image(x0, y0, anchor="nw")
        self._wf_draw_axes()
        self._draw_marker()
        self._draw_band()
        self._wf_tick()

    def _wf_draw_axes(self):
        x0, y0, pw, ph = self._plot
        self.canvas.delete("axis")
        fspan = self.view["f1"] - self.view["f0"]
        step = self._nice_step(fspan / 6.0)
        f = np.floor(self.view["f0"] / step) * step
        while f <= self.view["f1"] + 1e-9:
            if f >= self.view["f0"]:
                y = self._freq_to_y(float(f))
                self.canvas.create_line(x0 - 4, y, x0, y, fill="#8fa3bf", tags="axis")
                self.canvas.create_text(x0 - 6, y, anchor="e", fill="#9fb3c8",
                                        text=f"{f:g}", font=("Consolas", 9), tags="axis")
            f += step
        self.canvas.create_text(8, y0 - 6, anchor="w", fill="#6f8499", text="Hz",
                                font=("Consolas", 8), tags="axis")
        # 时间轴按瀑布实际覆盖的时长标注（每列 ~64 ms，宽度决定跨度）
        try:
            from .waterfall import HOP, SR as _WSR
            span = self._wf.width * HOP / float(_WSR) if self._wf is not None else 30.0
        except Exception:
            span = 30.0
        ticks = ((0.0, f"-{span:.0f}"), (0.5, f"-{span/2:.0f}"), (1.0, "0"))
        ch = self.canvas.winfo_height()
        y_tick = min(y0 + ph + 8, ch - 14)          # 防止数字被画布裁掉
        for frac, label in ticks:
            x = x0 + frac * pw
            self.canvas.create_line(x, y0 + ph, x, y0 + ph + 4, fill="#8fa3bf", tags="axis")
            # 最后一个刻度右对齐，给单位" s"留出位置，避免压字
            self.canvas.create_text(x, y_tick, anchor="e" if frac >= 1.0 else "n",
                                    fill="#9fb3c8", text=label,
                                    font=("Consolas", 9), tags="axis")
        self.canvas.create_text(x0 + pw + 2, y_tick, anchor="nw", fill="#6f8499",
                                text="s", font=("Consolas", 8), tags="axis")
        self._align_vslider()

    def _wf_tick(self):
        if not getattr(self, "_wf_mode", False) or self._wf is None:
            return
        if self._wf.frames:
            try:
                self._wf_img = ImageTk.PhotoImage(self._wf.to_image())
                self.canvas.itemconfig(self._wf_item, image=self._wf_img)
            except Exception:
                pass
        self._wf_job = self.after(66, self._wf_tick)



    def on_close_file(self):
        self._clear_quality()
        """关闭当前文件，恢复到"什么都没打开"的空白状态。"""
        try:
            self._player.stop()
        except Exception:
            pass
        self.path = None
        self._y8k = None
        self._spec = None
        self._sync_channel_panels()
        self._cands = []
        self.selected_hz = None
        self._tone_manual = False
        self._set_text("")
        for w in list(self.cand_bar.winfo_children()):
            if w is not self.lbl_cand:
                w.destroy()
        self.ent_hz.delete(0, "end")
        self._wf_mode = False
        self._wf_frozen = False
        self._wf = None
        self._wf_item = None
        try:
            self.vs.set_enabled(True)
        except Exception:
            pass
        self.canvas.delete("all")
        self._plot = None
        self.lbl_src.configure(text=f"{self.t('src')}: {self.t('none')}")
        try:
            self.lbl_time.configure(text="0.0 / 0.0 s")     # 关闭文件后时间轴归零
        except Exception:
            pass
        self._set_play_enabled(False)
        self.btn_save.configure(state="disabled")
        self.btn_cmp.configure(state="disabled")
        self.status.configure(text=self.t("hint"))
        self.lbl_suspect.configure(text="")

    # ------------------------------------------------------------ multi-channel
    def _sync_channel_panels(self):
        """文件模式和实时模式各用一套频道面板，避免同时占地方。"""
        live_mode = bool(self.capture.running)
        # 文件模式的候选频道面板：只在有文件时显示
        try:
            if self.path and not live_mode:
                self.cand_panel.pack(fill="x", pady=(4, 6))
            else:
                self.cand_panel.pack_forget()
        except Exception:
            pass
        # 实时的多频道表格：只在监听且勾选时显示
        try:
            if self.mc_var.get() and live_mode:
                self.mc_frame.pack(fill="x", pady=(2, 4), before=self.cand_panel
                                   if self.cand_panel.winfo_manager() else None)
                self._mc_shown = True
            else:
                self.mc_frame.pack_forget()
                self._mc_shown = False
        except Exception:
            pass

    def _on_mc_toggle(self):
        if self.mc_var.get() and self.capture.running and self.tracker is None:
            self.tracker = ChannelTracker()          # 监听中途打开也能用
        self._sync_channel_panels()

    def _mc_update(self):
        """在后台线程里算，算完再回主线程刷新列表（避免卡 UI）。"""
        if self.tracker is None or getattr(self, "_mc_busy", False):
            return
        buf = self.capture.snapshot8k()
        if len(buf) < 2 * 8000:
            return
        self._mc_busy = True
        buf2 = buf[-2 * 8000:]
        seg = buf[-12 * 8000:] if len(buf) > 12 * 8000 else buf
        tracker = self.tracker

        def work():
            try:
                chs = tracker.update(buf2)
                rows = [c for c in chs if c.confirmed][:6]
                out = []
                from .correct import PROWORDS, CALL_RE
                for c in rows:
                    wpm_txt, call_txt = "—", "—"
                    try:
                        r = dsp.decode_samples(seg, tone=c.freq)
                        if r.get("wpm"):
                            wpm_txt = f"{r['wpm']:.0f}"
                        toks = [w for w in (r.get("text") or "").split() if w]
                        calls = [w for w in toks if CALL_RE.match(w) and w not in PROWORDS]
                        if calls:
                            best = max(set(calls), key=calls.count)
                            if calls.count(best) >= 2 or len(best) >= 5:
                                call_txt = best

                    except Exception:
                        pass
                    if call_txt != "—" and "?" not in call_txt:
                        c.last_call = call_txt          # 记住，避免列表闪烁成 —
                    shown = c.last_call or "—"
                    out.append((f"{c.freq:.0f}", f"{c.level:.1f}", wpm_txt, shown))
                self._mc_q.put(out)
            except Exception:
                pass
            finally:
                self._mc_busy = False
        self._mc_q: queue.Queue = queue.Queue()

        threading.Thread(target=work, daemon=True).start()

    def _mc_render(self, rows):
        if self.tracker is None:
            return
        for item in self.mc_tree.get_children():
            self.mc_tree.delete(item)
        for v in rows:
            vals = list(v)
            if vals[2] != "—" and float(vals[2]) > 55:
                vals[2], vals[3] = "—", "—"
            self.mc_tree.insert("", "end", values=tuple(vals))
        # 同一呼号出现在多个频率 → 只留最强的
        seen = set()
        for item in self.mc_tree.get_children():
            v = list(self.mc_tree.item(item)["values"])
            call = str(v[3])
            if call in ("—", ""):
                continue
            if call in seen:
                v[3] = "—"
                self.mc_tree.item(item, values=v)
            else:
                seen.add(call)
        self._draw_mc_marks()

    def _mc_pick(self, _evt=None):
        sel = self.mc_tree.selection()
        if not sel:
            return
        vals = self.mc_tree.item(sel[0])["values"]
        try:
            f = float(vals[0])
        except Exception:
            return
        self.selected_hz = f
        self._tone_manual = True
        self.ent_hz.delete(0, "end")
        self.ent_hz.insert(0, f"{f:.1f}")
        if self.stream_dec is not None:
            self.stream_dec.set_tone(f)
        self._draw_marker()
        self.status.configure(text=f"{self.t('selected')}: {f:.0f} Hz")

    def _draw_mc_marks(self):
        if not self._plot or self.tracker is None:
            return
        self.canvas.delete("mc")
        x0, y0, pw, ph = self._plot
        for c in self.tracker.confirmed()[:8]:
            if not (self.view["f0"] <= c.freq <= self.view["f1"]):
                continue
            y = self._freq_to_y(c.freq)
            self.canvas.create_line(x0, y, x0 + 24, y, fill="#ff9f43", width=2, tags="mc")
            self.canvas.create_text(x0 + 28, y, anchor="w", fill="#ff9f43",
                                    text=f"{c.freq:.0f}", font=("Consolas", 8), tags="mc")

    # ------------------------------------------------------------ capture
    def _src_id(self) -> str | None:
        name = self.src_var.get()
        for s2 in self._srcs:
            if s2["name"] == name:
                return s2["id"]
        return None

    def _live_poll(self):
        """定时器回调：刷新文本 + 自动音调。任何分支都必须重新排程。"""
        self._drain_live()

        try:
            while True:
                f = self._evt_q.get_nowait()
                self.status.configure(text=f"{self.t('tone')}: {f:.1f} Hz")
        except queue.Empty:
            pass

        now = time.time()
        if self.stream_dec is not None and self.capture.rate:
            self.stream_dec.src_rate = int(self.capture.rate)   # 自愈：采样率随时同步

        gap_needed = 2.0 if self.stream_dec is None else 8.0
        buf_now = self.capture.snapshot8k() if self.capture.running else None
        # 短报文：电平可能已经落下去，此时只要缓冲里还有信号就继续估音调
        have_sig = (self.capture.level_db > -45.0) or (
            buf_now is not None and len(buf_now) > 8000 * 1.2
            and dsp.tonality(buf_now[-12000:]) > 35.0)
        if (not self._tone_manual and have_sig
                and (not self._live_tone_done
                     or (self.follow_var.get() and now - self._live_last_retune > gap_needed))):
            buf = self.capture.snapshot8k()
            if len(buf) > 1.2 * 8000:
                seg = buf[-20 * 8000:] if len(buf) > 20 * 8000 else buf
                if len(seg) >= 1.2 * 8000 and dsp.tonality(seg) >= 30.0:
                    try:
                        f0 = dsp.pick_tone_robust(seg)
                    except Exception:
                        f0 = 0.0
                    if 200 < f0 < 1600:
                        if self.stream_dec is None:
                            # 首次锁音调：要求连续两次估计接近，避免被开头的提示音带偏
                            self._tone_hist.append(float(f0))
                            self._tone_hist = self._tone_hist[-3:]
                            if (len(self._tone_hist) >= 2
                                    and abs(self._tone_hist[-1] - self._tone_hist[-2]) <= 10.0):
                                tone = sum(self._tone_hist[-2:]) / 2.0
                                self.selected_hz = round(float(tone), 1)
                                self._tone_manual = False
                                self.stream_dec = WindowDecoder(
                                    f0=self.selected_hz,
                                    on_update=lambda b, a: self._live_q.put((b, a)),
                                    on_tone=lambda f: self._evt_q.put(f),
                                    src_rate=self.capture.rate or 48000,
                                    backend=self._live_backend(),
                                    fix_text=False,
                                    alphabet=getattr(self, "alphabet", "latin"))
                                self.stream_dec.start()
                                for b in getattr(self, "_live_pending", []):
                                    self.stream_dec.feed(b)
                                self._live_pending = []
                                self.status.configure(
                                    text=f"{self.t('tone')}: {self.selected_hz:.1f} Hz")
                        elif (self.follow_var.get()
                              and abs(f0 - (self.selected_hz or 0)) > 20):
                            # 只有在"当前音调明显很糟"时才切换，避免越换越差
                            try:
                                seg2 = seg[-15 * 8000:] if len(seg) > 15 * 8000 else seg
                                cur = dsp.decode_samples(seg2, tone=self.selected_hz)
                                ct = cur.get("text", "")
                                bad = (ct.count("?") / max(1, len(ct))) > 0.25
                                alt = dsp.decode_samples(seg2, tone=f0)
                                sc_cur = dsp._text_score(ct) + (cur.get("snr_db") or 0) / 20.0
                                sc_alt = dsp._text_score(alt.get("text", "")) \
                                    + (alt.get("snr_db") or 0) / 20.0
                                if bad and sc_alt > sc_cur + 0.8:
                                    self.stream_dec.set_tone(f0)
                                    self.selected_hz = round(float(f0), 1)
                                    self.status.configure(
                                        text=f"{self.t('tone')}: {f0:.1f} Hz")
                            except Exception:
                                import traceback
                                _ui_err_log("[_live_poll]\n" + traceback.format_exc())
                        self._live_last_retune = time.time()
                        # 只有真的建好解码器才算"锁定"，否则下次还要继续估
                        if self.stream_dec is not None:
                            self._live_tone_done = True

        if self.tracker is not None:
            try:
                while True:
                    self._mc_render(self._mc_q.get_nowait())
            except queue.Empty:
                pass
        # ---- 自动跟随频率：停在一个音调上解码，信号换了频率就跟着换 ----
        if (self.stream_dec is not None and not self._tone_manual
                and self.follow_var.get()):
            grew = len(self._live_raw) > self._last_len
            if grew:
                self._last_len = len(self._live_raw)
                self._last_grow = now
            elif now - self._last_grow > 3.0 and self.capture.level_db > -45.0:
                seg2 = self.capture.snapshot8k()[-8 * 8000:]
                if len(seg2) > 4 * 8000 and dsp.tonality(seg2) >= 30.0:
                    f_new = dsp.pick_tone_robust(seg2)
                    # 当前音调上几乎没能量、而新峰强很多 → 立刻切
                    quick = False
                    if 200 < f_new < 1600 and abs(f_new - self.stream_dec.f0) > 60:
                        try:
                            spec = np.abs(np.fft.rfft(seg2 * np.hanning(len(seg2))))
                            fr = np.fft.rfftfreq(len(seg2), 1 / 8000.0)
                            def band_e(f):
                                m = (fr > f - 60) & (fr < f + 60)
                                return float(np.max(spec[m])) if m.any() else 0.0
                            quick = band_e(f_new) > band_e(self.stream_dec.f0) * 2.5
                        except Exception:
                            quick = False
                    if 200 < f_new < 1600 and abs(f_new - self.stream_dec.f0) > 25:
                        if abs(f_new - self._cand_tone) <= 15:
                            self._cand_n += 1
                        else:
                            self._cand_tone, self._cand_n = f_new, 1
                        if self._cand_n >= 2 or quick:
                            # 换台前把已抄到的内容存档，避免被新信号覆盖
                            if self._live_raw.strip():
                                self._live_prefix += self._live_raw.strip() + "  "
                                self._live_raw = ""
                                self.stream_dec.text = ""

                            self.selected_hz = float(f_new)
                            # 换台必须清空解码缓冲，否则旧音频会被重新解一遍 → 内容重复
                            self.stream_dec.set_tone(float(f_new))
                            # 用"新报文起点"作为重新起步的位置：
                            # 先找最后一段较长的静音，再从那之后开始解，开头就不会被切掉
                            try:
                                buf15 = self.capture.snapshot8k()[-15 * 8000:]
                                on = dsp.find_onset(buf15, float(f_new))
                                if (on is not None
                                        and on < len(buf15) / 8000.0 - 0.4):
                                    start = max(0, int((on - 0.9) * 8000))
                                    seed = buf15[start:]
                                else:
                                    seed = buf15[-6 * 8000:]
                                self.stream_dec.reseed(seed)
                            except Exception:
                                self.stream_dec.reset()
                            # 丢掉换台前排队中的旧更新，避免把新文本搅乱
                            try:
                                while True:
                                    self._live_q.get_nowait()
                            except queue.Empty:
                                pass
                            self._cand_n = 0
                            self._last_grow = now
                            self.ent_hz.delete(0, "end")
                            self.ent_hz.insert(0, f"{f_new:.1f}")
                            self._draw_marker()
                            self.status.configure(text=f"{self.t('tone')}: {f_new:.1f} Hz")
                    else:
                        self._cand_n = 0

        # 每 10 秒检查一次"系统音频增强"（会自动压缩动态，降低抄收率）
        if self.capture.running and now - getattr(self, "_fx_check", 0) > 10:
            self._fx_check = now
            try:
                buf8 = self.capture.snapshot8k()
                if len(buf8) > 8 * 8000 and self.selected_hz:
                    y8 = buf8[-8 * 8000:]
                    band = dsp.bandpass(y8.astype(np.float32), 8000, self.selected_hz, 180)
                    env = dsp.envelope(band, 8000)
                    mask = dsp.binarize(env)
                    if mask.any() and (~mask).any():
                        ps = float(np.mean(env[mask] ** 2))
                        pn = float(np.mean(env[~mask] ** 2))
                        contrast = 10 * float(np.log10((ps + 1e-12) / (pn + 1e-12)))
                        self._fx_contrast = contrast
                        if contrast < 11.0:
                            self.status.configure(text=self.t("fx_hint"))
            except Exception:
                import traceback
                _ui_err_log("[_live_poll]\n" + traceback.format_exc())

        if self.tracker is not None and time.time() - self._mc_last >= 2.0:
            self._mc_last = time.time()
            try:
                self._mc_update()
            except Exception:
                import traceback
                _ui_err_log("[_live_poll]\n" + traceback.format_exc())

        if self.capture.running:
            self.after(80, self._live_poll)
        else:
            self._drain_live()

    @staticmethod
    def _wrap_live(text: str, width: int = 78) -> str:
        """按行折行：Tk 里一整个 7000 字的长行每次插入都要重新排版，极慢。"""
        out, line = [], ""
        for w in text.split(" "):
            if not w:
                continue
            if not line:
                line = w
            elif len(line) + 1 + len(w) <= width:
                line += " " + w
            else:
                out.append(line)
                line = w
        if line:
            out.append(line)
        return "\n".join(out)

    def _live_render(self, shown: str):
        """增量刷新实时文本：只删除变化的尾部、追加新内容。

        整段重写会让"文本越长越卡"（Tk 每次都要重排全部字符）。
        """
        shown = self._wrap_live(shown)
        cur = getattr(self, "_shown_cache", "")
        if shown == cur:
            return
        n = 0
        lim = min(len(cur), len(shown))
        while n < lim and cur[n] == shown[n]:
            n += 1
        back = len(cur) - n
        add = shown[n:]
        self.txt.configure(state="normal")
        if back > 0:
            self.txt.delete(f"end-{back + 1}c", "end-1c")
        if add:
            self.txt.insert("end", add)
        self.txt.see("end")
        self.txt.configure(state="disabled")
        self._shown_cache = shown
        self._update_empty_hint()

    def on_cap_start(self):
        self._clear_live_state()       # 每次开始监听都从干净状态起步
        sid = self._src_id()
        if not sid:
            messagebox.showinfo(self.t("title"), self.t("cap_none"))
            return
        # 实时模式与文件模式彻底隔离：先关掉已打开的文件
        if self.path:
            self.on_close_file()
        self.capture.clear()
        self._live_pending: list[np.ndarray] = []
        # 每次开始监听都清掉上一次的音调历史，避免"沿用上一次的频率"
        self._tone_hist = []
        self._live_last_retune = 0.0
        self._cand_tone, self._cand_n = 0.0, 0
        self._last_len, self._last_grow = 0, time.time()
        if self.live_var.get():
            self._set_text("")
            self._live_raw = ""
            self._live_prefix = ""
            # 清掉文件模式遗留的频道与选区，避免影响实时
            self.selected_hz = None
            self._tone_manual = False
            self._cands = []
            for w in list(self.cand_bar.winfo_children()):
                if w is not self.lbl_cand:
                    w.destroy()
            self._cands = []
            self.lbl_cap.configure(text=self.t("cap_running"))
            if self.selected_hz:
                self.stream_dec = WindowDecoder(
                    f0=self.selected_hz,
                    on_update=lambda b, a: self._live_q.put((b, a)),
                    on_tone=lambda f: self.after(0, lambda: self.status.configure(
                        text=f"{self.t('tone')}: {f:.1f} Hz")),
                    src_rate=self.capture.rate or 48000,
                    backend=self._live_backend(), fix_text=False,
                    alphabet=getattr(self, "alphabet", "latin"))
                self.stream_dec.start()
                self._live_tone_done = True
            else:
                self.stream_dec = None      # 等自动估出音调后再建解码器
                self._live_tone_done = False
        else:
            self.stream_dec = None
        self._tone_manual = False
        self.capture.on_block = self._on_capture_block
        self._show_stop_hint(False)
        ok = self.capture.start(sid)
        try:
            self._update_empty_hint()
            self._sync_channel_panels()
        except Exception:
            pass
        if ok and self.stream_dec is not None:
            # 采集启动后才能知道真实采样率（48k），必须同步给解码器
            self.stream_dec.src_rate = self.capture.rate or 48000
        if not ok:
            messagebox.showerror(self.t("error"), self.capture.error or "")
            return
        self.btn_cap_start.configure(state="disabled")
        self.btn_cap_stop.configure(state="normal")
        self.btn_cap_save.configure(state="normal")
        self.cmb_src.configure(state="disabled")
        if self.mc_var.get():
            self.tracker = ChannelTracker()
            self._on_mc_toggle()
        else:
            self.tracker = None
        self._mc_last = 0.0
        self._mc_busy = False
        self._mc_q: queue.Queue = queue.Queue()
        self._tick_job = None
        self._tick_capture()
        if self.live_var.get():
            self._wf_start()
            self.after(80, self._live_poll)

    def on_cap_stop(self):
        was = getattr(self, "_live_snapshot", None)
        self.capture.stop()
        # 灯改为"已停止"并保留数值（复位时才清空）
        if self.quality_frame.winfo_manager() and any(
                self.q_labels[k].cget("text") for k in ("level", "snr")):
            self._q_color["signal"] = "off"
            self._q_pending["signal"] = None
            self.q_labels["signal"].configure(
                text="● " + self.t("q_signal") + " " + self.t("q_stopped"),
                foreground=self.Q_COLORS["off"])
            try:
                self.lbl_hold.pack(side="left", padx=(0, 8), after=self.q_labels["signal"])
            except Exception:
                import traceback
                _ui_err_log("[on_cap_stop]\n" + traceback.format_exc())
            self._live_snapshot = dict(
                level=(was or {}).get("level", getattr(self, "_q_ema", {}).get("level")),
                snr=(was or {}).get("snr", getattr(self, "_q_ema", {}).get("snr")),
                signal="—（已停止）",
                ts=(was or {}).get("ts", time.time()),
                state="stopped")
        self.tracker = None
        try:
            self._sync_channel_panels()
        except Exception:
            import traceback
            _ui_err_log("[on_cap_stop]\n" + traceback.format_exc())
        if self.stream_dec is not None:
            try:
                self.stream_dec.stop()
                self.stream_dec.flush()
            except Exception:
                import traceback
                _ui_err_log("[on_cap_stop]\n" + traceback.format_exc())
            self._drain_live()
            self.stream_dec = None
        self.capture.on_block = None
        # 冻结当前瀑布画面（不立刻切回静态频谱）
        if self._wf_job:
            try:
                self.after_cancel(self._wf_job)
            except Exception:
                import traceback
                _ui_err_log("[on_cap_stop]\n" + traceback.format_exc())
            self._wf_job = None
        self._wf_frozen = getattr(self, "_wf_mode", False)
        # 回到文件模式：重新生成频道按钮
        if self.path and self._y8k is not None:
            try:
                probe = self._y8k[: 45 * 8000] if len(self._y8k) > 45 * 8000 else self._y8k
                self._cands = detect_candidates(self._spec[0], self._spec[1], audio=probe)
                self._build_candidate_bar()
            except Exception:
                import traceback
                _ui_err_log("[on_cap_stop]\n" + traceback.format_exc())
        self.btn_cap_start.configure(state="normal")
        self.btn_cap_stop.configure(state="disabled")
        self.cmb_src.configure(state="readonly")
        self.lbl_cap.configure(text=f"{self.t('cap_idle')}  "
                                    f"{len(self.capture.snapshot())/8000:.1f} s")
        # 停下后给一句说明：文本保留、录音只有最近 N 秒、要重解请用文件模式
        try:
            secs = int(len(self.capture.snapshot8k()) / 8000.0)
            self.status.configure(text=self.t("cap_stopped").format(sec=secs))
        except Exception:
            import traceback
            _ui_err_log("[on_cap_stop]\n" + traceback.format_exc())
        self._show_stop_hint(True)

    def on_cap_save(self):
        y = self.capture.snapshot()
        if len(y) == 0:
            return
        path = filedialog.asksaveasfilename(defaultextension=".wav",
                                            filetypes=[("WAV", "*.wav")])
        if not path:
            return
        dur = self.capture.save_wav(path)
        self.lbl_cap.configure(text=f"{self.t('cap_saved')}: {dur:.1f} s")
        self.status.configure(text=f"{self.t('cap_saved')}: {dur:.1f} s")

    def _live_backend(self) -> str:
        return "ctc" if self.live_be_var.get() == "CTC" else "dsp"

    def _wf_feed(self, y):
        if self._wf is not None and self._wf_mode:
            try:
                self._wf.feed(y, self.capture.rate or 48000)
            except Exception:
                import traceback
                _ui_err_log("[_wf_feed]\n" + traceback.format_exc())

    def _on_capture_block(self, y):
        self._wf_feed(y)
        """采集线程回调：音调未知时先缓存，已知时直接解码。"""
        if self.stream_dec is not None:
            self.stream_dec.feed(y)
        else:
            self._live_pending.append(y)
            if sum(len(b) for b in self._live_pending) > 30 * 8000:
                self._live_pending.pop(0)

    def _drain_live(self):
        items = []
        while True:
            try:
                items.append(self._live_q.get_nowait())
            except queue.Empty:
                break
        if not items:
            return
        for back, add in items:
            if back > 0:
                self._live_raw = self._live_raw[: max(0, len(self._live_raw) - back)]
            if add:
                self._live_raw += add

        shown = self._live_prefix + self._live_shown(self._live_raw)

        self._live_render(shown)

    def _tick_capture(self):
        if not self.capture.running:
            return
        db = self.capture.level_db
        frac = max(0.0, min(1.0, (db + 60.0) / 60.0))
        self.meter.delete("all")
        w = int(self.meter.winfo_width() or 150)
        n = 30
        lit = int(round(frac * n))
        for i in range(n):
            x0 = 2 + i * (w - 4) / n
            x1 = x0 + (w - 4) / n - 1.5
            color = "#39d98a" if i < n * 0.7 else ("#ffd166" if i < n * 0.9 else "#ff5252")
            fill = color if i < lit else "#22303c"
            self.meter.create_rectangle(x0, 3, x1, 13, fill=fill, outline="")
        dur = len(self.capture.snapshot()) / 8000
        extra = ""
        q = self.txt.get("1.0", "end").count("?")
        wpm = getattr(self.stream_dec, "wpm", None) if self.stream_dec is not None else None
        we = getattr(self.stream_dec, "wpm_eff", None) if self.stream_dec is not None else None
        if wpm:
            if we and abs(we - wpm) > 0.6:
                extra = f"  {wpm:4.1f}/{we:3.1f} WPM"
            else:
                extra = f"  {wpm:4.1f} WPM"
        if q:
            extra += f"  ?x{q}"
        # 实时模式：把输入电平也显示出来（只读提示，便于判断麦克风条件）
        try:
            lv = getattr(self.capture, "level_db", None)
            if isinstance(lv, (int, float)) and lv > -120:
                # 实时数值做 ~3 秒滑动平均，避免每 hop 跳动
                hist = getattr(self, "_lvl_hist", None)
                if hist is None:
                    hist = []
                    self._lvl_hist = hist
                now = time.time()
                hist.append((now, float(lv)))
                while hist and now - hist[0][0] > 3.0:
                    hist.pop(0)
                avg = sum(v for _, v in hist) / max(1, len(hist))
                if avg < -33:
                    extra += f"  {self.t('q_lvl_low')} {avg:.0f}dB"
                elif avg < -28:
                    extra += f"  {self.t('q_lvl')} {avg:.0f}dB"
        except Exception:
            pass
        nfix = getattr(self.stream_dec, "corrections", 0) if self.stream_dec is not None else 0
        if nfix:
            extra += f"  {self.t('q_fix')} {nfix}"
        self.lbl_cap.configure(text=f"{self.t('cap_running')}  {db:5.1f} dBFS  {dur:5.1f} s{extra}")

        # 实时三灯：每 1 秒刷新一次（与解码 hop 解耦）
        try:
            if time.time() - getattr(self, "_q_ui_ts", 0.0) >= 1.0:
                self._q_ui_ts = time.time()
                self._update_live_quality()
        except Exception:
            import traceback
            _ui_err_log("[_update_live_quality]\n" + traceback.format_exc())

        self.after(100, self._tick_capture)

    # ------------------------------------------------------------ playback
    def _refresh_play_audio(self):
        """Rebuild the playback buffer (optionally band-passed to the channel)."""
        if self._y8k is None:
            return
        pos = self._player.position
        was_playing = self._player.is_playing
        if self.ch_var.get() and self.selected_hz:
            try:
                bw = float(self.bw_var.get() or 250)
            except ValueError:
                bw = 250.0
            y = dsp.bandpass(self._y8k.astype(np.float32), 8000, self.selected_hz, bw)
            peak = float(np.max(np.abs(y)) or 1.0)
            y = (y / peak * 0.9).astype(np.float32)
        else:
            y = self._y8k
        self._player.set_audio(y, 8000)
        self._player.set_volume(float(self.vol_var.get()) / 100.0)
        self._player.seek(pos)
        if was_playing:                       # 换频道/带宽后继续播放
            self._player.play(pos)
        self._draw_band()
        self.lbl_time.configure(text=f"{pos:.1f} / {self._player.duration:.1f} s")

    def _on_volume(self, _v=None):
        pct = float(self.vol_var.get())
        self._player.set_volume(pct / 100.0)
        self.lbl_vol.configure(text=f"{pct:.0f}%")
        self._draw_band()

    def _on_bw_change(self, _evt=None):
        self._refresh_play_audio()
        self._draw_band()

    def _set_play_enabled(self, on: bool):
        st = "normal" if on else "disabled"
        for w in (self.btn_play, self.btn_stop, self.chk_ch, self.scl_vol):
            try:
                w.configure(state=st)
            except Exception:
                pass
        try:
            self.cmb_bw.configure(state="readonly" if on else "disabled")
        except Exception:
            pass
        self.timeline.configure(cursor="hand2" if on else "arrow")
        self.vs.set_enabled(on)
        self._play_enabled = on

    def _draw_band(self):
        """Show the pass-band used for 'channel only' playback."""
        self.canvas.delete("band")
        if not self._plot or not self.selected_hz or not self.ch_var.get():
            return
        try:
            bw = float(self.bw_var.get() or 250)
        except ValueError:
            bw = 250.0
        x0, y0, pw, ph = self._plot
        lo = max(self.view["f0"], self.selected_hz - bw / 2)
        hi = min(self.view["f1"], self.selected_hz + bw / 2)
        if hi <= lo:
            return
        y_hi = self._freq_to_y(hi)
        y_lo = self._freq_to_y(lo)
        self.canvas.create_rectangle(x0, y_hi, x0 + pw, y_lo,
                                     outline="#39d98a", fill="#39d98a",
                                     stipple="gray12", width=1, tags="band")
        self.canvas.create_text(x0 + pw - 6, y_hi - 8, anchor="e", fill="#39d98a",
                                text=f"{self.t('band')} {bw:.0f} Hz",
                                font=("Consolas", 9), tags="band")

    def on_play_toggle(self):
        """One button: play / pause."""
        if self._y8k is None:
            return
        if self._player.is_playing:
            self._player.pause()
            self.btn_play.configure(text=self.t("play"))
            return
        if self._player.is_paused:
            self._player.resume()
        else:
            self._player.play(self._player.position)
        self.btn_play.configure(text=self.t("pause"))
        self._tick()

    def on_play(self):
        if self._y8k is None:
            return
        if self._player.is_paused:
            self._player.resume()
        else:
            self._player.play(self._player.position)
        self._tick()

    def on_pause(self):
        if self._player.is_playing:
            self._player.pause()

    def on_stop(self):
        self._player.stop()
        self.btn_play.configure(
            text=self.t("pause") if self._player.is_playing else self.t("play"))
        self.btn_reset.configure(text=self.t("reset"))
        self._player.seek(0.0)
        self.lbl_time.configure(text=f"0.0 / {self._player.duration:.1f} s")
        self._draw_playhead()

    def on_timeline_click(self, evt):
        if not getattr(self, "_play_enabled", False):
            return
        if self._y8k is None or not self._plot:
            return
        # 时间轴的 x 原点是"频谱画布的偏移 + 左边距"，不是 _plot[0]
        try:
            offset = self.canvas.winfo_rootx() - self.timeline.winfo_rootx()
        except Exception:
            offset = 18
        tl = offset + MARGIN_L
        twidth = max(1, self._plot[2])
        frac = (evt.x - tl) / twidth
        frac = min(1.0, max(0.0, frac))
        was_playing = self._player.is_playing
        self._player.seek(frac * self._player.duration)
        if was_playing:
            self._player.play(self._player.position)
        self._draw_playhead()
        self.lbl_time.configure(text=f"{self._player.position:.1f} / {self._player.duration:.1f} s")

    def _tick(self):
        playing = self._player.is_playing
        self._draw_playhead()
        if self._y8k is not None:
            self.lbl_time.configure(
                text=f"{self._player.position:.1f} / {self._player.duration:.1f} s")
        if playing:
            self.after(33, self._tick)
        else:
            self.btn_play.configure(text=self.t("play"))
            if self._player.duration and self._player.position >= self._player.duration - 0.03:
                self._player.seek(0.0)          # 播完回到开头
                self.lbl_time.configure(text=f"0.0 / {self._player.duration:.1f} s")
                self._draw_playhead()
        self.btn_reset.configure(text=self.t("reset"))

    def _draw_playhead(self):
        if not self._plot:
            return
        x0, y0, pw, ph = self._plot
        dur = self._player.duration or 1.0
        pos = self._player.position
        frac = min(1.0, max(0.0, pos / dur))
        t0, t1 = self.view["t0"], self.view["t1"]
        xv = x0 + (pos - t0) / max(1e-6, (t1 - t0)) * pw

        self.canvas.delete("play")
        if x0 <= xv <= x0 + pw:
            self.canvas.create_line(xv, y0, xv, y0 + ph, fill="#ffe066", width=2, tags="play")

        self.timeline.delete("all")
        # 与频谱画布的绘图区严格对齐：用屏幕坐标差，避免画布嵌套后 winfo_x 恒为 0
        try:
            offset = self.canvas.winfo_rootx() - self.timeline.winfo_rootx()
        except Exception:
            offset = 18
        tl = offset + MARGIN_L
        twidth = max(50, pw)
        t0, t1 = self.view["t0"], self.view["t1"]
        tspan = max(1e-6, t1 - t0)
        # 刻度：和频谱内部时间轴用同一套步长与映射，保证两条轴对得上
        step = self._nice_step(tspan / 6.0)
        t = np.floor(t0 / step) * step
        while t <= t1 + 1e-9:
            if t >= t0:
                tx = tl + (t - t0) / tspan * twidth
                self.timeline.create_line(tx, 16, tx, 22, fill="#8fa3bf")
                self.timeline.create_text(tx, 8, text=f"{t:g}", fill="#9fb3c8",
                                          font=("Consolas", 8))
            t += step
        self.timeline.create_line(tl, 22, tl + twidth, 22, fill="#33404f")
        if t0 <= pos <= t1:
            xp = tl + (pos - t0) / tspan * twidth
            self.timeline.create_line(xp, 0, xp, 26, fill="#ffe066", width=2)
        self.timeline.create_text(tl + twidth, 8, anchor="e", text="s", fill="#6f8499",
                                  font=("Consolas", 8))

    # ------------------------------------------------------------ spectrum
    def _compute_spectrum(self, x: np.ndarray):
        n_fft, hop = 512, 160
        n = len(x)
        n_frames = max(1, (n - n_fft) // hop)
        pad = np.pad(x, (n_fft // 2, n_fft // 2 + hop * n_frames))
        idx = np.arange(n_frames)[:, None] * hop + np.arange(n_fft)[None, :]
        frames = pad[idx] * np.hanning(n_fft)[None, :]
        spec = np.abs(np.fft.rfft(frames, axis=1)).T
        freqs = np.fft.rfftfreq(n_fft, 1.0 / 8000)
        return spec, freqs

    # ------------------------------------------------------------ view helpers
    def _align_vslider(self):
        """滑块与绘图区严格对齐（频谱/瀑布共用）。"""
        try:
            if not self._plot:
                return
            y0, ph = self._plot[1], self._plot[3]
            try:
                dy = self.canvas.winfo_rooty() - self.vs.master.winfo_rooty()
            except Exception:
                dy = 0
            self.vs.place_configure(x=0, y=dy + y0,
                                    width=18, height=max(40, ph))
        except Exception:
            pass

    def _freq_to_y(self, hz: float) -> float:
        x0, y0, pw, ph = self._plot
        f0, f1 = self.view["f0"], self.view["f1"]
        return y0 + ph - (hz - f0) / max(1e-6, (f1 - f0)) * ph

    def _y_to_freq(self, y: float) -> float:
        x0, y0, pw, ph = self._plot
        f0, f1 = self.view["f0"], self.view["f1"]
        frac = 1.0 - (y - y0) / max(1, ph)
        return f0 + min(1.0, max(0.0, frac)) * (f1 - f0)

    def _time_to_x(self, t: float) -> float:
        x0, y0, pw, ph = self._plot
        t0, t1 = self.view["t0"], self.view["t1"]
        return x0 + (t - t0) / max(1e-6, (t1 - t0)) * pw

    def _x_to_time(self, x: float) -> float:
        x0, y0, pw, ph = self._plot
        t0, t1 = self.view["t0"], self.view["t1"]
        frac = (x - x0) / max(1, pw)
        return t0 + min(1.0, max(0.0, frac)) * (t1 - t0)

    def on_vscroll(self, value):
        """Vertical slider = pan the frequency window (span stays the same)."""
        # 实时模式：视图固定，滑块不生效（由 _wf_start 置灰）
        if getattr(self, "_wf_mode", False):
            return
        if self._sync_guard or self._spec is None:
            return
        span = self.view["f1"] - self.view["f0"]
        center = max(self.vs.minimum, min(self.vs.maximum, float(value)))
        f0 = center - span / 2
        f1 = center + span / 2
        if f0 < 50:
            f0, f1 = 50.0, 50.0 + span
        if f1 > 3500:
            f1, f0 = 3500.0, 3500.0 - span
        self.view["f0"], self.view["f1"] = max(50.0, f0), min(3500.0, f1)
        self._draw_spectrum()

    def _sync_vslider(self):
        """滑块代表"视图中心"，范围限制在中心可到达的区间，避免滑到一半就卡住。"""
        self._sync_guard = True
        try:
            f0, f1 = self.view["f0"], self.view["f1"]
            span = max(1.0, f1 - f0)
            lo = max(50.0, 50.0 + span / 2.0)
            hi = min(3500.0, 3500.0 - span / 2.0)
            if hi <= lo:
                lo, hi = 50.0, 3500.0
            self.vs.set_range(lo, hi)
            self.vs.set((f0 + f1) / 2.0)
        finally:
            self._sync_guard = False

    def reset_view(self, _evt=None):
        self._clear_quality()
        """复位：视图位置 + 画面内容 + 文本 + 选中频率 一起清空。"""
        self.view = {"f0": F_MIN, "f1": F_MAX, "t0": 0.0, "t1": max(0.01, self.duration)}
        self._set_text("")
        self.selected_hz = None
        self._tone_manual = False
        self.ent_hz.delete(0, "end")
        self.lbl_suspect.configure(text="")
        # 复位 = 回到默认空白：隐藏"已停止"说明条、恢复空状态提示
        self._show_stop_hint(False)
        self._update_empty_hint()
        # 实时状态：把解码文本和缓冲一起清掉，否则下一次更新会把旧内容带回来
        try:
            self._live_raw = ""
            self._live_prefix = ""
            self._shown_cache = ""
            if self.stream_dec is not None:
                self.stream_dec.reset()
        except Exception:
            pass

        if getattr(self, "_wf_mode", False):
            # 实时/冻结状态：清空瀑布缓冲，从头开始画
            if self._wf is not None:
                self._wf.set_range(F_MIN, F_MAX)
                self._wf.clear()
            if self._wf_item is not None:
                try:
                    self.canvas.itemconfig(self._wf_item, image="")
                except Exception:
                    pass
            # 复位：红线、候选频率线、播放头全部清掉
            for tag in ("marker", "cand", "play"):
                self.canvas.delete(tag)
            self._wf_draw_axes()
            self.status.configure(text=self.t("hint"))
            return

        self._draw_spectrum()
        for tag in ("marker", "cand", "play"):
            self.canvas.delete(tag)
        self.status.configure(text=self.t("hint"))

    def on_wheel(self, evt):
        if not self._plot:
            return
        # 实时模式：视图固定，滚轮不做任何缩放（调谐请用输入框或多频道列表）
        if getattr(self, "_wf_mode", False):
            return
        if self._spec is None:
            return
        steps = 1 if evt.delta > 0 else -1
        factor = 0.8 ** steps
        x0, y0, pw, ph = self._plot
        if (evt.state & 0x0001) or (evt.state & 0x0004):      # Shift 或 Ctrl -> 时间轴缩放
            t_cur = self._x_to_time(evt.x)
            t0, t1 = self.view["t0"], self.view["t1"]
            span = max(0.05, (t1 - t0) * factor)
            frac = (t_cur - t0) / max(1e-6, (t1 - t0))
            new0 = t_cur - frac * span
            new1 = new0 + span
            if new0 < 0:
                new0, new1 = 0.0, span
            if new1 > self.duration:
                new1, new0 = self.duration, self.duration - span
            self.view["t0"], self.view["t1"] = max(0.0, new0), min(self.duration, new1)
        else:                                                  # 默认 -> 频率轴缩放
            f_cur = self._y_to_freq(evt.y)
            f0, f1 = self.view["f0"], self.view["f1"]
            span = max(30.0, (f1 - f0) * factor)
            frac = (f_cur - f0) / max(1e-6, (f1 - f0))
            new0 = f_cur - frac * span
            new1 = new0 + span
            if new0 < 50:
                new0, new1 = 50.0, 50.0 + span
            if new1 > 3500:
                new1, new0 = 3500.0, 3500.0 - span
            self.view["f0"], self.view["f1"] = max(50.0, new0), min(3500.0, new1)
        self._draw_spectrum()

    def _draw_spectrum(self):
        if getattr(self, "_wf_mode", False):
            self._wf_draw_axes()
            return
        if self._spec is None or not _HAS_PIL:
            return
        spec, freqs = self._spec
        cw = max(200, self.canvas.winfo_width())
        ch = max(120, self.canvas.winfo_height())
        pw = max(50, cw - MARGIN_L - MARGIN_R)
        ph = max(50, ch - MARGIN_T - MARGIN_B)

        t1 = self.view["t1"] or max(0.01, self.duration)
        t0 = self.view["t0"]
        t0, t1 = max(0.0, min(t0, self.duration)), max(0.01, min(t1, self.duration))
        if t1 - t0 < 0.01:
            t1 = t0 + 0.01
        self.view["t0"], self.view["t1"] = t0, t1

        fi = np.flatnonzero((freqs >= self.view["f0"]) & (freqs <= self.view["f1"]))
        if fi.size < 2:
            fi = np.arange(len(freqs))
        fps = 8000 / 160.0                       # 时间帧率
        ti = np.arange(int(t0 * fps), max(int(t0 * fps) + 2, int(t1 * fps)))
        ti = np.clip(ti, 0, spec.shape[1] - 1)

        sub = spec[np.ix_(fi, ti)]
        db = 20 * np.log10(sub + 1e-9)
        lo = np.percentile(db, 35)
        hi = np.percentile(db, 99.5)
        img = np.clip((db - lo) / max(1e-6, hi - lo), 0, 1)
        img = _lut()[(img * 255).astype(np.uint8)]
        pil = Image.fromarray(img[::-1, :, :]).resize((pw, ph))
        self._img_ref = ImageTk.PhotoImage(pil)

        self.canvas.delete("all")
        x0, y0 = MARGIN_L, MARGIN_T
        self.canvas.create_image(x0, y0, anchor="nw", image=self._img_ref)
        self._plot = (x0, y0, pw, ph)
        self._spec_h = ph

        # 频率轴（按视图动态取刻度）
        fspan = self.view["f1"] - self.view["f0"]
        fstep = self._nice_step(fspan / 6.0)
        f = np.floor(self.view["f0"] / fstep) * fstep
        while f <= self.view["f1"] + 1e-9:
            if f >= self.view["f0"]:
                y = self._freq_to_y(float(f))
                self.canvas.create_line(x0 - 4, y, x0, y, fill="#8fa3bf")
                self.canvas.create_text(x0 - 6, y, anchor="e", fill="#9fb3c8",
                                        text=f"{f:g}", font=("Consolas", 9))
            f += fstep
        self.canvas.create_text(8, y0 - 6, anchor="w", fill="#6f8499", text="Hz",
                                font=("Consolas", 8))

        # 时间轴（随视图缩放）
        tspan = self.view["t1"] - self.view["t0"]
        tstep = self._nice_step(tspan / 6.0)
        t = np.floor(self.view["t0"] / tstep) * tstep
        while t <= self.view["t1"] + 1e-9:
            if t >= self.view["t0"]:
                x = self._time_to_x(float(t))
                # 刻度线保留，数字交给下面的时间轴（避免两套数字重复且难对齐）
                self.canvas.create_line(x, y0 + ph, x, y0 + ph + 4, fill="#8fa3bf")
            t += tstep


        self._draw_candidates()
        self._draw_marker()
        self._draw_band()
        self._draw_playhead()
        self._sync_vslider()
        self._align_vslider()

    @staticmethod
    def _nice_step(raw: float) -> float:
        if raw <= 0:
            return 1.0
        exp = np.floor(np.log10(raw))
        base = raw / (10 ** exp)
        for m in (1, 2, 5, 10):
            if base <= m:
                return float(m * (10 ** exp))
        return float(10 ** (exp + 1))

    def _draw_candidates(self):
        self.canvas.delete("cand")
        if not self._cands or not self._plot:
            return
        x0, y0, pw, ph = self._plot
        for f, depth, level in self._cands:
            if not (self.view["f0"] <= f <= self.view["f1"]):
                continue
            y = self._freq_to_y(f)
            self.canvas.create_line(x0, y, x0 + pw, y, fill="#3fd0d4", width=1,
                                    dash=(3, 4), tags="cand")
            self.canvas.create_text(x0 + pw - 4, y - 7, anchor="e", fill="#3fd0d4",
                                    text=f"{f:.0f}", font=("Consolas", 8), tags="cand")

    def _build_candidate_bar(self):
        for w in self.cand_bar.winfo_children():
            if w is not self.lbl_cand:
                w.destroy()
        n = len(self._cands)
        self.lbl_cand.configure(
            text=f"{self.t('cand')} ({n})" if n else self.t("cand"))
        sel = self.selected_hz if self.selected_hz else (self._cands[0][0] if self._cands else None)
        for i, (f, depth, level) in enumerate(self._cands):
            chosen = sel is not None and abs(f - sel) < 1.0
            txt = f"{f:.0f} Hz"
            b = tk.Button(self.cand_bar, text=txt, width=9, relief="flat", bd=0,
                          font=("Segoe UI", 9),
                          background="#2f6fb3" if chosen else "#ffffff",
                          foreground="#ffffff" if chosen else "#33475c",
                          activebackground="#3c82cc" if chosen else "#eaf2fb",
                          activeforeground="#ffffff" if chosen else "#1d3550",
                          highlightthickness=1,
                          highlightbackground="#2f6fb3" if chosen else "#cfd9e4",
                          padx=6, pady=3, cursor="hand2",
                          command=lambda hz=f: self._pick(hz))
            b.pack(side="left", padx=(0, 6))

    def _pick(self, hz: float):
        self.selected_hz = float(hz)
        self._tone_manual = True
        if self.capture.running:
            if self.stream_dec is not None:
                self.stream_dec.set_tone(self.selected_hz)
            self.ent_hz.delete(0, "end")
            self.ent_hz.insert(0, f"{hz:.1f}")
            self.after(0, self._build_candidate_bar)
            return
        self._refresh_play_audio()
        self.ent_hz.delete(0, "end")
        self.ent_hz.insert(0, f"{hz:.1f}")
        self._draw_marker()
        self.after(0, self._build_candidate_bar)
        if self.path:
            self.on_decode()

    def _draw_marker(self):
        self.canvas.delete("marker")
        if self.selected_hz is None or not self._plot:
            return
        x0, y0, pw, ph = self._plot
        if not (self.view["f0"] <= self.selected_hz <= self.view["f1"]):
            return
        y = self._freq_to_y(self.selected_hz)
        self.canvas.create_line(x0, y, x0 + pw, y, fill="#ff5252", width=2,
                                tags=("marker", "mline"))
        self.canvas.create_text(x0 + 6, max(y0 + 8, y - 9), anchor="w", fill="#ffd166",
                                text=f"{self.selected_hz:.1f} Hz",
                                font=("Consolas", 10, "bold"), tags="marker")

    def on_canvas_click(self, evt):
        if not self._plot:
            return
        if self.capture.running or getattr(self, "_wf_frozen", False):
            self.status.configure(text=self.t("live_click_hint"))
            return
        x0, y0, pw, ph = self._plot
        if not (x0 <= evt.x <= x0 + pw and y0 <= evt.y <= y0 + ph):
            return
        self.selected_hz = min(3000.0, max(100.0, self._y_to_freq(evt.y)))
        self._tone_manual = True
        self.ent_hz.delete(0, "end")
        self.ent_hz.insert(0, f"{self.selected_hz:.0f}")
        self._draw_marker()
        self._draw_band()
        self._refresh_play_audio()
        self.status.configure(text=f"{self.t('selected')}: {self.selected_hz:.0f} Hz")
        self.lbl_suspect.configure(text="")

        # 实时监听中：只切换解码频率，绝不去解"上次打开的文件"
        if self.capture.running:
            if self.stream_dec is not None:
                self.stream_dec.set_tone(self.selected_hz)
            return
        if self.path:
            self.on_decode()

    def _start_live_decoder(self, tone: float):
        """按指定音调创建实时解码器（首次自动锁定之前也能用）。"""
        self.selected_hz = float(tone)
        dec = WindowDecoder(
            f0=self.selected_hz,
            on_update=lambda b, a: self._live_q.put((b, a)),
            on_tone=lambda f: self.after(0, lambda: self.status.configure(
                text=f"{self.t('tone')}: {f:.1f} Hz")),
            src_rate=self.capture.rate or 48000,
            backend=self._live_backend(), fix_text=False,
            alphabet=getattr(self, "alphabet", "latin"))
        self.stream_dec = dec
        dec.start()
        try:
            for b in getattr(self, "_live_pending", []):
                dec.feed(b)
            self._live_pending = []
        except Exception:
            import traceback
            _ui_err_log("[_start_live_decoder]\n" + traceback.format_exc())
        self._live_tone_done = True
        return dec

    def _apply_live_tone(self, tone: float):
        """实时中切换音调：没有解码器就建一个，有就只改音调。"""
        if self.stream_dec is None:
            self._start_live_decoder(tone)
        else:
            try:
                self.stream_dec.set_tone(float(tone))
            except Exception:
                import traceback
                _ui_err_log("[_apply_live_tone]\n" + traceback.format_exc())
        self.lbl_suspect.configure(text="")

    def on_manual_freq(self, _evt=None):
        try:
            hz = float(self.ent_hz.get().strip())
        except ValueError:
            return
        if not (100 <= hz <= 3000):
            messagebox.showinfo(self.t("title"), self.t("bad_freq"))
            return
        self.selected_hz = min(F_MAX, max(F_MIN, hz))
        self._tone_manual = True
        self._draw_marker()
        self._draw_band()
        self._refresh_play_audio()
        if self.capture.running:
            self._apply_live_tone(self.selected_hz)
            return
        if self.path:
            self.on_decode()


    # ------------------------------------------------------------ busy hint

    def _show_busy(self):
        self._busy_job = None
        w = tk.Toplevel(self)
        w.withdraw()
        w.overrideredirect(True)
        w.transient(self)
        frm = ttk.Frame(w, padding=(24, 14), relief="solid", borderwidth=1)
        frm.pack()
        ttk.Label(frm, text=self.t("decoding"), font=("Segoe UI", 11)).pack()
        w.update_idletasks()

        ww, wh = w.winfo_reqwidth(), w.winfo_reqheight()
        x = self.winfo_rootx() + (self.winfo_width() - ww) // 2
        y = self.winfo_rooty() + (self.winfo_height() - wh) // 2
        w.geometry(f"{ww}x{wh}+{max(0, x)}+{max(0, y)}")
        w.deiconify()
        w.lift()
        self._busy_win = w

    def _end_busy(self):
        if getattr(self, "_busy_job", None):
            try:
                self.after_cancel(self._busy_job)
            except Exception:
                pass
            self._busy_job = None
        if getattr(self, "_busy_win", None):
            try:
                self._busy_win.destroy()
            except Exception:
                pass
            self._busy_win = None
        self.config(cursor="")

    # ------------------------------------------------------------ actions
    def on_lang(self, _evt=None):
        self.lang = {"English": "en", "Русский": "ru"}.get(self.lang_var.get(), "zh")
        self.title(self.t("title"))
        # 统一刷新所有带静态文案的控件（新增控件请加进这张表）
        _WIDGET_KEYS = (
            ("btn_open", "open"), ("btn_close_file", "close_file"),
            ("btn_decode", "decode"), ("btn_save", "save"), ("btn_clear", "clear"),
            ("btn_tx", "txpad"), ("lbl_lang", "lang"), ("lbl_spec", "spectrum"),
            ("lbl_hz", "hz"), ("lbl_be", "backend"), ("lbl_cand", "cand"),
            ("btn_cap_start", "cap_start"), ("btn_cap_stop", "cap_stop"),
            ("btn_cap_save", "cap_save"), ("chk_mc", "multi"),
            ("chk_follow", "follow"), ("chk_live", "live_dec"),
            ("btn_reset", "reset"), ("btn_stop", "stop"), ("chk_ch", "ch_only"),
        )
        for attr, key in _WIDGET_KEYS:
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.configure(text=self.t(key))
                except Exception:
                    pass
        self.btn_cyr.configure(text=self.t("latin") if self.show_cyr else self.t("cyr"))
        self._update_alpha_label()
        self.chk_gate.configure(text=self.t("gate"))
        self.chk_fix.configure(text=self.t("fix"))
        self.btn_cmp.configure(text=self.t("cmp"))
        self.btn_play.configure(
            text=self.t("pause") if self._player.is_playing else self.t("play"))
        self.btn_reset.configure(text=self.t("reset"))
        self.btn_stop.configure(text=self.t("stop"))
        self.chk_ch.configure(text=self.t("ch_only"))
        self._rebuild_backend_values()
        self._refresh_src()
        if hasattr(self, "lbl_empty"):
            self._update_empty_hint()          # 切换语言后刷新空状态文案
        if hasattr(self, "lbl_stop_hint"):
            self._show_stop_hint(self.lbl_stop_hint.winfo_manager() == "pack")
        try:
            self.mc_tree.heading("freq", text=self.t("col_freq"))
            self.mc_tree.heading("lvl", text=self.t("col_dbfs"))
            self.mc_tree.heading("wpm", text=self.t("col_wpm"))
            self.mc_tree.heading("call", text=self.t("col_call"))
            self.lbl_hold.configure(text=self.t("q_hold"))
        except Exception:
            pass
        # 状态栏与实时电平提示也跟随语言
        try:
            self.status.configure(text=self.t("hint"))
        except Exception:
            pass
        try:
            self.lbl_cap.configure(text=self.t("cap_stopped").format(sec=120)
                                   if self.capture.running else self.t("cap_idle"))
        except Exception:
            pass

    def _selected_backend(self) -> str:
        sel = self.be_var.get()
        if sel.startswith("CTC"):
            return "ctc"
        if sel.startswith("DSP"):
            return "dsp"
        return "auto"

    def _rebuild_backend_values(self):
        cur = self._selected_backend()
        vals = [self.t("be_dsp"), self.t("be_ctc"), self.t("be_auto")]
        self.cmb_be.configure(values=vals)
        self.be_var.set({"dsp": vals[0], "ctc": vals[1], "auto": vals[2]}[cur])

    def _refresh_src(self):
        name = os.path.basename(self.path) if self.path else self.t("none")
        self.lbl_src.configure(text=f"{self.t('src')}: {name}")

    def on_open(self):
        path = filedialog.askopenfilename(
            title=self.t("open"),
            filetypes=[("WAV audio", "*.wav"), ("All files", "*.*")])
        if not path:
            return
        if self.capture.running:
            self.on_cap_stop()
        # 退出瀑布/冻结状态，回到静态频谱
        self._wf_mode = False
        self._wf_frozen = False
        self._wf = None
        self._wf_item = None
        if self._wf_job:
            try:
                self.after_cancel(self._wf_job)
            except Exception:
                pass
            self._wf_job = None
        self.path = path
        self.selected_hz = None
        self._clear_quality()          # 换文件时先清空上一次的指示灯
        self._refresh_src()

        x, sr = dsp.read_wav(path)
        y = dsp.resample(x, sr)
        y = y / (float(np.max(np.abs(y))) or 1.0)
        self.duration = len(y) / 8000.0
        self._y8k = y.astype(np.float32)
        self._player.set_audio(self._y8k, 8000)
        self._player.set_volume(float(self.vol_var.get()) / 100.0)
        self.lbl_time.configure(text=f"0.0 / {self._player.duration:.1f} s")
        self._set_play_enabled(True)
        self._spec = self._compute_spectrum(y)
        self.view = {"f0": F_MIN, "f1": F_MAX, "t0": 0.0, "t1": self.duration}
        # 频道检测只用前 45 秒的音频：快 15 倍，且结果基本一致
        probe = y[: 45 * 8000] if len(y) > 45 * 8000 else y
        self._cands = detect_candidates(self._spec[0], self._spec[1], audio=probe)
        self._draw_spectrum()
        self._build_candidate_bar()
        self._sync_channel_panels()
        self._show_stop_hint(False)
        self.status.configure(text=self.t("hint"))
        self.lbl_suspect.configure(text="")

    def on_decode(self):
        if not self.path or self._spec is None:
            messagebox.showinfo(self.t("title"), self.t("no_file"))
            return
        if self._decoding:                 # 正在解码，忽略重复点击
            return

        self._decoding = True
        self._busy_start = time.time()
        self.config(cursor="watch")
        self.status.configure(text=self.t("decoding"))
        self.lbl_suspect.configure(text="")
        # 250 ms 后才弹提示窗：快任务不会闪
        self._busy_job = self.after(250, self._show_busy)

        backend = self._selected_backend()
        args = (self.path, backend, self.selected_hz, bool(self.gate_var.get()))
        threading.Thread(target=self._worker, args=args, daemon=True).start()
        self._alphabet = self.alphabet
        self.after(60, self._poll)

    def _worker(self, path, backend, tone, gate):
        alphabet = getattr(self, "_alphabet", "latin")
        try:
            res = pipeline.decode_file(path, backend=backend, tone=tone,
                                       out_dir=self.out_dir, gate=gate,
                                       alphabet=alphabet)
            self._q.put(("ok", res))
        except Exception as e:                            # noqa: BLE001
            self._q.put(("err", e))

    def _poll(self):
        try:
            kind, payload = self._q.get_nowait()
        except queue.Empty:
            self.after(60, self._poll)
            return

        self._end_busy()
        self._decoding = False
        if kind == "err":
            messagebox.showerror(self.t("error"), str(payload))
            self.status.configure(text=self.t("hint"))
            return

        self._apply_result(payload)

    def _show_stop_hint(self, on: bool):
        if not hasattr(self, "lbl_stop_hint"):
            return
        try:
            if on:
                self.lbl_stop_hint.pack(fill="x", pady=(4, 0))
                self.lbl_stop_hint.configure(text=self.t("stop_hint"))
            else:
                self.lbl_stop_hint.pack_forget()
            self._update_empty_hint()
        except Exception:
            pass

    def _empty_asset_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "empty_state.png")

    def _on_txt_resize(self, _evt=None):
        """窗口尺寸变化时，防抖重建空状态图片。"""
        if self._empty_img_job:
            try:
                self.after_cancel(self._empty_img_job)
            except Exception:
                pass
        self._empty_img_job = self.after(160, self._render_empty_image)

    def _render_empty_image(self):
        """按文本框宽度生成空状态插图（结果缓存，避免反复解码图片）。"""
        self._empty_img_job = None
        if not hasattr(self, "lbl_empty_img") or not self.lbl_empty_img.winfo_manager():
            return
        try:
            from PIL import Image, ImageTk
        except Exception:
            return
        path = self._empty_asset_path()
        if not os.path.exists(path):
            return
        txt_w = max(200, self.txt.winfo_width())
        txt_h = max(120, self.txt.winfo_height())
        # 宽度不超过 92%，高度不超过 55%（给下面的提示文字留位置）
        w = int(min(txt_w * 0.92, txt_h * 0.55 * 6.25, 1160))
        w = max(240, w)
        ph = self._empty_img_cache.get(w)
        if ph is None:
            try:
                im = Image.open(path)
                h = max(40, int(im.size[1] * w / im.size[0]))
                im = im.resize((w, h), Image.LANCZOS)
                ph = ImageTk.PhotoImage(im)
                self._empty_img_cache[w] = ph
            except Exception:
                return
        self.lbl_empty_img.configure(image=ph)
        self.lbl_empty_img.image = ph

    def _show_empty_art(self, on: bool):
        """显示/隐藏空状态插图。"""
        if not hasattr(self, "lbl_empty_img"):
            return
        try:
            if on:
                self.lbl_empty_img.place(relx=0.5, rely=0.38, anchor="center")
                self._render_empty_image()
            else:
                self.lbl_empty_img.place_forget()
        except Exception:
            pass

    def _update_empty_hint(self):
        """空状态提示：正文为空时显示说明，有内容就隐藏。"""
        if not hasattr(self, "lbl_empty"):
            return
        try:
            content = self.txt.get("1.0", "end-1c").strip()
        except Exception:
            content = ""
        if content:
            self.lbl_empty.place_forget()
            self._show_empty_art(False)
        else:
            live = getattr(self, "capture", None) is not None and self.capture.running
            if not live and getattr(self, "lbl_stop_hint", None) is not None \
                    and self.lbl_stop_hint.winfo_manager():
                self.lbl_empty.place_forget()      # 已停止：由说明条负责解释
                self._show_empty_art(False)
                return
            self.lbl_empty.configure(
                text=self.t("empty_live") if live else self.t("empty_file"))
            self.lbl_empty.place(relx=0.5, rely=0.86, anchor="center")
            self._show_empty_art(True)


    # ---------- 实时信号质量（滚动窗口 + 平滑 + 迟滞） ----------
    LIVE_METRICS_EVERY = 1.0
    _live_snapshot = None          # 最后一帧实时数据（停止后供悬浮提示使用）      # 每 1 秒算一次（不跟 hop 走）
    LIVE_WINDOW_S = 10.0          # 只看最近 10 秒（会话越长，历史越不相关）
    LIVE_TAU_S = 3.0              # EMA 时间常数
    LIVE_CONFIRM = 2              # 颜色变化需连续 2 次确认（迟滞，防闪）

    def _live_metrics(self):
        """取最近 10 秒音频，算 电平 / SNR / 音调性（只读，不影响解码）。"""
        try:
            buf = self.capture.snapshot8k(self.LIVE_WINDOW_S)   # 8k 数据，最近 10 秒
        except Exception:
            return None
        if buf is None or len(buf) < int(3 * 8000):
            return None
        seg = np.asarray(buf, dtype=np.float32)
        # 音调候选：解码器锁定值 + 本地估计值，取"音调性"最高的那个
        # （解码器可能还没锁定，或锁在旧频率上）
        cands = []
        dec = getattr(self, "stream_dec", None)
        f_dec = getattr(dec, "f0", None) if dec is not None else None
        f_est = None
        try:
            f_est = dsp.estimate_tone(seg)
        except Exception:
            f_est = None
        for f in (f_dec, f_est):
            if f and f > 0 and all(abs(f - c) > 5 for c in cands):
                cands.append(float(f))
        if not cands:
            return None
        best, best_ton = None, -1e9
        for f in cands:
            try:
                t = dsp.tonality(seg, f0=f)
            except Exception:
                t = -1e9
            if t > best_ton:
                best, best_ton = f, t
        tone = best
        try:
            snr = dsp.estimate_snr(seg, tone)
        except Exception:
            snr = None
        ton = best_ton
        lvl = 20.0 * float(np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-12))
        return dict(level=lvl, snr=snr, tonality=ton, tone=tone)

    def _smooth(self, key, value, dt):
        """指数滑动平均：τ 越大越稳、越慢。"""
        if value is None:
            return None
        alpha = 1.0 - float(np.exp(-max(1e-3, dt) / self.LIVE_TAU_S))
        old = self._q_ema.get(key)
        val = float(value) if old is None else old + alpha * (float(value) - old)
        self._q_ema[key] = val
        return val

    def _set_live_light(self, key, text, state):
        """带迟滞的灯：颜色必须连续确认 N 次才切换。"""
        pending = self._q_pending.get(key)
        if pending is None or pending[0] != state:
            self._q_pending[key] = [state, 1]
            if self._q_color.get(key) is None:
                self._q_color[key] = state
        else:
            pending[1] += 1
            if pending[1] >= self.LIVE_CONFIRM and self._q_color.get(key) != state:
                self._q_color[key] = state
        lb = self.q_labels[key]
        lb.configure(text=text, foreground=self.Q_COLORS[self._q_color.get(key, "off")])

    def _update_live_quality(self):
        """实时三灯（信号 / 电平 / SNR），每 1 秒刷新；异常写日志。"""
        try:
            self._update_live_quality_impl()
        except Exception:
            import traceback
            _ui_err_log("[live_quality]\n" + traceback.format_exc())

    def _update_live_quality_impl(self):
        # 实时模式也要把灯区挂上（否则"先开监听、没解过文件"时灯区不出现）
        if not self.quality_frame.winfo_manager():
            self.quality_frame.pack(side="left", padx=(0, 10), before=self.lbl_suspect)
        self.q_labels["decode"].configure(text="")      # 实时不显示解码灯
        self.q_labels["ratio"].configure(text="")       # 实时不显示时序灯
        for k in ("_q_ema", "_q_color", "_q_pending", "_q_last_ts"):
            if not hasattr(self, k):
                setattr(self, k, {} if k != "_q_last_ts" else 0.0)
        now = time.time()
        dt = max(0.2, now - (self._q_last_ts or now))
        self._q_last_ts = now

        met = self._live_metrics()

        # 另外用最近 2 秒判断"现在有没有信号"（10 秒窗口衰减太慢）
        quick = None
        try:
            buf = self.capture.snapshot8k(2.0)                   # 8k 数据，最近 2 秒
            if buf is not None and len(buf) >= int(1.5 * 8000):
                seg2 = np.asarray(buf, dtype=np.float32)
                f2 = dsp.estimate_tone(seg2) if np.max(np.abs(seg2)) > 1e-4 else None
                lv2 = 20.0 * float(np.log10(np.sqrt(np.mean(seg2 ** 2)) + 1e-12))
                ton2 = dsp.tonality(seg2, f0=f2) if f2 else 0.0
                quick = dict(level=lv2, tonality=ton2)
        except Exception:
            quick = None
        # 无信号：全灰，且清空平滑状态（避免沿用旧值）
        chk = quick or met
        silent = (met is None) or (chk is None) or (chk["level"] < -60.0) or (chk["tonality"] < 15.0)
        if silent:
            self._q_silent_n = getattr(self, "_q_silent_n", 0) + 1
            # 信号立即熄灭（相当于静噪关闭）；电平/SNR 连续 3 秒无信号后再熄灭，
            # 避免正常发报的间隙（词间隔）导致闪烁
            self._q_color["signal"] = "off"
            self._q_pending["signal"] = None
            self.q_labels["signal"].configure(text="● " + self.t("q_signal") + " " + self.t("q_none"),
                                             foreground=self.Q_COLORS["off"])
            self.lbl_hold.pack_forget()
            self._live_snapshot = dict(level=self._q_ema.get("level"),
                                       snr=self._q_ema.get("snr"),
                                       signal="无", ts=time.time(), state="nosig")
            # 电平 / SNR 冻结保留（不清空），方便发报结束后回头看
            self.q_labels["decode"].configure(text="")
            return
        self._q_silent_n = 0

        lvl = self._smooth("level", met["level"], dt)
        snr = self._smooth("snr", met["snr"], dt) if met["snr"] is not None else None

        # 信号灯：有信号（绿）/ 弱（黄）
        sig_state = "good" if (lvl is not None and lvl > -50) else "warn"
        self._set_live_light("signal", "● " + self.t("q_signal") + " " + self.t("q_yes"), sig_state)
        self._live_snapshot = dict(level=lvl, snr=snr, signal="有",
                                   ts=time.time(), state="live")

        # 电灯塔
        if lvl is not None:
            st = "good" if lvl >= -28 else ("warn" if lvl >= -33 else "bad")
            self._set_live_light("level", f"● {self.t('q_level')} {lvl:.0f}dB", st)

        # SNR 灯
        if snr is not None:
            st = "good" if snr >= 18 else ("warn" if snr >= 12 else "bad")
            self._set_live_light("snr", f"● {self.t('q_snr')} {snr:.0f}dB", st)
        else:
            self._q_color["snr"] = "off"
            self.q_labels["snr"].configure(text=f"● {self.t('q_snr')} —",
                                               foreground=self.Q_COLORS["off"])

        # 实时模式不显示"解码"灯（文本还在增长，容易误导）
        self.q_labels["decode"].configure(text="")

    # ---------- 信号质量指示灯 ----------
    Q_COLORS = {"good": "#1b8a3a", "warn": "#b07d00", "bad": "#c0392b", "off": "#8a9aab"}

    def _quality_levels(self, res):
        """把各项指标换算成 绿/黄/红 三档（阈值由 71 个样本校准得出）。"""
        lvl = res.get("level_dbfs")
        snr = res.get("snr_db")
        tr = res.get("timing_ratio")
        reg = res.get("regularity")

        if isinstance(lvl, (int, float)):
            lv = "good" if lvl >= -28 else ("warn" if lvl >= -33 else "bad")
        else:
            lv = "off"
        if isinstance(snr, (int, float)):
            sv = "good" if snr >= 18 else ("warn" if snr >= 12 else "bad")
        else:
            sv = "off"
        # 时序比：真实录音在 10–40 WPM 都稳定在 ~1.0（ARRL 实测 1.02–1.04），
        # 所以用绝对区间判定；不做速度归一化（合成素材的偏差不代表真实信号）
        if isinstance(tr, (int, float)):
            if tr > 1.55 or tr < 0.62:
                tv = "bad"
            elif 0.80 <= tr <= 1.35:
                tv = "good"
            else:
                tv = "warn"
        else:
            tv = "off"

        # 文本可信度（参考成熟做法：优先看"解不出来的字符"，而不是词形统计）
        #   · ? 比例：解码器自己都判不出的码元 —— 最可靠的坏信号
        #   · 非 ASCII：拉丁模式下出现西里尔字母 = 串码（俄文模式则不算）
        #   · 单字词比例：只在"没有任何有效词"时才作为依据，
        #     否则会把比赛风格（R R R K K 5NN TU）误判成乱码
        try:
            from .correct import PROWORDS as _PW, CALL_RE as _CALL
        except Exception:
            _PW, _CALL = set(), None
        txt = (res.get("text") or "").strip()
        toks = [w for w in txt.split() if w]
        if len(toks) >= 4:
            single = sum(1 for w in toks if len(w) == 1) / len(toks)
            qrate = txt.count("?") / max(1, len(txt.replace(" ", "")))
            nonascii = sum(1 for ch in txt if ord(ch) > 127) / max(1, len(txt))
            valid = sum(1 for w in toks
                        if (len(w) >= 2 and (w in _PW or (_CALL is not None and _CALL.match(w))))
                        or w in ("K", "R")) / len(toks)
            cyr = (res.get("alphabet") == "ru")
            if cyr:
                nonascii = 0.0
            # 没有任何"有效词"（呼号/常用词/K/R）且单字占多数 → 基本是乱码
            if qrate > 0.12 or nonascii > 0.05 or (valid < 0.20 and single > 0.30):
                cv = "bad"
            elif qrate > 0.03 or nonascii > 0.01 or (valid < 0.4 and single > 0.35):
                cv = "warn"
            else:
                cv = "good"
        else:
            cv = "off"
        if isinstance(reg, (int, float)):
            rv = "good" if reg >= 0.85 else ("warn" if reg >= 0.35 else "bad")
        else:
            rv = "off"
        # "解码"灯：把文本可信度与规律性合起来看（相当于"已锁定 / 未锁定"状态）
        order = {"bad": 2, "warn": 1, "good": 0, "off": 0}
        dv = "bad" if "bad" in (cv, rv) else ("warn" if "warn" in (cv, rv) else "good")
        return {"level": (lv, lvl), "snr": (sv, snr), "decode": (dv, None),
                "ratio": (tv, tr), "reg": (rv, reg), "text": (cv, None)}

    def _update_quality(self, res):
        """文件模式指示灯：电平 / SNR / 时序 / 解码（信号槽清空）。"""
        try:
            self.q_labels["signal"].configure(text="")      # 文件模式不显示"信号"槽
            vals = self._quality_levels(res)
            labels = {
                "level": (self.t("q_level"), "dB"),
                "snr": (self.t("q_snr"), "dB"),
                "ratio": (self.t("q_ratio"), ""),
                "decode": (self.t("q_decode"), ""),
            }
            for key in ("level", "snr", "ratio", "decode"):   # 文件模式四项
                state, value = vals[key]
                name, unit = labels[key]
                lb = self.q_labels[key]
                if key == "decode":
                    word = {"good": self.t("q_good"), "warn": self.t("q_suspect"), "bad": self.t("q_bad")}.get(state, "—")
                    lb.configure(text=f"● {name} {word}", foreground=self.Q_COLORS[state])
                    continue
                if value is None:
                    lb.configure(text=f"● {name} —",
                                 foreground=self.Q_COLORS["off"])
                    continue
                if key in ("level", "snr"):
                    txt = f"● {name} {value:.0f}{unit}"
                else:                                   # 时序：两位小数
                    txt = f"● {name} {value:.2f}"
                lb.configure(text=txt, foreground=self.Q_COLORS[state])
            if not self.quality_frame.winfo_manager():
                self.quality_frame.pack(side="left", padx=(0, 10), before=self.lbl_suspect)
        except Exception:
            import traceback
            _ui_err_log("[_update_quality]\n" + traceback.format_exc())

    def _clear_live_state(self):
        """停止监听 / 复位：清空实时平滑状态与三灯（并把灯区收起）。"""
        self._q_silent_n = 0
        self._q_ema = {}
        self._q_color = {}
        self._q_pending = {}
        self._q_last_ts = 0.0
        self._q_ui_ts = 0.0
        for key in ("signal", "level", "snr", "ratio", "decode"):
            lb = self.q_labels.get(key)
            if lb is not None:
                lb.configure(text="")
        try:
            self.lbl_hold.pack_forget()
        except Exception:
            pass
        self._live_snapshot = None
        try:
            self.quality_frame.pack_forget()      # 全部熄灭后收起，避免留空白
        except Exception:
            pass

    def _clear_quality(self):
        """清空结果时，把指示灯一并收起（避免显示上一次的残留）。"""
        try:
            for lb in getattr(self, "q_labels", {}).values():
                lb.configure(text="")
            hold = getattr(self, "lbl_hold", None)
            if hold is not None:
                hold.pack_forget()
            self._live_snapshot = None
            if hasattr(self, "quality_frame"):
                self.quality_frame.pack_forget()
            self._last_res = None
        except Exception:
            pass

    def _popup_tip(self, lines):
        """生成一个无边框提示窗，返回 Toplevel。"""
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.configure(background="#2b3440")
        tk.Label(tip, text="\n".join(lines), justify="left", anchor="w",
                 background="#2b3440", foreground="#e8eef6",
                 font=("Microsoft YaHei", 9), padx=12, pady=10).pack()
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = self.q_labels["level"].winfo_rootx()
        y = self.q_labels["level"].winfo_rooty() - th - 6
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        x = max(0, min(x, sw - tw - 4))
        y = max(0, min(y, sh - th - 4))
        tip.geometry(f"{tw}x{th}+{x}+{y}")
        return tip

    def _show_quality_tip(self, _evt=None):
        """鼠标悬停：按"灯 → 明细"的顺序列出指标。"""
        try:
            if self._q_tip is not None:
                return

            # ---------- 实时模式 ----------
            if getattr(self.capture, "running", False):
                ema = getattr(self, "_q_ema", {}) or {}
                lv = f"{ema['level']:.1f} dBFS" if "level" in ema else self.t("tip_no_data")
                sn = f"{ema['snr']:.1f} dB" if "snr" in ema else self.t("tip_no_data")
                sig = self.q_labels["signal"].cget("text").split()[-1] if self.q_labels["signal"].cget("text") else "—"
                lines = [
                    self.t("tip_live_title"),
                    "",
                    f"{self.t('q_signal'):<8}{sig}",
                    "        " + self.t("tip_sig_rule"),
                    "",
                    f"{self.t('q_level'):<8}{lv}",
                    "        " + self.t("tip_lvl_rule"),
                    "",
                    f"{self.t('q_snr'):<8}{sn}",
                    "        " + self.t("tip_snr_rule"),
                    "",
                    self.t("tip_live_foot"),
                ]
                self._q_tip = self._popup_tip(lines)
                return

            # ---------- 已停止：用最后一帧实时数据 ----------
            snap = getattr(self, "_live_snapshot", None)
            if snap and snap.get("state") == "stopped" and not getattr(self, "_last_res", None):
                ts = time.strftime("%H:%M:%S", time.localtime(snap.get("ts") or time.time()))
                lv = f"{snap['level']:.1f} dBFS" if snap.get("level") is not None else "—"
                sn = f"{snap['snr']:.1f} dB" if snap.get("snr") is not None else "—"
                lines = [
                    self.t("tip_stop_title").format(ts=ts),
                    "",
                    f"{self.t('q_signal'):<8}{self.t('q_stopped')}",
                    "",
                    f"{self.t('q_level'):<8}{lv}",
                    "        " + self.t("tip_lvl_rule"),
                    "",
                    f"{self.t('q_snr'):<8}{sn}",
                    "        " + self.t("tip_snr_rule"),
                    "",
                    self.t("tip_stop_foot"),
                ]
                self._q_tip = self._popup_tip(lines)
                return

            # ---------- 文件模式 ----------
            cur = getattr(self, "_last_res", None)
            if not cur:
                return
            v = self._quality_levels(cur)
            def num(val, unit="", nd=2):
                return "—" if val is None else f"{val:.{nd}f}{unit}"
            body = (cur.get("text") or "").replace("\n", " ")
            chars = [c for c in body if not c.isspace()]
            q = (chars.count("?") / len(chars) * 100) if chars else 0.0
            nonascii = (sum(1 for c in chars if ord(c) > 127) / len(chars) * 100) if chars else 0.0
            words = [w for w in body.split() if w]
            single = (sum(1 for w in words if len(w) == 1) / len(words) * 100) if words else 0.0
            _word = {"good": self.t("q_good"),
                     "warn": self.t("q_suspect"),
                     "bad": self.t("q_bad")}.get(v["decode"][0], "—")
            lines = [
                self.t("tip_file_title"),
                "",
                f"{self.t('q_level'):<8}{num(v['level'][1], ' dBFS', 1)}",
                "        " + self.t("tip_lvl_rule"),
                "",
                f"{self.t('q_snr'):<8}{num(v['snr'][1], ' dB', 1)}",
                "        " + self.t("tip_snr_rule"),
                "",
                f"{self.t('q_ratio'):<8}{num(v['ratio'][1])}",
                "        " + self.t("tip_ratio_rule"),
                "",
                f"{self.t('q_decode'):<8}{_word}",
                "        " + self.t("tip_decode_rule"),
                "",
                f"{self.t('q_reg'):<8}{num(v['reg'][1])}    {self.t('tip_reg')}",
                f"{self.t('q_text'):<8}" + self.t("tip_textline").format(q=q, single=single, nonascii=nonascii),
            ]
            self._q_tip = self._popup_tip(lines)
        except Exception:
            import traceback
            _ui_err_log("[quality_tip]\n" + traceback.format_exc())
            self._q_tip = None

    def _hide_quality_tip(self, _evt=None):
        try:
            if self._q_tip is not None:
                self._q_tip.destroy()
        except Exception:
            pass
        self._q_tip = None

    def _snr_text(self, snr) -> str:
        """SNR 统一格式：数值 + 语言描述（高/良好/低/极低）。"""
        if snr is None:
            return "—"
        if snr >= 20:
            label = self.t("snr_high")
        elif snr >= 10:
            label = self.t("snr_good")
        elif snr >= 0:
            label = self.t("snr_low")
        else:
            label = self.t("snr_vlow")
        return f"{snr} dB ({label})"

    def _apply_result(self, res: dict):
        self.result = res
        self.show_cyr = (getattr(self, "alphabet", "latin") == "ru")
        self.show_cyr_var.set(self.show_cyr)
        self.btn_cyr.configure(text=self.t("latin") if self.show_cyr else self.t("cyr"))

        # 让红线和输入框跟随实际解码用的音调
        tone = res.get("tone_hz")
        if tone:
            self.selected_hz = float(tone)
            self.ent_hz.delete(0, "end")
            self.ent_hz.insert(0, f"{self.selected_hz:.1f}")
            self._draw_marker()
            self._refresh_play_audio()
            self.after(0, self._build_candidate_bar)

        self._render()
        self._log_click(res)

        # 速度显示：Farnsworth 时同时给出字符速度与有效速度（如 14.8/5.0）
        wpm = res.get("wpm") or "—"
        we = res.get("wpm_eff")
        if isinstance(we, (int, float)) and isinstance(wpm, (int, float)) and abs(we - wpm) > 0.6:
            wpm = f"{wpm:.1f}/{we:.1f}"
        elif isinstance(wpm, (int, float)):
            wpm = f"{wpm:.1f}"
        sus = int(res.get("gate_suspects") or 0)
        snr = res.get("snr_db")
        snr_txt = self._snr_text(snr)
        self._last_res = res
        self._update_quality(res)
        self.status.configure(
            text=(f"{self.t('backend')}: {res.get('backend')} | {self.t('tone')}: {res.get('tone_hz')} Hz | "
                  f"{self.t('wpm')}: {wpm} | SNR: {snr_txt} | {self.t('dur')}: {res.get('duration_s')} s | "
                  f"{self.t('elapsed')}: {res.get('elapsed_s')} s"),
            foreground="#222222")
        self.lbl_suspect.configure(text=f"suspect {sus}" if sus else "")

        if res.get("winner"):
            self.btn_cmp.configure(
                state="normal" if res.get("ctc_text") else "disabled")
            ag = res.get("agreement")
            ag_txt = f"{ag*100:.0f}%" if isinstance(ag, (int, float)) else "—"
            self.status.configure(
                text=(f"{self.t('auto')} → {res['winner'].upper()} | {self.t('agree')} "
                      f"{ag_txt} | SNR: " + self._snr_text(res.get("snr_db"))
                      + f" | {self.t('elapsed')}: {res.get('elapsed_s')} s"))
        else:
            self.btn_cmp.configure(state="disabled")

    def _log_click(self, res: dict):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            p = os.path.join(self.out_dir, "clicklog.csv")
            new = not os.path.exists(p)
            with open(p, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["timestamp", "file", "selected_hz", "tone_hz",
                                "backend", "text", "wpm"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), os.path.basename(self.path),
                            "" if self.selected_hz is None else round(self.selected_hz, 1),
                            res.get("tone_hz", ""), res.get("backend", ""),
                            res.get("text", "")[:200], res.get("wpm", "")])
        except Exception:
            pass

    def _render(self):
        if not self.result:
            return
        detail = self.result.get("detail") or []
        codes = self.result.get("codes") or []

        # 有 detail（CTC）时按字符逐个写入，便于把可疑字符标红
        if detail and not self.show_cyr:
            self.txt.configure(state="normal")
            self.txt.delete("1.0", "end")
            for d in detail:
                ch = d.get("char", "")
                if not ch:
                    continue
                if d.get("suspect"):
                    self.txt.insert("end", ch, "sus")
                else:
                    self.txt.insert("end", ch)
            self.txt.configure(state="disabled")
            self._update_empty_hint()      # 有内容了：隐藏空状态插图
            return

        # 文件模式：始终按"当前字母表"从 codes 重新渲染
        # （拉丁 ⇄ 俄文 来回切换都正确，不会被解码时的字母表锁死）
        if codes:
            text = dsp.codes_to_text(codes, getattr(self, "alphabet", "latin"))
        else:
            text = self.result.get("text", "")
        self._set_text(text)

    def _set_text(self, text: str):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        if text:
            self.txt.insert("1.0", text)
        self.txt.configure(state="disabled")
        self._update_empty_hint()

    def on_txpad(self):
        """打开"发送练习"窗口（CW 发报编辑 + 试听 + 保存）。"""
        from .txpad import TxPad
        win = getattr(self, "_txpad_win", None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_set()
            return
        self._txpad_win = TxPad(self, self.t, lang=self.lang)

    def on_compare(self):
        res = self.result or {}
        if not res.get("winner"):
            return

        w = tk.Toplevel(self)
        w.title(self.t("cmp_title"))
        w.transient(self)
        w.resizable(True, True)

        head = tk.Label(w, anchor="w", padx=14, pady=8, font=("Segoe UI", 10, "bold"),
                        text=(f"{self.t('agree')}: {res['agreement']*100:.0f}%   "
                              f"→ {res['winner'].upper()}  ({res.get('reason', '')})"))
        head.pack(fill="x")

        body = ttk.Frame(w, padding=(14, 0, 14, 8))
        body.pack(fill="both", expand=True)

        blocks = (
            (f"{self.t('be_dsp')}   score {res.get('dsp_score')}", self.t("dsp_desc"),
             res.get("dsp_text", "")),
            (f"{self.t('be_ctc')}   score {res.get('ctc_score')}", self.t("ctc_desc"),
             res.get("ctc_text", "")),
        )
        for title, desc, text in blocks:
            ttk.Label(body, text=title, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 0))
            ttk.Label(body, text=desc, foreground="#5a6b80").pack(anchor="w", pady=(0, 3))

            holder = ttk.Frame(body)
            holder.pack(fill="both", expand=True)
            sb = ttk.Scrollbar(holder, orient="vertical")
            box = tk.Text(holder, height=6, wrap="word", font=("Consolas", 11),
                          yscrollcommand=sb.set)
            sb.configure(command=box.yview)
            sb.pack(side="right", fill="y")
            box.pack(side="left", fill="both", expand=True)
            box.insert("1.0", text)
            box.configure(state="disabled")

        ttk.Button(w, text="OK", command=w.destroy).pack(pady=(0, 10))

        # 居中于主窗口
        w.update_idletasks()
        ww, wh = 780, 520
        x = self.winfo_rootx() + (self.winfo_width() - ww) // 2
        y = self.winfo_rooty() + (self.winfo_height() - wh) // 2
        w.geometry(f"{ww}x{wh}+{max(0, x)}+{max(0, y)}")
        w.update_idletasks()
        # 修正一次：窗口边框/标题栏会让实际中心略偏
        dx = (self.winfo_rootx() + self.winfo_width() // 2) - (w.winfo_rootx() + w.winfo_width() // 2)
        dy = (self.winfo_rooty() + self.winfo_height() // 2) - (w.winfo_rooty() + w.winfo_height() // 2)
        if abs(dx) > 2 or abs(dy) > 2:
            w.geometry(f"+{max(0, w.winfo_x() + dx)}+{max(0, w.winfo_y() + dy)}")
        w.lift()

    def _update_alpha_label(self):
        """状态栏右侧显示当前字母表（文件/实时共用）。"""
        try:
            la = (getattr(self, "alphabet", "latin") == "latin")
            self.lbl_alpha.configure(
                text=f"{self.t('alpha')}: {self.t('alpha_la') if la else self.t('alpha_ru')}")
        except Exception:
            pass

    def tr_cyr_label(self) -> str:
        """勾选框的文字：未开启时显示"西里尔"，开启后显示"拉丁"。"""
        return self.t("latin") if self.show_cyr else self.t("cyr")

    def _live_shown(self, text: str) -> str:
        """实时文本 -> 屏幕显示（纠错 + 可选的西里尔转换，只影响显示）。"""
        shown = text
        if getattr(self, "alphabet", "latin") == "ru" and shown:
            return shown          # 俄文模式：文本本身就是西里尔，不再转换/纠错
        if self.fix_var.get() and shown:
            try:
                # 2026-11-27：实时显示同样停用词替换，只做空格修复
                from .correct import repair_spacing
                shown, _ = repair_spacing(shown)
            except Exception:
                pass
        if self.show_cyr and shown:
            try:
                from .morse_ru import latin_to_cyrillic
                shown = latin_to_cyrillic(shown)
            except Exception:
                pass
        return shown

    def on_cyr(self):
        """在"拉丁 / 俄文"之间切换：不只是换显示，而是换整套字母表。"""
        self.alphabet = "ru" if self.alphabet == "latin" else "latin"
        self.show_cyr = (self.alphabet == "ru")
        self.show_cyr_var.set(self.show_cyr)
        self.btn_cyr.configure(text=self.t("latin") if self.show_cyr else self.t("cyr"))
        self._update_alpha_label()
        # 文件结果：直接用已有的 codes 重新渲染，不需要重新解码
        if self.result:
            self._render()
        # 实时：切换字母表并让解码器重解已有缓冲
        if self.stream_dec is not None:
            try:
                self.stream_dec.alphabet = self.alphabet
                # 用已有缓冲重解一遍，切换字母表后立刻看到对应文字
                buf = self.capture.snapshot8k() if self.capture.running else None
                self._live_raw = ""
                self._live_prefix = ""
                self._set_text("")
                if buf is not None and len(buf) > 8000:
                    self.stream_dec.reseed(buf[-30 * 8000:])
                else:
                    self.stream_dec.reset()
            except Exception:
                pass
        if getattr(self, "_live_raw", ""):
            self._set_text(self._live_shown(self._live_raw))

    def on_save(self):
        if not self.result:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        codes = self.result.get("codes") or []
        if codes:
            text = dsp.codes_to_text(codes, getattr(self, "alphabet", "latin"))
        else:
            text = self.result.get("text", "")
        if not text:
            text = self.txt.get("1.0", "end").strip()
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def on_clear(self):
        self._clear_quality()
        self._set_text("")
        self.result = None
        self.selected_hz = None
        self.ent_hz.delete(0, "end")
        self._draw_spectrum()
        self.status.configure(text=self.t("hint"))
        self.lbl_suspect.configure(text="")


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
