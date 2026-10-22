# -*- coding: utf-8 -*-
"""Figure 1 of the paper (aggregate counts only): a) on-premise pipeline,
b) study flow and validation coverage.

Designed at true print size (17 cm wide) so fonts stay readable in the document.
The counts are the published aggregate figures; no data file is read.

    python scripts/make_flow_figure.py [--out figures/fig1_study_flow.png]
"""
import argparse
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

BLUE_F, BLUE_E = "#E8F0FB", "#1F4E79"   # data boxes
GREEN_F, GREEN_E = "#E7F3EA", "#2C5F2D" # model box
GREY_F, GREY_E = "#F2F2F2", "#8A8A8A"   # excluded / cloud
INK = "#1A1A1A"

fig = plt.figure(figsize=(6.9, 6.6))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

def box(cx, cy, w, h, text, fc=BLUE_F, ec=BLUE_E, fs=7.2, bold_first=False, lw=1.0):
    ax.add_patch(FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                 boxstyle="round,pad=0.008,rounding_size=0.012",
                 facecolor=fc, edgecolor=ec, linewidth=lw))
    if bold_first and "\n" in text:
        first, rest = text.split("\n", 1)
        ax.text(cx, cy + h/2 - 0.014, first, ha="center", va="top",
                fontsize=fs + 0.3, fontweight="bold", color=INK)
        ax.text(cx, cy + h/2 - 0.014 - 0.032, rest, ha="center", va="top",
                fontsize=fs, color=INK, linespacing=1.25)
    else:
        ax.text(cx, cy, text, ha="center", va="center", fontsize=fs,
                color=INK, linespacing=1.25)

def arrow(x0, y0, x1, y1, color=BLUE_E, lw=1.1):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="-|>", color=color, lw=lw,
                                shrinkA=1, shrinkB=1, mutation_scale=11))

# ================= Panel a =================
ax.text(0.015, 0.982, "a", fontsize=12, fontweight="bold", va="top")
ax.text(0.048, 0.980, "On-premise extraction pipeline", fontsize=9.5,
        fontweight="bold", va="top", color=INK)

# hospital perimeter (dashed)
ax.add_patch(FancyBboxPatch((0.02, 0.640), 0.760, 0.300,
             boxstyle="round,pad=0.004,rounding_size=0.015",
             facecolor="none", edgecolor=BLUE_E, linewidth=1.3,
             linestyle=(0, (5, 3))))
ax.text(0.038, 0.9445, "HOSPITAL PERIMETER  ·  clinical text never leaves the institution",
        fontsize=6.6, fontweight="bold", color=BLUE_E, va="center", ha="left",
        bbox=dict(facecolor="white", edgecolor="none", pad=1.5))

ROW1 = 0.868
box(0.100, ROW1, 0.125, 0.080, "Operative\nreport (PDF)", fs=7.2)
box(0.282, ROW1, 0.155, 0.088, "Text extraction\n(PDF to plain text)", fs=6.8)
box(0.512, ROW1, 0.215, 0.100,
    "LLM classifier\nfrozen prompt v6.2 (≈14k tokens)\n4-bit weights, two GPUs\nno fine-tuning",
    fc=GREEN_F, ec=GREEN_E, fs=6.4, bold_first=True)
box(0.705, ROW1, 0.125, 0.088, "Strict JSON\n5 fields +\nconfidence", fs=6.8)

arrow(0.166, ROW1, 0.201, ROW1)
arrow(0.363, ROW1, 0.401, ROW1)
arrow(0.623, ROW1, 0.639, ROW1)

ROW2 = 0.708
box(0.585, ROW2, 0.270, 0.072,
    "Per field: decision, verbatim quote,\nconfidence high / medium / low,\nor “undecidable” (abstention)",
    fs=6.6)
arrow(0.705, ROW1 - 0.047, 0.705, ROW2 + 0.040)

box(0.175, ROW2, 0.250, 0.080,
    "Swissnoso form pre-filled\nlow confidence & abstentions\nrouted to IPC nurse", fs=6.8, bold_first=True)
arrow(0.446, ROW2, 0.304, ROW2)

# crossed-out cloud (outside perimeter)
ax.add_patch(FancyBboxPatch((0.820, 0.830), 0.160, 0.082,
             boxstyle="round,pad=0.006,rounding_size=0.014",
             facecolor=GREY_F, edgecolor=GREY_E, linewidth=1.0))
ax.text(0.900, 0.886, "Cloud LLM APIs", ha="center", fontsize=7.0, color="#666666")
ax.text(0.900, 0.858, "external vendors", ha="center", fontsize=6.2, color="#888888")
ax.plot([0.827, 0.973], [0.836, 0.906], color="#C0392B", lw=1.6, alpha=0.9)
ax.plot([0.827, 0.973], [0.906, 0.836], color="#C0392B", lw=1.6, alpha=0.9)
ax.text(0.900, 0.806, "not used", ha="center", fontsize=6.6,
        color="#C0392B", fontweight="bold")

