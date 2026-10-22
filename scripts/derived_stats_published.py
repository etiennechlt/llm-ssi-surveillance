# -*- coding: utf-8 -*-
"""Derived statistics of the paper, recomputed from the PUBLISHED aggregate counts.

The confusion-matrix cells, accuracies and denominators below are the
aggregate values reported in the paper (validation split, prompt 6.2,
consolidated campaign, 1768 documents per model). They were produced by
src/derived_stats.py on the study data, which cannot be shared. This script
lets a reader recompute the Wilson 95% confidence intervals, sensitivities,
specificities and predictive values without access to the data.

    python scripts/derived_stats_published.py
"""
import math

def wilson(p_hat, n, z=1.959964):
    if n == 0:
        return (float('nan'), float('nan'))
    denom = 1 + z**2 / n
    centre = (p_hat + z**2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p_hat * (1 - p_hat) / n + z**2 / (4 * n**2))
    return (centre - half, centre + half)

def fmt(p, n):
    lo, hi = wilson(p, n)
    return f"{p:.3f} (95% CI {lo:.3f}-{hi:.3f})"

def binstats(name, tn, fp, fn, tp):
    n = tn + fp + fn + tp
    pos = tp + fn
    neg = tn + fp
    sens = tp / pos if pos else float('nan')
    spec = tn / neg if neg else float('nan')
    ppv = tp / (tp + fp) if tp + fp else float('nan')
    npv = tn / (tn + fn) if tn + fn else float('nan')
    acc = (tp + tn) / n
    prev = pos / n
    print(f"{name}: n={n} prev={prev:.3f}")
    print(f"   acc  {fmt(acc, n)}")
    print(f"   sens {fmt(sens, pos)}  [tp={tp}/pos={pos}]")
    print(f"   spec {fmt(spec, neg)}  [tn={tn}/neg={neg}]")
    print(f"   ppv  {fmt(ppv, tp+fp)}   npv {fmt(npv, tn+fn)}")

print("=" * 70)
print("IMPLANT (positive = implant)")
binstats("Qwen3.6-35B-A3B", 957, 93, 22, 286)
binstats("MedGemma-27B ", 946, 113, 16, 291)
binstats("Ministral-14B", 945, 114, 19, 287)
binstats("Apertus-8B  ", 761, 291, 27, 281)

print("=" * 70)
print("SCOPY (positive = minimally invasive)")
binstats("Ministral-14B", 643, 78, 157, 387)
binstats("Qwen3.6-35B-A3B", 625, 89, 196, 338)
binstats("MedGemma-27B ", 666, 54, 261, 283)
binstats("Apertus-8B  ", 687, 32, 328, 212)

print("=" * 70)
print("COLORECTAL CANCER (positive = oui)")
binstats("Qwen3.6-35B-A3B", 211, 24, 8, 213)
binstats("Ministral-14B", 81, 21, 2, 193)
binstats("MedGemma-27B ", 198, 43, 18, 226)
binstats("Apertus-8B  ", 57, 152, 3, 218)

print("=" * 70)
print("ACCURACY WILSON CIs (headline values of the paper)")
for label, acc, n in [
    ("categorie MedGemma", 0.8338, 1366),
    ("categorie Qwen", 0.7990, 1358),
    ("categorie Ministral", 0.7688, 1367),
    ("categorie Apertus", 0.3228, 1360),
    ("contamination MedGemma", 0.8609, 1366),
    ("contamination Qwen", 0.8100, 1358),
    ("contamination Ministral", 0.7696, 1367),
    ("contamination Apertus", 0.4669, 1360),
    ("implant Qwen", 0.9153, 1358),
    ("implant MedGemma", 0.9056, 1366),
    ("implant Ministral", 0.9026, 1365),
    ("implant Apertus", 0.7662, 1360),
    ("scopy Ministral", 0.8142, 1265),
    ("scopy Qwen", 0.7716, 1248),
    ("scopy MedGemma", 0.7508, 1264),
    ("scopy Apertus", 0.7141, 1259),
    ("cancer Qwen", 0.9298, 456),
    ("cancer Ministral", 0.9226, 297),
    ("cancer MedGemma", 0.8742, 485),
    ("cancer Apertus", 0.6395, 430),
    ("conf-haute contamination Qwen", 0.8828, 1229),
    ("conf-haute contamination MedGemma", 0.8819, 1313),
    ("conf-haute categorie MedGemma", 0.8356, 1350),
]:
    print(f"   {label:38s} {fmt(acc, n)}")

print("=" * 70)
print("Ground-truth class distributions (validation, MedGemma-evaluable subset)")
proc = {"COLO (6)": 441, "RECTO (281)": 91, "Other cardiac (43)": 180,
        "CABG mammary (44)": 22, "CABG venous/mixed (45)": 158, "Laminectomy (50)": 474}
tot = sum(proc.values())
for k, v in proc.items():
    print(f"   {k:24s} {v:5d} ({100*v/tot:.1f}%)")
print(f"   total {tot}")
conta = {"I": 818, "II": 357, "III": 109, "IV": 82}
tot = sum(conta.values())
for k, v in conta.items():
    print(f"   Altemeier {k:3s} {v:5d} ({100*v/tot:.1f}%)")

print("=" * 70)
print("Label-space harmonisation for the procedure category (46/47 mapped to 43)")
for label, before, after in [("MedGemma-27B", 0.8338, 0.9136),
                             ("Qwen3.6-35B", 0.7990, 0.9146),
                             ("Ministral-14B", 0.7688, 0.8676),
                             ("Apertus-8B", 0.3228, 0.4434)]:
    print(f"   {label:14s} {before:.4f} -> {after:.4f}")
print("   reference category 43: n=180 operations")

print("=" * 70)
print("Prompt sizes (prompts/system_prompt_reasoning_v6_2.md, prompts/user_prompt_reasoning_v6.md)")
print("   system v6.2: 54331 chars ~ %d tokens (chars/4)" % (54331 // 4))
print("   user v6: 1602 chars ~ %d tokens" % (1602 // 4))
