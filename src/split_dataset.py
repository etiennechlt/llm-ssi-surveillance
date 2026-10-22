# -*- coding: utf-8 -*-
"""Development / validation split of the surveilled operations.

The split is made at the PATIENT level (all operations of a patient fall in
the same set) and stratified on each patient's representative procedure (the
mode of his or her procedure codes). The study used a development fraction of
0.20 of the patients and random_state=42.

Usage
-----
    python src/split_dataset.py --input data/operations_all.csv \
        --out-dev data/operations_dev.csv --out-val data/operations_val.csv \
        [--proc-col procedure] [--patient-col patient_id] [--dev-fraction 0.20] [--seed 42]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split


def stratified_split_by_procedure(df: pd.DataFrame, proc_col: str = "procedure",
                                  patient_col: str | None = None, train_size: float = 0.20,
                                  random_state: int = 42):
    """Return (df_dev, df_val), stratified on `proc_col`.

    With `patient_col`, patients (not rows) are split, stratified on the mode
    of their procedures, so that no patient appears in both sets.
    """
    if patient_col is None:
        train_idx, test_idx = train_test_split(
            df.index, train_size=train_size, stratify=df[proc_col], random_state=random_state,
        )
        return df.loc[train_idx].copy(), df.loc[test_idx].copy()

    per_patient_proc = df.groupby(patient_col)[proc_col].agg(lambda s: s.mode().iloc[0])
    patients = per_patient_proc.index.to_numpy()
    labels = per_patient_proc.to_numpy()

    train_patients, test_patients = train_test_split(
        patients, train_size=train_size, stratify=labels, random_state=random_state,
    )
    df_train = df[df[patient_col].isin(train_patients)].copy()
    df_test = df[df[patient_col].isin(test_patients)].copy()
    return df_train, df_test


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=Path, help="operation table (CSV)")
    ap.add_argument("--out-dev", required=True, type=Path)
    ap.add_argument("--out-val", required=True, type=Path)
    ap.add_argument("--sep", default=";")
    ap.add_argument("--proc-col", default="procedure")
    ap.add_argument("--patient-col", default="patient_id",
                    help="grouping column; pass an empty string for a row-level split")
    ap.add_argument("--dev-fraction", type=float, default=0.20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    df = pd.read_csv(args.input, sep=args.sep, dtype={"doc_version_id": str, "patient_id": str})
    df_dev, df_val = stratified_split_by_procedure(
        df, proc_col=args.proc_col, patient_col=args.patient_col or None,
        train_size=args.dev_fraction, random_state=args.seed,
    )
    for path, part in ((args.out_dev, df_dev), (args.out_val, df_val)):
        path.parent.mkdir(parents=True, exist_ok=True)
        part.to_csv(path, index=False, sep=args.sep)
    print(f"development: {len(df_dev)} rows, validation: {len(df_val)} rows")


if __name__ == "__main__":
    main()
