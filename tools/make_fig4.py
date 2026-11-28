"""fig4：系统框图（纵向单栏，手排）。

尺寸 3.5 x 7.4 in @ 300 dpi，纯黑白，用于论文单栏。
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = os.path.join(r"F:\QQBot\cwdecoder", "docs", "images", "fig4_system.png")

W, H = 3.5, 7.4
fig, ax = plt.subplots(figsize=(W, H), dpi=300)
ax.set_xlim(0, W)
ax.set_ylim(0, H)
ax.axis("off")

FILL = "#fafafa"
EDGE = "#3a3a3a"


def box(x, y, w, h, text, fs=7.6, bold=False):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle="round,pad=0.012,rounding_size=0.06",
                                linewidth=0.9, edgecolor=EDGE, facecolor=FILL))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, fontweight="bold" if bold else "normal", linespacing=1.25)


def arrow(p1, p2, lw=0.9):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=9,
                                 linewidth=lw, color=EDGE,
                                 shrinkA=0, shrinkB=0))


def caption(x, y, text):
    ax.text(x, y, text, fontsize=7.4, color="#555555", ha="left", va="bottom")


# ---------------- 输入 ----------------
box(0.15, 6.98, 1.50, 0.32, "WAV file")
box(1.85, 6.98, 1.50, 0.32, "Live audio\n(loopback / mic)", fs=6.9)
box(0.80, 6.38, 1.90, 0.34, "Resample → 8 kHz mono", fs=7.2)

arrow((0.90, 6.98), (1.20, 6.72))
arrow((2.60, 6.98), (2.30, 6.72))

# ---------------- 两条并行链路 ----------------
BW, BH, GAP = 1.52, 0.40, 0.13
TOP = 5.80
dsp_x, ctc_x = 0.12, 1.86

caption(dsp_x, TOP + 0.06, "DSP chain")
caption(ctc_x, TOP + 0.06, "CTC chain")

dsp_steps = ["1  Band-pass 180 Hz", "2  Envelope", "3  Percentile threshold",
             "4  Mark / space runs", "5  Unit duration", "6  Gap clustering",
             "7  Morse lookup"]
dsp_y = []
for i, s in enumerate(dsp_steps):
    y = TOP - i * (BH + GAP) - BH
    dsp_y.append(y)
    box(dsp_x, y, BW, BH, s, fs=6.8)

ctc_steps = ["1  Frame spectrum", "2  Acoustic model (ONNX)", "3  CTC decode"]
ctc_y = []
for i, s in enumerate(ctc_steps):
    y = TOP - i * (BH + GAP) - BH
    ctc_y.append(y)
    box(ctc_x, y, BW, BH, s, fs=6.5)

arrow((1.35, 6.38), (1.35, dsp_y[0] + BH))
arrow((2.35, 6.38), (2.35, ctc_y[0] + BH))

for i in range(len(dsp_y) - 1):
    arrow((dsp_x + BW / 2, dsp_y[i]), (dsp_x + BW / 2, dsp_y[i + 1] + BH))
for i in range(len(ctc_y) - 1):
    arrow((ctc_x + BW / 2, ctc_y[i]), (ctc_x + BW / 2, ctc_y[i + 1] + BH))

# ---------------- 选择器 ----------------
SEL_Y, SEL_H = 1.62, 0.40
box(0.80, SEL_Y, 1.90, SEL_H, "Selector (agreement + SNR)", fs=7.2, bold=True)
arrow((dsp_x + BW / 2, dsp_y[-1]), (1.20, SEL_Y + SEL_H))
arrow((ctc_x + BW / 2, ctc_y[-1]), (2.30, SEL_Y + SEL_H))

# ---------------- 输出 ----------------
OUT1_Y = 1.00
box(0.12, OUT1_Y, BW, 0.36, "Spacing repair\n(no word substitution)", fs=6.4)
box(ctc_x, OUT1_Y, BW, 0.36, "Text output", fs=7.2)
arrow((1.40, SEL_Y), (0.90, OUT1_Y + 0.36))
arrow((0.12 + BW, OUT1_Y + 0.18), (ctc_x, OUT1_Y + 0.18))

OUT2_Y = 0.34
box(0.12, OUT2_Y, BW, 0.36, "Quality indicator\n(level / SNR / timing)", fs=6.4)
box(ctc_x, OUT2_Y, BW, 0.36, "GUI\n(spectrogram + lights)", fs=6.8)
arrow((1.95, SEL_Y), (0.90, OUT2_Y + 0.36))
arrow((0.12 + BW, OUT2_Y + 0.18), (ctc_x, OUT2_Y + 0.18))
arrow((ctc_x + BW / 2, OUT1_Y), (ctc_x + BW / 2, OUT2_Y + 0.36))

fig.subplots_adjust(0, 0, 1, 1)
fig.savefig(OUT, dpi=300, facecolor="white")
print("已生成:", OUT, os.path.getsize(OUT) // 1024, "KB")
