# -*- coding: utf-8 -*-
"""Append anatomical pathology reports to the operative report records
(paired sub-study of the paper: classification with versus without the
pathology report).

For each input record whose patient is selected, the text becomes:

    <compte_rendu_operatoire>
    ...operative report...
    </compte_rendu_operatoire>

    <rapport_pathologie index="1">
    ...pathology report...
    </rapport_pathologie>
    ...

As in the study, pathology reports are matched on patient_id and all reports
of the patient are appended. The number of appended reports is stored in
`patho_report_count`.

Usage
-----
    python src/add_pathology_reports.py --in-dir data/texts --out-dir data/texts_with_pathology \
        --reports data/pathology_reports.csv [--patients data/selected_documents.csv]

--reports : CSV with columns patient_id and text (or contentText).
--patients: optional CSV with a patient_id column; only these patients are kept
            (in the study: patients with digestive surgery).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

_RE_PREFIX_PAT_DOC = re.compile(r"^(?P<prefix>.*)\.(?P<pat>\d+)_(?P<doc>\d+)$")
_RE_PAT_EDS_ID = re.compile(r"^CRO_PAT-(?P<pat>\d+)_EDS-\d+_ID-\d+$")


def patient_id_of(record: dict, path: Path) -> str:
    pid = str(record.get("patient_id") or "").strip()
    if pid:
        return pid
    for rx in (_RE_PAT_EDS_ID, _RE_PREFIX_PAT_DOC):
        m = rx.match(path.stem)
        if m:
            return m.group("pat")
    return ""


def read_csv_rows(path: Path, sep: str):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f, delimiter=sep)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--reports", required=True, type=Path)
    ap.add_argument("--patients", type=Path, default=None)
    ap.add_argument("--sep", default=";")
    args = ap.parse_args()

    selected = None
    if args.patients is not None:
        selected = {(r.get("patient_id") or "").strip() for r in read_csv_rows(args.patients, args.sep)}
        selected.discard("")

    reports: dict = {}
    for r in read_csv_rows(args.reports, args.sep):
        pid = (r.get("patient_id") or "").strip()
        content = (r.get("text") or r.get("contentText") or "").strip()
        if pid and content:
            reports.setdefault(pid, []).append(content)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    copied = enriched = skipped = 0
    for path in sorted(args.in_dir.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(record, dict):
            skipped += 1
            continue
        pid = patient_id_of(record, path)
        if selected is not None and pid not in selected:
            skipped += 1
            continue
        if not record.get("patient_id"):
            record["patient_id"] = pid

        parts = ["<compte_rendu_operatoire>", record.get("text", "") or "", "</compte_rendu_operatoire>"]
        texts = reports.get(pid, [])
        for idx, patho in enumerate(texts, start=1):
            parts += [f'<rapport_pathologie index="{idx}">', patho, "</rapport_pathologie>"]
        record["text"] = "\n\n".join(parts)
        if texts:
            record["patho_report_count"] = len(texts)
            enriched += 1

        (args.out_dir / path.name).write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
        copied += 1

    print(f"written: {copied}, with pathology report(s): {enriched}, skipped: {skipped}")


if __name__ == "__main__":
    main()