# ================= Panel b =================
ax.text(0.015, 0.606, "b", fontsize=12, fontweight="bold", va="top")
ax.text(0.048, 0.604, "Study flow and validation coverage", fontsize=9.5,
        fontweight="bold", va="top", color=INK)

# left: consort flow
box(0.245, 0.532, 0.420, 0.072,
    "2050 operations under Swissnoso SSI surveillance\nGeneva University Hospitals, Oct 2022 – Sep 2024",
    fs=7.0, bold_first=True)

box(0.125, 0.402, 0.190, 0.098,
    "Development set\nn = 391 (19%)\nprompt engineering\n11 model × prompt configs", fs=6.6, bold_first=True)
box(0.365, 0.402, 0.190, 0.098,
    "Validation set\nn = 1659 (81%)\nblinded, untouched\nuntil prompt frozen", fs=6.6, bold_first=True)
arrow(0.185, 0.496, 0.135, 0.453)
arrow(0.305, 0.496, 0.355, 0.453)

box(0.365, 0.268, 0.200, 0.068,
    "1768 operative report\ndocuments retrieved", fs=6.8)
arrow(0.365, 0.352, 0.365, 0.304)

box(0.365, 0.135, 0.200, 0.062,
    "Inference:\n4 models × frozen\nprompt v6.2", fc=GREEN_F, ec=GREEN_E, fs=6.8)
arrow(0.365, 0.233, 0.365, 0.168)

ax.text(0.030, 0.300,
        "Excluded per model:\n"
        "• unparseable outputs\n"
        "   (no JSON, invalid\n"
        "   JSON, schema)\n"
        "• no linked target\n"
        "   operation\n"
        "   (patient + date)",
        fontsize=6.4, color="#555555", va="top", ha="left", linespacing=1.35)

arrow(0.467, 0.135, 0.512, 0.135, color=GREEN_E)

# right: coverage table
TX0, TX1 = 0.520, 0.985
ax.text((TX0 + TX1) / 2, 0.532, "Validation coverage per model",
        fontsize=8.0, fontweight="bold", ha="center", color=INK)

col_model, col_parse, col_eval = TX0 + 0.004, 0.760, 0.935
rows_y = [0.472, 0.424, 0.379, 0.334, 0.289]
ax.add_patch(Rectangle((TX0, rows_y[0] - 0.020), TX1 - TX0, 0.056,
             facecolor="#DCE7F5", edgecolor="none"))
ax.text(col_model, rows_y[0] + 0.006, "Model", fontsize=6.9, fontweight="bold",
        ha="left", va="center")
ax.text(col_parse, rows_y[0] + 0.006, "Parseable\noutputs", fontsize=6.9,
        fontweight="bold", ha="center", va="center", linespacing=1.1)
ax.text(col_eval, rows_y[0] + 0.006, "Evaluable\noperations", fontsize=6.9,
        fontweight="bold", ha="center", va="center", linespacing=1.1)
data = [
    ("MedGemma-27B",  "1766/1768 (99.9%)",  "1366"),
    ("Ministral-14B", "1767/1768 (99.9%)",  "1367"),
    ("Apertus-8B",    "1761/1768 (99.6%)",  "1360"),
    ("Qwen3.6-35B-A3B",   "1757/1768 (99.4%)",  "1358"),
]
for (m, p, n), y in zip(data, rows_y[1:]):
    ax.text(col_model, y, m, fontsize=6.9, ha="left", va="center")
    ax.text(col_parse, y, p, fontsize=6.9, ha="center", va="center")
    ax.text(col_eval, y, n, fontsize=6.9, ha="center", va="center", fontweight="bold")
ax.plot([TX0, TX1], [rows_y[0] + 0.036, rows_y[0] + 0.036], color=BLUE_E, lw=1.0)
ax.plot([TX0, TX1], [rows_y[0] - 0.020, rows_y[0] - 0.020], color=BLUE_E, lw=1.0)
for y in [r - 0.0225 for r in rows_y[1:-1]]:
    ax.plot([TX0, TX1], [y, y], color="#C9D4E4", lw=0.6)
ax.plot([TX0, TX1], [rows_y[-1] - 0.0225, rows_y[-1] - 0.0225], color=BLUE_E, lw=1.0)

ax.text(TX0, 0.245,
        "Consolidated campaign: initial pass plus one resubmission of\n"
        "the documents that failed, same model and prompt (Table 2).",
        fontsize=6.4, color="#555555", va="top", linespacing=1.3)

ax.text(TX0, 0.185,
        "Evaluable operations: parseable predictions linked to\n"
        "the operation they describe, compared with the reference\n"
        "standard on five variables (procedure category,\n"
        "contamination class, implant, minimally invasive\n"
        "approach, colorectal cancer).",
        fontsize=6.4, color="#555555", va="top", linespacing=1.35)

ap = argparse.ArgumentParser(description="Draw Figure 1 (pipeline and study flow).")
ap.add_argument("--out", default=os.path.join("figures", "fig1_study_flow.png"))
out = ap.parse_args().out
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
fig.savefig(out, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.06)
print("saved", out)
