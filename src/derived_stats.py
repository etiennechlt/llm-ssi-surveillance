# -*- coding: utf-8 -*-
"""Derived statistics reported in the paper, recomputed from the runs.

Prints aggregate figures only (no report content):
  - confusion matrices per variable and run;
  - sensitivity, specificity, PPV and NPV for the binary variables, with
    Wilson 95% confidence intervals;
  - accuracy with Wilson 95% CI, all predictions and high-confidence only;
  - accuracy gain and coverage of the high-confidence filter;
  - distribution of the reference standard on the evaluable subset of one run;
  - post-hoc harmonisation of the procedure label space (46/47 mapped to 43);
  - size of the prompts.

Usage
-----
    python src/derived_stats.py \
        --run "Model-A=runs/model_a" --run "Model-B=runs/model_b" \
        --documents data/documents.csv --operations data/operations.csv \
        [--forms data/forms.csv] [--reference-run Model-A] > derived_stats.txt
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from analyze import (
    APPROACH, CANCER, CONTA, IMPLANT, PROC, TARGET_ORDER,
    add_reference_args, build_runs_and_reference, evaluate_run, load_run,
    merge_with_reference,
)
from metrics import fmt_ci
from parsing import norm_oui_non, normalize_proc, to_bool_scopie

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = [("system v6.2", REPO_ROOT / "prompts" / "system_prompt_reasoning_v6_2.md"),
           ("user, as sent", REPO_ROOT / "prompts" / "user_template_as_sent.md"),
           ("user v6 file", REPO_ROOT / "prompts" / "user_prompt_reasoning_v6.md")]

PROC_NAMES = {"6": "Colon (6)", "281": "Rectum (281)", "43": "Other cardiac (43)",
              "44": "CABG mammary (44)", "45": "CABG venous/mixed (45)", "50": "Laminectomy (50)"}


def binstats(name, tn, fp, fn, tp):
    n = tn + fp + fn + tp
    pos, neg = tp + fn, tn + fp
    sens = tp / pos if pos else float("nan")
    spec = tn / neg if neg else float("nan")
    ppv = tp / (tp + fp) if tp + fp else float("nan")
    npv = tn / (tn + fn) if tn + fn else float("nan")
    acc = (tp + tn) / n if n else float("nan")
    prev = pos / n if n else float("nan")
    print(f"{name}: n={n} prev={prev:.3f}  [tn={tn} fp={fp} fn={fn} tp={tp}]")
    print(f"   acc  {fmt_ci(acc, n)}")
    print(f"   sens {fmt_ci(sens, pos)}  [tp={tp}/pos={pos}]")
    print(f"   spec {fmt_ci(spec, neg)}  [tn={tn}/neg={neg}]")
    print(f"   ppv  {fmt_ci(ppv, tp + fp)}   npv {fmt_ci(npv, tn + fn)}")


def rule(title):
    print("=" * 78)
    print(title)
    print("=" * 78)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_reference_args(ap)
    ap.add_argument("--reference-run", default=None,
                    help="run whose evaluable subset is used to describe the reference "
                         "standard (default: first --run)")
    args = ap.parse_args()

    runs, df_sn, bridge = build_runs_and_reference(args)
    order = list(runs)
    ref_label = args.reference_run or order[0]
    if ref_label not in runs:
        raise SystemExit(f"--reference-run: unknown label {ref_label}")

    merged, metrics_all, metrics_high = {}, {}, {}
    for label, info in runs.items():
        df_pred, _ = load_run(info["dir"], source_dir=info.get("source"))
        df_eval = merge_with_reference(df_pred, bridge, df_sn)
        merged[label] = df_eval
        metrics_all[label] = evaluate_run(df_eval, conf_filter=False) if not df_eval.empty else {}
        metrics_high[label] = evaluate_run(df_eval, conf_filter=True) if not df_eval.empty else {}

    # -------------------------------------------------------------------------
    rule("CONFUSION MATRICES (all predictions; rows = reference, columns = predicted)")
    for target in TARGET_ORDER:
        print(f"\n--- {target} ---")
        for label in order:
            m = metrics_all[label].get(target)
            if not m:
                continue
            cm = pd.DataFrame(m["confusion_matrix"], index=m["display_labels"],
                              columns=m["display_labels"])
            print(f"\n{label}  acc={m['accuracy']:.4f}  n={m['n_samples']}")
            print(cm.to_string())

    # -------------------------------------------------------------------------
    rule("BINARY VARIABLES (sensitivity, specificity, PPV, NPV)")
    print("\nIMPLANT (positive = implant placed), labels [No, Yes]")
    for label in order:
        m = metrics_all[label].get(IMPLANT)
        if m:
            cm = m["confusion_matrix"]
            binstats(f"{label:16s}", tn=cm[0][0], fp=cm[0][1], fn=cm[1][0], tp=cm[1][1])

    print("\nMINIMALLY INVASIVE APPROACH (positive = minimally invasive), labels [Yes, No]")
    for label in order:
        m = metrics_all[label].get(APPROACH)
        if m:
            cm = m["confusion_matrix"]
            binstats(f"{label:16s}", tn=cm[1][1], fp=cm[1][0], fn=cm[0][1], tp=cm[0][0])

    print("\nCOLORECTAL CANCER (positive = cancer), labels [No, Yes]")
    for label in order:
        m = metrics_all[label].get(CANCER)
        if m:
            cm = m["confusion_matrix"]
            binstats(f"{label:16s}", tn=cm[0][0], fp=cm[0][1], fn=cm[1][0], tp=cm[1][1])

    # -------------------------------------------------------------------------
    rule("ACCURACY WITH WILSON 95% CI")
    for target in TARGET_ORDER:
        print(f"\n--- {target} ---")
        for label in order:
            for tag, store in (("all       ", metrics_all), ("high conf.", metrics_high)):
                m = store[label].get(target)
                if m:
                    print(f"   {label:16s} {tag} {fmt_ci(m['accuracy'], m['n_samples'])}  "
                          f"n={m['n_samples']}  F1w={m['f1_weighted']:.4f}  "
                          f"kappa={m['kappa']:.4f}  kappa_quad={m['weighted_kappa']:.4f}")

    # -------------------------------------------------------------------------
    rule("HIGH-CONFIDENCE FILTER: ACCURACY GAIN (percentage points) AND COVERAGE")
    rows = []
    for target in TARGET_ORDER:
        for label in order:
            a, h = metrics_all[label].get(target), metrics_high[label].get(target)
            if a and h:
                rows.append({
                    "variable": target, "run": label,
                    "acc_all": round(a["accuracy"], 4), "n_all": a["n_samples"],
                    "acc_high": round(h["accuracy"], 4), "n_high": h["n_samples"],
                    "delta_pts": round(100 * (h["accuracy"] - a["accuracy"]), 1),
                    "coverage_%": round(100 * h["n_samples"] / a["n_samples"], 1),
                })
    df_gain = pd.DataFrame(rows)
    if not df_gain.empty:
        print(df_gain.to_string(index=False))
        print(f"\nLargest gain over all variables and runs: {df_gain['delta_pts'].max()} points")
        print("\n-- Best run per variable on high-confidence predictions --")
        for target in TARGET_ORDER:
            sub = df_gain[df_gain["variable"] == target].sort_values("acc_high", ascending=False)
            if sub.empty:
                continue
            best = sub.iloc[0]
            print(f"   {target:28s} {best['run']:16s} acc_high={best['acc_high']:.3f}  "
                  f"coverage={best['coverage_%']:.1f}%")

    # -------------------------------------------------------------------------
    rule(f"REFERENCE STANDARD DISTRIBUTION (evaluable subset of {ref_label})")
    d = merged[ref_label]
    if not d.empty:
        proc = d["procedure"].apply(normalize_proc).value_counts()
        tot = int(proc.sum())
        for k in ["50", "6", "43", "45", "281", "44"]:
            v = int(proc.get(k, 0))
            print(f"   {PROC_NAMES[k]:26s} {v:5d} ({100 * v / tot:.1f}%)")
        print(f"   total {tot}")

        conta = d["class_contamination"].dropna().astype(int).value_counts()
        tot_c = int(conta.sum())
        for k, name in [(1, "I"), (2, "II"), (3, "III"), (4, "IV")]:
            v = int(conta.get(k, 0))
            print(f"   Altemeier {name:3s} {v:5d} ({100 * v / tot_c:.1f}%)" if tot_c else
                  f"   Altemeier {name:3s} {v:5d}")
        print(f"   total {tot_c}")

        imp = d["implant_bool"].dropna().astype(bool)
        if len(imp):
            print(f"   implant placed: {int(imp.sum())}/{len(imp)} ({100 * imp.mean():.1f}%)")
        if "SCOPIE" in d.columns:
            sc = d["SCOPIE"].map(to_bool_scopie).dropna().astype(bool)
            if len(sc):
                print(f"   minimally invasive: {int(sc.sum())}/{len(sc)} ({100 * sc.mean():.1f}%)")
        if "COLORECTAL_CANCER" in d.columns:
            cc = d["COLORECTAL_CANCER"].map(norm_oui_non).dropna()
            if len(cc):
                print(f"   colorectal cancer (assessable colorectal operations): "
                      f"{int((cc == 'oui').sum())}/{len(cc)} ({100 * (cc == 'oui').mean():.1f}%)")

    # -------------------------------------------------------------------------
    rule("PROCEDURE CATEGORY: POST-HOC LABEL HARMONISATION (46/47 -> 43)")
    for label in order:
        d = merged[label]
        if d.empty:
            continue
        sub = d[["procedure", "categorie_operation.decision"]].dropna()
        y_true = sub["procedure"].apply(normalize_proc)
        y_pred = sub["categorie_operation.decision"].apply(normalize_proc)
        before = float((y_true == y_pred).mean())
        y_pred_h = y_pred.replace({"46": "43", "47": "43"})
        after = float((y_true == y_pred_h).mean())
        n43 = int((y_true == "43").sum())
        moved = int(((y_true == "43") & y_pred.isin(["46", "47"])).sum())
        print(f"   {label:16s} n={len(sub)}  acc {before:.4f} -> {after:.4f}   "
              f"(reference 43: n={n43}, of which {moved} predicted 46/47)")

    # -------------------------------------------------------------------------
    rule("PROMPT SIZE")
    for name, p in PROMPTS:
        if p.is_file():
            n = len(p.read_text(encoding="utf-8", errors="replace"))
            print(f"   {name:14s} {n} characters, about {n // 4} tokens (characters / 4)")
        else:
            print(f"   {name:14s} not found ({p})")


if __name__ == "__main__":
    main()
