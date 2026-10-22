# -*- coding: utf-8 -*-
"""Evaluate classification runs against the surveillance reference standard.

For each run directory (one output file per document, as written by
infer.py) the script:

1. parses and validates every model output (parsing.py) and classifies the
   unparseable ones (no JSON, invalid JSON, schema violation, other; outputs
   that merely reproduce the input text are counted separately);
2. links each parsed prediction to the single operation it describes, through
   patient identity and date (document table -> operation table);
3. computes, per variable: accuracy, weighted and macro F1, Cohen's kappa,
   quadratic weighted kappa (meaningful for the ordinal contamination class
   only), confusion matrices, on all predictions and on self-declared
   high-confidence predictions only;
4. reports abstentions ("INDECIDABLE" or missing value) and the distribution
   of self-reported confidence levels;
5. optionally compares two runs document by document (exact McNemar test).

Usage
-----
    python src/analyze.py \
        --run "Model-A=runs/model_a" --run "Model-B=runs/model_b" \
        --documents data/documents.csv --operations data/operations.csv \
        [--forms data/forms.csv] [--source-dir data/texts] \
        [--paired | --common-files] [--mcnemar Model-A Model-B] \
        --out results/validation

The expected input formats are described in README.md.
Only aggregate tables and figures are written, plus one parse-error list per
run (file names only) for quality control.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from tqdm import tqdm

from sklearn.metrics import (accuracy_score, classification_report, cohen_kappa_score,
                             confusion_matrix, f1_score)

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import seaborn as sns  # noqa: E402

from metrics import mcnemar_exact_p, weighted_kappa  # noqa: E402
from parsing import (  # noqa: E402
    CONFIDENCE_BUCKETS,
    CONFIDENCE_BUCKETS_EN,
    add_confidence_bucket,
    classify_error,
    ensure_contamination_column,
    norm_oui_non,
    normalize_proc,
    parse_output_file,
    raw_model_text,
    to_bool_scopie,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# =============================================================================
# 0) Constants
# =============================================================================

# Swissnoso procedure codes of the surveillance module studied in the paper:
# 6 colon, 281 rectum, 43 other cardiac surgery, 44 CABG (mammary artery
# only), 45 CABG (venous or mixed grafts), 50 laminectomy.
PROC_TARGETS = [6, 281, 43, 44, 45, 50]

# Reference-standard columns of the operation table (see README.md).
GT_COLS = ["procedure", "class_contamination", "implant_bool", "SCOPIE", "COLORECTAL_CANCER"]

PROC = "Procedure category"
CONTA = "Wound contamination class"
IMPLANT = "Implant"
APPROACH = "Minimally invasive approach"
CANCER = "Colorectal cancer"
TARGET_ORDER = [PROC, CONTA, IMPLANT, APPROACH, CANCER]

CONF_COL_BY_TARGET = {
    PROC: "conf_categorie_operation",
    CONTA: "conf_classe_de_contamination",
    IMPLANT: "conf_implant_pose",
    APPROACH: "conf_voie_abord",
    CANCER: "conf_cancer_colorectal",
}

CONF_PREFIXES = [
    ("categorie_operation", "conf_categorie_operation"),
    ("classe_de_contamination", "conf_classe_de_contamination"),
    ("implant_pose", "conf_implant_pose"),
    ("voie_abord", "conf_voie_abord"),
    ("cancer_colorectal", "conf_cancer_colorectal"),
]

# Confidence level kept for the "high-confidence" variant of the metrics.
CONFIDENCE_KEEP = {"haute"}

CONTA_MAPPING = {"INDECIDABLE": 0, "I": 1, "II": 2, "III": 3, "IV": 4}
PROC_ORDER = ["6", "281", "43", "44", "45", "50", "INDECIDABLE", "Exclu"]
PROC_DISPLAY = {"INDECIDABLE": "Undecidable", "Exclu": "Excluded"}

DPI = 300


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


# =============================================================================
# 1) Identifiers and dates
# =============================================================================

def norm_id(x: Any) -> Optional[str]:
    """Identifier as a canonical string ("123", "123.0" and 123 are equal)."""
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    s = str(x).strip()
    if s == "" or s.lower() in {"nan", "none", "<na>"}:
        return None
    if s.isdigit():
        return str(int(s))
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
    except ValueError:
        pass
    return s


_ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_DMY_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})")


def norm_date(x: Any) -> Optional[str]:
    """Date as YYYY-MM-DD. Accepts YYYY-MM-DD[...] and DD.MM.YYYY[...]."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    s = str(x).strip()
    m = _ISO_DATE.match(s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _DMY_DATE.match(s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def _pick(df: pd.DataFrame, candidates: Sequence[str], what: str, path: Path) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    raise SystemExit(f"{path}: no column for {what} (expected one of {list(candidates)})")


# =============================================================================
# 2) Reference standard and document-to-operation linkage
# =============================================================================

def load_reference(documents: Path, operations: Path, forms: Optional[Path] = None,
                   procedures: Sequence[int] = PROC_TARGETS, sep: str = ";"
                   ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (df_sn, bridge).

    df_sn : one row per surveilled operation of the target procedures,
            indexed by `op_idx`, with the reference columns GT_COLS.
    bridge: document -> operation table (`_id` -> `op_idx`), built by an
            inner join on (patient_id, date). A document that matches no
            operation is not evaluable.
    """
    df_sn = pd.read_csv(operations, sep=sep, encoding="utf-8-sig")
    df_cro = pd.read_csv(documents, sep=sep, encoding="utf-8-sig")

    keep = {normalize_proc(p) for p in procedures}
    df_sn = df_sn.loc[df_sn["procedure"].map(normalize_proc).isin(keep)].copy()

    if forms is not None:
        # Optional second table carrying SCOPIE and COLORECTAL_CANCER per stay
        # (eds_id), as in the original study where these two fields come from
        # the surveillance form rather than from the operation export.
        df_form = pd.read_csv(forms, sep=sep, encoding="utf-8-sig")
        df_form = df_form[["eds_id", "SCOPIE", "COLORECTAL_CANCER"]].copy()
        df_form["_eds_key"] = df_form["eds_id"].map(norm_id)
        df_form = df_form.drop(columns="eds_id").drop_duplicates("_eds_key")
        df_sn = df_sn.drop(columns=[c for c in ("SCOPIE", "COLORECTAL_CANCER") if c in df_sn.columns])
        df_sn["_eds_key"] = df_sn["eds_id"].map(norm_id)
        df_sn = df_sn.merge(df_form, how="left", on="_eds_key").drop(columns="_eds_key")

    df_sn = df_sn.reset_index(drop=True)
    df_sn["op_idx"] = df_sn.index

    doc_col = _pick(df_cro, ["_id", "doc_id"], "the document id", documents)
    doc_date = _pick(df_cro, ["eds_passages_begin_date", "date", "link_date"], "the linkage date", documents)
    op_date = _pick(df_sn, ["date_op", "date"], "the operation date", operations)

    a = pd.DataFrame({
        "_id": df_cro[doc_col].map(norm_id),
        "patient_id": df_cro["patient_id"].map(norm_id),
        "date": df_cro[doc_date].map(norm_date),
    })
    if "eds_id" in df_cro.columns:
        a["eds_id"] = df_cro["eds_id"].map(norm_id)
    b = pd.DataFrame({
        "op_idx": df_sn["op_idx"],
        "patient_id": df_sn["patient_id"].map(norm_id),
        "date": df_sn[op_date].map(norm_date),
    })
    if "eds_id" in df_sn.columns:
        b["eds_id"] = df_sn["eds_id"].map(norm_id)

    a = a.dropna(subset=["_id", "patient_id", "date"])
    b = b.dropna(subset=["patient_id", "date"])
    bridge = a.merge(b, on=["patient_id", "date"], how="inner", suffixes=("_doc", "_op"))
    return df_sn, bridge


# =============================================================================
# 3) Reading a run directory
# =============================================================================

# File-name conventions from which the document id can be recovered.
_RE_PAT_EDS_ID = re.compile(r"^CRO_PAT-(\d+)_EDS-(\d+)_ID-(\d+)$")
_RE_PREFIX_PAT_DOC = re.compile(r"^(?P<prefix>.*)\.(?P<pat>\d+)_(?P<doc>\d+)$")


def ids_from_filename(stem: str) -> Dict[str, Optional[str]]:
    m = _RE_PAT_EDS_ID.match(stem)
    if m:
        return {"patient_id": norm_id(m.group(1)), "eds_id": norm_id(m.group(2)),
                "doc_id": norm_id(m.group(3))}
    m = _RE_PREFIX_PAT_DOC.match(stem)
    if m:
        return {"patient_id": norm_id(m.group("pat")), "eds_id": None,
                "doc_id": norm_id(m.group("doc"))}
    return {"patient_id": None, "eds_id": None, "doc_id": None}


def load_run(run_dir: Path, keep_stems: Optional[set] = None,
             source_dir: Optional[Path] = None
             ) -> Tuple[pd.DataFrame, List[Dict[str, str]]]:
    """Parse every output file of a run -> flattened DataFrame + error list.

    `source_dir` (optional): directory of the input records served to the
    model, with the same file names. Used to flag outputs that are a verbatim
    copy of the input, to distinguish them from genuine format failures.
    """
    frames: List[pd.DataFrame] = []
    errors: List[Dict[str, str]] = []
    files = sorted(p for p in run_dir.glob("*.json") if not p.name.startswith("_"))
    if keep_stems is not None:
        files = [p for p in files if p.stem in keep_stems]

    for p in tqdm(files, desc=run_dir.name, leave=False, disable=not sys.stderr.isatty()):
        try:
            df_data = parse_output_file(p)
        except Exception as e:  # truncated JSON, no JSON block, schema violation...
            echo = ""
            if source_dir is not None and (source_dir / p.name).exists():
                try:
                    with p.open("r", encoding="utf-8-sig") as f:
                        out_txt = raw_model_text(json.load(f))
                    with (source_dir / p.name).open("r", encoding="utf-8-sig") as f:
                        src_txt = raw_model_text(json.load(f))
                    echo = "yes" if out_txt.strip() == src_txt.strip() else "no"
                except Exception:
                    echo = ""
            errors.append({"file": p.name, "category": classify_error(e),
                           "input_echo": echo, "error": f"{type(e).__name__}: {e}"})
            continue

        ids = ids_from_filename(p.stem)
        if ids["doc_id"] is None:
            json_doc = norm_id(df_data["doc_id"].iloc[0]) if "doc_id" in df_data.columns else None
            ids["doc_id"] = json_doc if json_doc is not None else p.stem
        df_data = df_data.copy()
        df_data["filename"] = p.name
        df_data["stem"] = p.stem
        for k, v in ids.items():
            df_data[f"{k}_file"] = v
        frames.append(df_data)

    if not frames:
        return pd.DataFrame(), errors

    df_flat = pd.concat(frames, ignore_index=True)
    df_flat = ensure_contamination_column(df_flat)
    for prefix, out_col in CONF_PREFIXES:
        df_flat = add_confidence_bucket(df_flat, prefix, out_col)
    return df_flat, errors


def merge_with_reference(df_pred: pd.DataFrame, bridge: pd.DataFrame, df_sn: pd.DataFrame) -> pd.DataFrame:
    """Join each prediction to the operation described by its document."""
    if df_pred.empty:
        return pd.DataFrame()
    df = df_pred.merge(bridge, left_on="doc_id_file", right_on="_id", how="inner")
    gt = df_sn[["op_idx"] + [c for c in GT_COLS if c in df_sn.columns]].copy()
    df = df.merge(gt, on="op_idx", how="left")
    return df


# =============================================================================
# 4) Metrics
# =============================================================================

def _metrics(y_true, y_pred, labels) -> Dict[str, Any]:
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    # `weighted_kappa` uses quadratic weights over label indices: it is only
    # meaningful for an ORDINAL variable (contamination class I to IV). For the
    # nominal variables read `kappa` (unweighted Cohen's kappa).
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "f1_weighted": f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0),
        "f1_macro": f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0),
        "kappa": cohen_kappa_score(y_true, y_pred, labels=labels),
        "weighted_kappa": weighted_kappa(y_true, y_pred, labels=labels),
        "n_samples": int(len(y_true)),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=labels),
        "report_dict": classification_report(y_true, y_pred, labels=labels, zero_division=0,
                                             output_dict=True),
        "labels": labels,
    }


def _sub(df: pd.DataFrame, conf_col: str, conf_filter: bool) -> pd.DataFrame:
    if not conf_filter:
        return df
    if conf_col not in df.columns:
        return df.iloc[0:0]
    return df.loc[df[conf_col].isin(CONFIDENCE_KEEP)]


def eval_implant(df: pd.DataFrame, conf_filter: bool) -> Optional[Dict[str, Any]]:
    if "implant_bool" not in df.columns or "implant_pose.value" not in df.columns:
        return None
    sub = _sub(df, "conf_implant_pose", conf_filter)
    sub = sub[["implant_bool", "implant_pose.value"]].dropna()
    if sub.empty:
        return None
    m = _metrics(sub["implant_bool"].astype(bool), sub["implant_pose.value"].astype(bool),
                 [False, True])
    m["display_labels"] = ["No", "Yes"]
    m["target"] = IMPLANT
    return m


def eval_contamination(df: pd.DataFrame, conf_filter: bool) -> Optional[Dict[str, Any]]:
    if "class_contamination" not in df.columns or "classe_de_contamination_pred" not in df.columns:
        return None
    sub = _sub(df, "conf_classe_de_contamination", conf_filter).copy()
    sub["pred_num"] = sub["classe_de_contamination_pred"].map(CONTA_MAPPING)
    sub = sub.dropna(subset=["class_contamination", "pred_num"])
    if sub.empty:
        return None
    # Undecidable (0) is part of the label set, so an abstention counts as an
    # error for this multi-class variable.
    m = _metrics(sub["class_contamination"].astype(int), sub["pred_num"].astype(int),
                 [0, 1, 2, 3, 4])
    m["display_labels"] = ["Und.", "I", "II", "III", "IV"]
    m["target"] = CONTA
    return m


def eval_approach(df: pd.DataFrame, conf_filter: bool) -> Optional[Dict[str, Any]]:
    if "SCOPIE" not in df.columns or "voie_abord.scopie" not in df.columns:
        return None
    sub = _sub(df, "conf_voie_abord", conf_filter)
    y_true = sub["SCOPIE"].map(to_bool_scopie)
    y_pred = sub["voie_abord.scopie"].map(to_bool_scopie)
    mask = y_true.notna() & y_pred.notna()
    if mask.sum() == 0:
        return None
    m = _metrics(y_true[mask].astype(bool), y_pred[mask].astype(bool), [True, False])
    m["display_labels"] = ["Yes", "No"]
    m["target"] = APPROACH
    return m


def eval_cancer(df: pd.DataFrame, conf_filter: bool) -> Optional[Dict[str, Any]]:
    if "COLORECTAL_CANCER" not in df.columns or "cancer_colorectal.value" not in df.columns:
        return None
    sub = _sub(df, "conf_cancer_colorectal", conf_filter)
    y_true = sub["COLORECTAL_CANCER"].map(norm_oui_non)
    y_pred = sub["cancer_colorectal.value"].map(norm_oui_non)
    mask = y_true.notna() & y_pred.notna()
    if mask.sum() == 0:
        return None
    m = _metrics(y_true[mask], y_pred[mask], ["non", "oui"])
    m["display_labels"] = ["No", "Yes"]
    m["target"] = CANCER
    return m


def eval_procedure(df: pd.DataFrame, conf_filter: bool) -> Optional[Dict[str, Any]]:
    if "procedure" not in df.columns or "categorie_operation.decision" not in df.columns:
        return None
    sub = _sub(df, "conf_categorie_operation", conf_filter)
    if sub.empty:
        return None
    y_true = sub["procedure"].apply(normalize_proc)
    y_pred = sub["categorie_operation.decision"].apply(normalize_proc)
    present = set(y_true.unique()) | set(y_pred.unique())
    labels = [l for l in PROC_ORDER if l in present] + sorted(l for l in present if l not in PROC_ORDER)
    m = _metrics(y_true, y_pred, labels)
    m["display_labels"] = [PROC_DISPLAY.get(l, l) for l in labels]
    m["target"] = PROC
    return m


ALL_EVALS = [eval_procedure, eval_contamination, eval_implant, eval_approach, eval_cancer]


def evaluate_run(df: pd.DataFrame, conf_filter: bool) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for fn in ALL_EVALS:
        m = fn(df, conf_filter)
        if m is not None:
            out[m["target"]] = m
    return out


# =============================================================================
# 5) Document-level comparison, abstentions, confidence
# =============================================================================

def per_doc_correct(df: pd.DataFrame, target: str) -> pd.Series:
    """Boolean series indexed by document id: is the prediction correct?

    Non-evaluable documents (missing reference or prediction) are absent.
    """
    if df.empty or "doc_id_file" not in df.columns:
        return pd.Series(dtype=bool)
    d = df.set_index("doc_id_file")

    if target == PROC:
        yt = d["procedure"].apply(normalize_proc)
        yp = d["categorie_operation.decision"].apply(normalize_proc)
        keep = d["procedure"].notna()
    elif target == CONTA:
        yt = d["class_contamination"]
        yp = d["classe_de_contamination_pred"].map(CONTA_MAPPING)
        keep = yt.notna() & yp.notna()
    elif target == IMPLANT:
        yt = d["implant_bool"]
        yp = d["implant_pose.value"]
        keep = yt.notna() & yp.notna()
        yt, yp = yt.astype("boolean"), yp.astype("boolean")
    elif target == APPROACH:
        yt = d["SCOPIE"].map(to_bool_scopie)
        yp = d["voie_abord.scopie"].map(to_bool_scopie)
        keep = yt.notna() & yp.notna()
    elif target == CANCER:
        yt = d["COLORECTAL_CANCER"].map(norm_oui_non)
        yp = d["cancer_colorectal.value"].map(norm_oui_non)
        keep = yt.notna() & yp.notna()
    else:
        return pd.Series(dtype=bool)

    ok = (yt[keep].astype(str) == yp[keep].astype(str))
    return ok[~ok.index.duplicated(keep="first")]


def paired_table(df_a: pd.DataFrame, df_b: pd.DataFrame, name_a: str, name_b: str) -> pd.DataFrame:
    """Document-by-document comparison of two runs, exact McNemar test."""
    rows = []
    for target in TARGET_ORDER:
        oa, ob = per_doc_correct(df_a, target), per_doc_correct(df_b, target)
        common = oa.index.intersection(ob.index)
        if len(common) == 0:
            continue
        a, b_ = oa.loc[common].astype(bool), ob.loc[common].astype(bool)
        both_ok = int((a & b_).sum())
        a_only = int((a & ~b_).sum())
        b_only = int((~a & b_).sum())
        both_ko = int((~a & ~b_).sum())
        rows.append({
            "variable": target,
            "n_paired": int(len(common)),
            f"acc_{name_a}": round((both_ok + a_only) / len(common), 4),
            f"acc_{name_b}": round((both_ok + b_only) / len(common), 4),
            "delta_acc": round((b_only - a_only) / len(common), 4),
            "both_correct": both_ok,
            f"only_{name_a}_correct": a_only,
            f"only_{name_b}_correct": b_only,
            "both_wrong": both_ko,
            "p_mcnemar": round(mcnemar_exact_p(a_only, b_only), 4),
        })
    return pd.DataFrame(rows)


ABSTENTION_SPEC = {
    PROC: ("categorie_operation.decision", {"INDECIDABLE", "Exclu"}),
    CONTA: ("classe_de_contamination_pred", {"INDECIDABLE"}),
    IMPLANT: ("implant_pose.value", set()),
    APPROACH: ("voie_abord.scopie", {"INDECIDABLE"}),
    CANCER: ("cancer_colorectal.value", {"INDECIDABLE"}),
}


def abstention_table(df: pd.DataFrame, run_label: str) -> pd.DataFrame:
    """Share of documents where the model does not decide (undecidable / missing).

    Useful because `to_bool_scopie` maps "INDECIDABLE" to "not minimally
    invasive": for that variable an abstention is scored as an answer.
    """
    rows = []
    for target, (col, words) in ABSTENTION_SPEC.items():
        if col not in df.columns:
            continue
        s = df[col]
        n = len(s)
        n_null = int(s.isna().sum())
        n_word = int(s.astype(str).str.strip().isin(words).sum()) if words else 0
        rows.append({
            "run": run_label, "variable": target, "n_documents": n,
            "undecidable": n_word, "missing": n_null,
            "%abstention": round(100 * (n_word + n_null) / n, 1) if n else np.nan,
        })
    return pd.DataFrame(rows)


def confidence_table(df: pd.DataFrame, run_label: str) -> pd.DataFrame:
    rows = []
    for target in TARGET_ORDER:
        col = CONF_COL_BY_TARGET[target]
        if col not in df.columns:
            continue
        dist = df[col].value_counts().reindex(CONFIDENCE_BUCKETS, fill_value=0).astype(int)
        row = {"run": run_label, "variable": target,
               **{CONFIDENCE_BUCKETS_EN[k]: int(v) for k, v in dist.items()},
               "total": int(dist.sum())}
        row["%high"] = round(100 * row["high"] / row["total"], 1) if row["total"] else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# =============================================================================
# 6) Figures
# =============================================================================

def _flat_xticks(ax, width: int = 18, fontsize: int = 9):
    """Horizontal, wrapped x tick labels (seaborn rotates long labels to 90 degrees)."""
    labels = [textwrap.fill(t.get_text(), width) for t in ax.get_xticklabels()]
    ax.set_xticks(ax.get_xticks())
    ax.set_xticklabels(labels, rotation=0, fontsize=fontsize)
    plt.setp(ax.get_yticklabels(), rotation=0)


def plot_cm_grid(metrics_by_run: Dict[str, Dict[str, Any]], target: str, out_path: Path,
                 suptitle_extra: str = ""):
    """One figure per variable, one confusion matrix per run."""
    runs = [r for r, mm in metrics_by_run.items() if target in mm]
    if not runs:
        return
    n = len(runs)
    ncols = min(n, 4)
    nrows = int(np.ceil(n / ncols))
    labels = metrics_by_run[runs[0]][target]["display_labels"]
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.6 * ncols, 4.2 * nrows), squeeze=False)

    for ax in axes.flat:
        ax.axis("off")
    for i, run in enumerate(runs):
        ax = axes.flat[i]
        ax.axis("on")
        m = metrics_by_run[run][target]
        disp = m["display_labels"]
        sns.heatmap(m["confusion_matrix"], xticklabels=disp, yticklabels=disp, fmt="d",
                    annot=True, cmap="Blues", cbar=False, ax=ax)
        stats = (f"accuracy={m['accuracy']:.3f}  "
                 f"weighted F1={m['f1_weighted']:.3f}  n={m['n_samples']}")
        ax.set_title(f"{run}\n{stats}", fontsize=10)
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Reference")
        plt.setp(ax.get_yticklabels(), rotation=0)
        if len(labels) > 5:
            ax.tick_params(axis="x", rotation=45)
            for _lab in ax.get_xticklabels():
                _lab.set_ha("right")
                _lab.set_rotation_mode("anchor")

    fig.suptitle(f"{target}{suptitle_extra}", fontsize=13, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def plot_metric_heatmap(df_lb: pd.DataFrame, metric: str, out_path: Path, title: str):
    pivot = df_lb.pivot_table(index="run", columns="target", values=metric, observed=False)
    pivot = pivot.reindex(columns=[t for t in TARGET_ORDER if t in pivot.columns])
    if pivot.empty:
        return
    fig, ax = plt.subplots(figsize=(max(10, 2.4 * len(pivot.columns)),
                                    max(3, 0.9 * len(pivot) + 2.0)))
    sns.heatmap(pivot, annot=True, fmt=".3f", cmap="YlGn", vmin=0, vmax=1, linewidths=0.5, ax=ax)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel("")
    ax.set_xlabel("")
    _flat_xticks(ax)
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


def plot_confidence_heatmap(df_conf: pd.DataFrame, out_path: Path, title: str):
    if df_conf.empty:
        return
    cols = [CONFIDENCE_BUCKETS_EN[k] for k in CONFIDENCE_BUCKETS]
    df_conf = df_conf.copy()
    df_conf["key"] = df_conf["run"] + " · " + df_conf["variable"]
    heat = df_conf.set_index("key")[cols]
    heat_pct = heat.div(heat.sum(axis=1).replace(0, np.nan), axis=0) * 100
    fig, ax = plt.subplots(figsize=(10.5, max(4, 0.35 * len(heat_pct) + 1.8)))
    sns.heatmap(heat_pct.fillna(0), annot=True, fmt=".1f", cmap="Blues",
                cbar_kws={"label": "% of predictions"}, ax=ax)
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_ylabel("")
    ax.set_xlabel("")
    _flat_xticks(ax, width=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=DPI, bbox_inches="tight")
    plt.close(fig)


# =============================================================================
# 7) Evaluation of a set of runs
# =============================================================================

def build_leaderboard(all_metrics: Dict[str, Dict[str, Dict[str, Any]]], variant: str) -> pd.DataFrame:
    rows = []
    for run, targets in all_metrics.items():
        for target, m in targets.items():
            rows.append({
                "run": run, "target": target, "variant": variant,
                "accuracy": m["accuracy"], "f1_weighted": m["f1_weighted"],
                "f1_macro": m["f1_macro"], "kappa": m["kappa"],
                "weighted_kappa": m["weighted_kappa"], "n_samples": m["n_samples"],
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["target"] = pd.Categorical(df["target"], categories=TARGET_ORDER, ordered=True)
        df = df.sort_values(["target", "f1_weighted"], ascending=[True, False])
    return df


def df_to_md(df: pd.DataFrame) -> str:
    """Markdown table without the `tabulate` dependency."""
    if df.empty:
        return "_(empty)_"

    def fmt(v):
        if isinstance(v, float):
            if pd.isna(v):
                return "NA"
            return f"{v:.1f}" if abs(v) >= 10 else f"{v:.4f}"
        return "NA" if v is None else str(v)

    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(fmt(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def markdown_leaderboard(df_lb: pd.DataFrame, title: str) -> List[str]:
    lines = [f"### {title}\n"]
    for target in [t for t in TARGET_ORDER if t in set(df_lb["target"])]:
        sub = df_lb[df_lb["target"] == target].sort_values("f1_weighted", ascending=False)
        lines.append(f"**{target}**\n")
        lines.append("| Rank | Run | Accuracy | F1 (weighted) | F1 (macro) | Kappa | Quadratic kappa | N |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for rank, (_, r) in enumerate(sub.iterrows(), 1):
            lines.append(
                f"| {rank} | {r['run']} | {r['accuracy']:.4f} | {r['f1_weighted']:.4f} | "
                f"{r['f1_macro']:.4f} | {r['kappa']:.4f} | {r['weighted_kappa']:.4f} | "
                f"{int(r['n_samples'])} |"
            )
        lines.append("")
    return lines


def _output_files(run_dir: Path) -> List[Path]:
    return sorted(p for p in run_dir.glob("*.json") if not p.name.startswith("_"))


def parseable_stems(runs: Dict[str, Dict[str, Any]]) -> set:
    """Documents with a parseable output in EVERY run.

    Without this restriction each model is scored on its own subset: a model
    that fails to produce JSON on its hardest cases is favoured, since these
    cases leave its denominator.
    """
    common: Optional[set] = None
    for label, info in runs.items():
        run_dir = info["dir"]
        ok = set()
        files = _output_files(run_dir)
        for pth in tqdm(files, desc=f"pairing {label}", leave=False, disable=not sys.stderr.isatty()):
            try:
                parse_output_file(pth)
                ok.add(pth.stem)
            except Exception:
                pass
        print(f"  {label}: {len(ok)}/{len(files)} parseable")
        common = ok if common is None else (common & ok)
    common = common or set()
    print(f"  -> documents kept: {len(common)}")
    return common


def common_file_stems(runs: Dict[str, Dict[str, Any]]) -> set:
    """Documents with an output file (parseable or not) in EVERY run."""
    common: Optional[set] = None
    for label, info in runs.items():
        stems = {p.stem for p in _output_files(info["dir"])}
        print(f"  {label}: {len(stems)} files")
        common = stems if common is None else (common & stems)
    common = common or set()
    print(f"  -> documents kept: {len(common)}")
    return common


def analyze_batch(runs: Dict[str, Dict[str, Any]], df_sn: pd.DataFrame, bridge: pd.DataFrame,
                  out_dir: Path, title: str, keep_stems: Optional[set] = None):
    """Load, evaluate and report a set of runs.

    Returns (leaderboard, merged_by_run, preds_by_run, metrics_all, metrics_high).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(exist_ok=True)

    coverage_rows = []
    conf_frames = []
    abst_frames = []
    metrics_all: Dict[str, Dict[str, Dict[str, Any]]] = {}
    metrics_high: Dict[str, Dict[str, Dict[str, Any]]] = {}
    merged_by_run: Dict[str, pd.DataFrame] = {}
    preds_by_run: Dict[str, pd.DataFrame] = {}

    for label, info in runs.items():
        run_dir = info["dir"]
        n_files = len([p for p in _output_files(run_dir) if keep_stems is None or p.stem in keep_stems])
        print(f"\n[{label}] {run_dir} ({n_files} files)")

        df_pred, errors = load_run(run_dir, keep_stems=keep_stems, source_dir=info.get("source"))
        preds_by_run[label] = df_pred
        df_err = pd.DataFrame(errors)
        if errors:
            df_err.to_csv(out_dir / f"parse_errors_{slug(label)}.csv", index=False)
        err_by_cat = df_err["category"].value_counts().to_dict() if errors else {}
        n_echo = int((df_err["input_echo"] == "yes").sum()) if errors else 0

        df_eval = merge_with_reference(df_pred, bridge, df_sn)
        merged_by_run[label] = df_eval

        declared = "NA"
        if not df_pred.empty and "version" in df_pred.columns:
            vc = df_pred["version"].dropna().value_counts()
            declared = " / ".join(f"{k} ({v})" for k, v in vc.items()) if len(vc) else "NA"

        coverage_rows.append({
            "run": label,
            "directory": run_dir.name,
            "declared_version": declared,
            "files": n_files,
            "parsed": int(len(df_pred)),
            "parse_rate_%": round(100 * len(df_pred) / n_files, 1) if n_files else np.nan,
            "err_no_json_output": int(err_by_cat.get("no_json_output", 0)),
            "err_invalid_json": int(err_by_cat.get("invalid_json", 0)),
            "err_schema_violation": int(err_by_cat.get("schema_violation", 0)),
            "err_other": int(err_by_cat.get("other", 0)),
            "outputs_echoing_input": n_echo,
            "linked_documents": int(df_eval["doc_id_file"].nunique()) if not df_eval.empty else 0,
            "evaluable_rows": int(len(df_eval)),
        })
        print(f"  parsed={len(df_pred)}/{n_files}  errors={len(errors)} {err_by_cat}"
              f"  (input echoes: {n_echo})  evaluable rows={len(df_eval)}")

        if df_eval.empty:
            continue

        metrics_all[label] = evaluate_run(df_eval, conf_filter=False)
        metrics_high[label] = evaluate_run(df_eval, conf_filter=True)
        conf_frames.append(confidence_table(df_eval, label))
        abst_frames.append(abstention_table(df_eval, label))

        for target, m in metrics_all[label].items():
            pd.DataFrame(m["report_dict"]).transpose().to_csv(
                out_dir / f"report_{slug(target)}_{slug(label)}.csv")

    df_cov = pd.DataFrame(coverage_rows)
    df_cov.to_csv(out_dir / "coverage.csv", index=False)

    lb_all = build_leaderboard(metrics_all, "all predictions")
    lb_high = build_leaderboard(metrics_high, "high confidence")
    lb = pd.concat([lb_all, lb_high], ignore_index=True)
    lb.to_csv(out_dir / "leaderboard.csv", index=False)

    df_abst = pd.concat(abst_frames, ignore_index=True) if abst_frames else pd.DataFrame()
    if not df_abst.empty:
        df_abst.to_csv(out_dir / "abstention_summary.csv", index=False)

    df_conf = pd.concat(conf_frames, ignore_index=True) if conf_frames else pd.DataFrame()
    if not df_conf.empty:
        df_conf.to_csv(out_dir / "confidence_summary.csv", index=False)
        plot_confidence_heatmap(df_conf, fig_dir / "confidence_heatmap.png",
                                "Distribution of self-reported confidence levels (%)")

    for target in TARGET_ORDER:
        plot_cm_grid(metrics_all, target, fig_dir / f"cm_{slug(target)}.png",
                     suptitle_extra=", all predictions")
        plot_cm_grid(metrics_high, target, fig_dir / f"cm_{slug(target)}_high_confidence.png",
                     suptitle_extra=", high-confidence predictions")

    if not lb_all.empty:
        plot_metric_heatmap(lb_all, "f1_weighted", fig_dir / "heatmap_f1_weighted.png",
                            "Weighted F1, all predictions")
        plot_metric_heatmap(lb_all, "weighted_kappa", fig_dir / "heatmap_kappa.png",
                            "Weighted kappa, all predictions")
    if not lb_high.empty:
        plot_metric_heatmap(lb_high, "f1_weighted", fig_dir / "heatmap_f1_weighted_high_confidence.png",
                            "Weighted F1, high-confidence predictions")

    # -- Markdown report
    lines = [f"# {title}\n",
             "Each prediction is linked to the single operation described by its document "
             "(document table joined to the operation table on patient_id + date).\n",
             "## Coverage\n",
             df_to_md(df_cov), ""]
    if not lb_all.empty:
        lines += markdown_leaderboard(lb_all, "Ranking, all predictions")
    if not lb_high.empty:
        lines += markdown_leaderboard(lb_high, "Ranking, high-confidence predictions only")
    if not df_abst.empty:
        lines += ["## Abstentions (undecidable or missing value)\n",
                  "> For the minimally invasive approach, `to_bool_scopie` maps an abstention "
                  "to \"not minimally invasive\": for this variable an abstention is scored as "
                  "a negative answer in the metrics above.\n",
                  df_to_md(df_abst), ""]
    if not df_conf.empty:
        lines += ["## Self-reported confidence levels\n", df_to_md(df_conf), ""]
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")

    # -- Console
    if not lb_all.empty:
        print("\n" + "=" * 78)
        print(f"  {title}: weighted F1 (all predictions)")
        print("=" * 78)
        piv = lb_all.pivot_table(index="run", columns="target", values="f1_weighted", observed=False)
        piv = piv.reindex(columns=[t for t in TARGET_ORDER if t in piv.columns])
        print(piv.round(4).to_string())
        print("\n  evaluable n per variable:")
        pivn = lb_all.pivot_table(index="run", columns="target", values="n_samples", observed=False)
        pivn = pivn.reindex(columns=[t for t in TARGET_ORDER if t in pivn.columns])
        print(pivn.astype("Int64").to_string())

    print(f"\nResults written to {out_dir}")
    return lb, merged_by_run, preds_by_run, metrics_all, metrics_high


PRED_COLS = {
    "categorie_operation.decision": PROC,
    "classe_de_contamination_pred": CONTA,
    "implant_pose.value": IMPLANT,
    "voie_abord.scopie": APPROACH,
    "cancer_colorectal.value": CANCER,
}


def compare_two_runs(label_a: str, label_b: str, merged_by_run, preds_by_run, out_dir: Path):
    """Paired comparison of two runs on the same documents (McNemar) and raw agreement."""
    df_paired = paired_table(merged_by_run.get(label_a, pd.DataFrame()),
                             merged_by_run.get(label_b, pd.DataFrame()),
                             slug(label_a), slug(label_b))
    df_paired.to_csv(out_dir / "paired_mcnemar.csv", index=False)
    print("\n=== Paired comparison (same documents, exact McNemar test) ===")
    print(df_paired.to_string(index=False))

    pa, pb = preds_by_run.get(label_a, pd.DataFrame()), preds_by_run.get(label_b, pd.DataFrame())
    rows = []
    if not pa.empty and not pb.empty:
        cols = ["stem"] + [c for c in PRED_COLS if c in pa.columns and c in pb.columns]
        merged = pa[cols].merge(pb[cols], on="stem", how="inner", suffixes=("_a", "_b"))
        for col, name in PRED_COLS.items():
            ca, cb = f"{col}_a", f"{col}_b"
            if ca not in merged.columns or cb not in merged.columns:
                continue
            # Missing values compare equal ("NA"), whatever the pandas string dtype.
            sa = merged[ca].map(lambda v: "NA" if pd.isna(v) else str(v))
            sb = merged[cb].map(lambda v: "NA" if pd.isna(v) else str(v))
            diff = sa != sb
            rows.append({"variable": name, "n": len(merged), "identical": int((~diff).sum()),
                         "different": int(diff.sum()), "%agreement": round(100 * (~diff).mean(), 1)})
    df_agree = pd.DataFrame(rows)
    df_agree.to_csv(out_dir / "run_agreement.csv", index=False)
    print("\n=== Agreement between the two runs on the same documents ===")
    print(df_agree.to_string(index=False))

    with (out_dir / "report.md").open("a", encoding="utf-8") as f:
        f.write(f"\n## Paired comparison: {label_a} vs {label_b} (same documents, exact McNemar test)\n\n")
        f.write(df_to_md(df_paired))
        f.write("\n\n## Raw agreement between the two runs\n\n")
        f.write(df_to_md(df_agree))
        f.write("\n")


# =============================================================================
# 8) CLI
# =============================================================================

def parse_run_args(values: List[str]) -> Dict[str, Dict[str, Any]]:
    runs: Dict[str, Dict[str, Any]] = {}
    for v in values:
        label, sep, path = v.partition("=")
        if not sep:
            path, label = v, Path(v).name
        run_dir = Path(path)
        if not run_dir.is_dir():
            raise SystemExit(f"Run directory not found: {run_dir}")
        if label in runs:
            raise SystemExit(f"Duplicate run label: {label}")
        runs[label] = {"dir": run_dir}
    return runs


def add_reference_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--run", action="append", required=True, metavar="LABEL=DIR",
                    help="run to evaluate (repeatable): a label and the directory of output files")
    ap.add_argument("--documents", required=True, type=Path,
                    help="document table (doc id, patient_id, linkage date)")
    ap.add_argument("--operations", required=True, type=Path,
                    help="operation table with the reference standard")
    ap.add_argument("--forms", type=Path, default=None,
                    help="optional table (eds_id, SCOPIE, COLORECTAL_CANCER) merged on eds_id")
    ap.add_argument("--source-dir", type=Path, default=None,
                    help="directory of the input records (same file names), used to detect "
                         "outputs that merely reproduce the input")
    ap.add_argument("--sep", default=";", help="CSV separator (default ';')")
    ap.add_argument("--procedures", default=",".join(str(p) for p in PROC_TARGETS),
                    help="procedure codes kept in the operation table (default %(default)s)")


def build_runs_and_reference(args):
    runs = parse_run_args(args.run)
    if args.source_dir is not None:
        for info in runs.values():
            info["source"] = args.source_dir
    procedures = [p.strip() for p in args.procedures.split(",") if p.strip()]
    df_sn, bridge = load_reference(args.documents, args.operations, args.forms,
                                   procedures=procedures, sep=args.sep)
    print(f"Reference: {len(df_sn)} target operations, "
          f"{bridge['_id'].nunique()} linkable documents")
    return runs, df_sn, bridge


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_reference_args(ap)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--paired", action="store_true",
                       help="score every run on the documents parseable by ALL runs")
    group.add_argument("--common-files", action="store_true",
                       help="score every run on the documents that have an output file in ALL runs")
    ap.add_argument("--mcnemar", nargs=2, metavar=("LABEL_A", "LABEL_B"),
                    help="also compare two runs document by document (exact McNemar test)")
    ap.add_argument("--title", default="Evaluation of classification runs")
    ap.add_argument("--out", required=True, type=Path, help="output directory")
    args = ap.parse_args()

    runs, df_sn, bridge = build_runs_and_reference(args)
    if args.mcnemar:
        missing = [l for l in args.mcnemar if l not in runs]
        if missing:
            raise SystemExit(f"--mcnemar: unknown run label(s): {missing}")

    keep = None
    title = args.title
    if args.paired:
        print("\nRestricting to documents parseable by all runs ...")
        keep = parseable_stems(runs)
        title += " (paired documents)"
    elif args.common_files:
        print("\nRestricting to documents present in all runs ...")
        keep = common_file_stems(runs)
        title += " (common documents)"

    _, merged, preds, _, _ = analyze_batch(runs, df_sn, bridge, args.out, title, keep_stems=keep)
    if args.mcnemar:
        compare_two_runs(args.mcnemar[0], args.mcnemar[1], merged, preds, args.out)


if __name__ == "__main__":
    main()
