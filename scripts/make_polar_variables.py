# -*- coding: utf-8 -*-
"""
Polar (radar) charts of per-variable performance of the four models.

Source of the numbers: report.md written by src/analyze.py on the validation
split (prompt 6.2, consolidated campaign: initial pass plus one resubmission
of the failed documents, 1768 documents per model). Aggregate metrics only;
no data file is read.

    python scripts/make_polar_variables.py [--out-dir figures]

Outputs:
    polar_models_by_variable.png            models overlaid, weighted F1 and kappa
    polar_models_by_variable_highconf.png   same, high-confidence predictions only
    polar_variables_data.csv                table view of every plotted value
"""
from pathlib import Path
import argparse
import csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

VARIABLES = [
    "Procedure\ncategory",
    "Wound\ncontamination\nclass",
    "Implant",
    "Minimally invasive\napproach",
    "Colorectal\ncancer",
]
VAR_FLAT = ["Procedure category", "Wound contamination class", "Implant",
            "Minimally invasive approach", "Colorectal cancer"]

# models in descending overall order, named as in the manuscript
MODELS = ["MedGemma-27B", "Qwen3.6-35B-A3B", "Ministral-14B", "Apertus-8B"]

# categorical slots 1, 2, 3, 7 - validated all-pairs, light mode (CVD dE 9.2, normal dE 16.3)
MODEL_COLORS = dict(zip(MODELS, ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7"]))

# (accuracy, weighted F1, macro F1, Cohen kappa, N)
ALL = {
    "MedGemma-27B": {
        "Procedure category":          (0.8338, 0.8553, 0.3427, 0.7780, 1366),
        "Wound contamination class":   (0.8609, 0.8579, 0.5531, 0.7554, 1366),
        "Implant":                     (0.9056, 0.9097, 0.8774, 0.7563, 1366),
        "Minimally invasive approach": (0.7508, 0.7372, 0.7256, 0.4669, 1264),
        "Colorectal cancer":           (0.8742, 0.8739, 0.8738, 0.7483, 485),
    },
    "Qwen3.6-35B-A3B": {
        "Procedure category":          (0.7990, 0.8179, 0.4077, 0.7369, 1358),
        "Wound contamination class":   (0.8100, 0.8339, 0.5201, 0.6816, 1358),
        "Implant":                     (0.9153, 0.9182, 0.8880, 0.7767, 1358),
        "Minimally invasive approach": (0.7716, 0.7669, 0.7589, 0.5215, 1248),
        "Colorectal cancer":           (0.9298, 0.9298, 0.9298, 0.8598, 456),
    },
    "Ministral-14B": {
        "Procedure category":          (0.7688, 0.8103, 0.4438, 0.7017, 1367),
        "Wound contamination class":   (0.7696, 0.8072, 0.5200, 0.6224, 1367),
        "Implant":                     (0.9026, 0.9068, 0.8731, 0.7477, 1365),
        "Minimally invasive approach": (0.8142, 0.8118, 0.8063, 0.6142, 1265),
        "Colorectal cancer":           (0.9226, 0.9204, 0.9097, 0.8203, 297),
    },
    "Apertus-8B": {
        "Procedure category":          (0.3228, 0.3706, 0.2176, 0.2497, 1360),
        "Wound contamination class":   (0.4669, 0.5548, 0.3313, 0.3030, 1360),
        "Implant":                     (0.7662, 0.7845, 0.7329, 0.4879, 1360),
        "Minimally invasive approach": (0.7141, 0.6845, 0.6666, 0.3736, 1259),
        "Colorectal cancer":           (0.6395, 0.5851, 0.5808, 0.2643, 430),
    },
}

HIGH = {
    "MedGemma-27B": {
        "Procedure category":          (0.8356, 0.8569, 0.3728, 0.7805, 1350),
        "Wound contamination class":   (0.8819, 0.8701, 0.5461, 0.7847, 1313),
        "Implant":                     (0.9062, 0.9104, 0.8782, 0.7580, 1365),
        "Minimally invasive approach": (0.7512, 0.7382, 0.7274, 0.4699, 1238),
        "Colorectal cancer":           (0.8879, 0.8876, 0.8875, 0.7754, 464),
    },
    "Qwen3.6-35B-A3B": {
        "Procedure category":          (0.8247, 0.8333, 0.4150, 0.7678, 1312),
        "Wound contamination class":   (0.8828, 0.8780, 0.5471, 0.7862, 1229),
        "Implant":                     (0.9153, 0.9182, 0.8880, 0.7767, 1358),
        "Minimally invasive approach": (0.7676, 0.7632, 0.7562, 0.5160, 1205),
        "Colorectal cancer":           (0.9368, 0.9368, 0.9368, 0.8737, 443),
    },
    "Ministral-14B": {
        "Procedure category":          (0.8226, 0.8421, 0.4674, 0.7665, 1263),
        "Wound contamination class":   (0.8663, 0.8755, 0.5567, 0.7464, 1174),
        "Implant":                     (0.9115, 0.9158, 0.8841, 0.7700, 1344),
        "Minimally invasive approach": (0.8150, 0.8124, 0.8054, 0.6124, 1157),
        "Colorectal cancer":           (0.9307, 0.9291, 0.9207, 0.8420, 274),
    },
    "Apertus-8B": {
        "Procedure category":          (0.4308, 0.4646, 0.2608, 0.3341, 1019),
        "Wound contamination class":   (0.7898, 0.7389, 0.4285, 0.6594, 804),
        "Implant":                     (0.7667, 0.7850, 0.7335, 0.4889, 1359),
        "Minimally invasive approach": (0.9296, 0.9301, 0.9250, 0.8501, 611),
        "Colorectal cancer":           (0.6395, 0.5851, 0.5808, 0.2643, 430),
    },
}

INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#8a8985"
ANGLES = np.linspace(0, 2 * np.pi, len(VARIABLES), endpoint=False)
LOOP = np.concatenate([ANGLES, ANGLES[:1]])


def style_axis(ax):
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)
    ax.set_ylim(0, 1)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_yticklabels(["0.2", "0.4", "0.6", "0.8", "1.0"], fontsize=7, color=MUTED)
    for lbl in ax.get_yticklabels():
        lbl.set_bbox(dict(facecolor="#fcfcfb", edgecolor="none", pad=1.0, alpha=0.85))
    ax.set_rlabel_position(180 / len(VARIABLES))
    ax.set_xticks(ANGLES)
    ax.set_xticklabels(VARIABLES, fontsize=8.5, color=INK2)
    ax.tick_params(axis="x", pad=12)
    ax.grid(color="#d8d7d2", linewidth=0.7)
    ax.spines["polar"].set_color("#d8d7d2")
    ax.spines["polar"].set_linewidth(0.7)
    ax.set_facecolor("#fcfcfb")


def draw_series(ax, values, color, label):
    v = np.concatenate([values, values[:1]])
    ax.plot(LOOP, v, color=color, linewidth=2.0, label=label, zorder=3,
            solid_capstyle="round")
    ax.plot(LOOP, v, "o", color=color, markersize=5,
            markeredgecolor="#fcfcfb", markeredgewidth=1.2, zorder=4)


def overlay_figure(data, subtitle, outfile):
    metrics = [("Weighted F1", 1), ("Cohen kappa", 3)]
    fig, axes = plt.subplots(1, 2, figsize=(12.4, 6.8), dpi=200,
                             subplot_kw=dict(projection="polar"))
    fig.patch.set_facecolor("#fcfcfb")
    for ax, (metric, idx) in zip(axes, metrics):
        style_axis(ax)
        for model in MODELS:
            vals = np.array([data[model][v][idx] for v in VAR_FLAT])
            draw_series(ax, vals, MODEL_COLORS[model], model)
        ax.set_title(metric, fontsize=13, fontweight="bold", color=INK, pad=18)

    handles = [Line2D([], [], color=MODEL_COLORS[m], linewidth=2.0, marker="o",
                      markersize=5, markeredgecolor="#fcfcfb", label=m)
               for m in MODELS]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False,
               fontsize=10, bbox_to_anchor=(0.5, 0.025), labelcolor=INK2)
    fig.suptitle("Per-variable performance of the four models", fontsize=15,
                 fontweight="bold", color=INK, y=0.975)
    fig.text(0.5, 0.928, subtitle, ha="center", fontsize=9.5, color=INK2)
    fig.subplots_adjust(top=0.78, bottom=0.15, left=0.09, right=0.91, wspace=0.38)
    fig.savefig(outfile, facecolor=fig.get_facecolor())
    plt.close(fig)
    print("wrote", outfile.name)


def write_table(outfile):
    with open(outfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["subset", "model", "variable", "accuracy", "f1_weighted",
                    "f1_macro", "cohen_kappa", "n"])
        for subset, data in (("all predictions", ALL), ("high confidence", HIGH)):
            for model in MODELS:
                for var in VAR_FLAT:
                    acc, f1w, f1m, kap, n = data[model][var]
                    w.writerow([subset, model, var, "%.4f" % acc, "%.4f" % f1w,
                                "%.4f" % f1m, "%.4f" % kap, n])
    print("wrote", outfile.name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Draw the per-variable polar charts.")
    ap.add_argument("--out-dir", type=Path, default=Path("figures"))
    out_dir = ap.parse_args().out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    overlay_figure(
        ALL,
        "Validation split (1768 documents), prompt 6.2, all predictions",
        out_dir / "polar_models_by_variable.png",
    )
    overlay_figure(
        HIGH,
        "Validation split, prompt 6.2, high-confidence predictions only",
        out_dir / "polar_models_by_variable_highconf.png",
    )
    write_table(out_dir / "polar_variables_data.csv")
