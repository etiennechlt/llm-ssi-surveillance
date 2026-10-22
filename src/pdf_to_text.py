# -*- coding: utf-8 -*-
"""Convert operative reports (PDF or plain text) into the JSON input records
of infer.py.

PDF files are converted to Markdown by a Docling server (docling-serve,
https://github.com/docling-project/docling-serve), run locally so that no
document leaves the institution; .txt files are read as they are. The text
then goes through the light cleaning used in the study:

- Unicode normalisation (NFKC), joined hyphenations, non-breaking spaces;
- dates DD.MM.YYYY rewritten as YYYY-MM-DD, a few unit spellings unified;
- removal of administrative header lines (admission or follow-up physician,
  phone numbers, signature lines), the "Concerne :" line being replaced by a
  neutral placeholder. This is NOT a de-identification tool;
- removal of social / spiritual history lines and of some headers;
- expansion of a few clinical abbreviations, e.g. "TA" -> "TA (tension artérielle)";
- collapse of hard line wraps and blank lines.

Each output record is written as <input file stem>.json:
    {"patient_id": "...", "eds_id": "...", "doc_id": "...", "text": "..."}

Identifiers come from an optional metadata CSV (columns: a document id named
_id, doc_id or doc_version_id; optional patient_id or ipp; optional eds_id or
eds), matched on the document id found in the file name (`...ID-<doc>` or the
whole stem). Use pseudonymous identifiers.

Usage
-----
    python src/pdf_to_text.py --input-dir data/pdf --output-dir data/texts \
        [--metadata data/documents.csv] [--docling-url http://localhost:5001/v1/convert/file]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
from pathlib import Path

DEFAULT_DOCLING_URL = "http://localhost:5001/v1/convert/file"


# ================== Docling ==================
def convert_pdf_to_md_docling(pdf_path: Path, docling_url: str) -> str:
    """Send the PDF to the Docling server and return the Markdown text."""
    import requests

    payload = {"to_formats": "md", "image_export_mode": "placeholder", "do_ocr": "false"}
    with open(pdf_path, "rb") as f:
        files_payload = {"files": (pdf_path.name, f, "application/pdf")}
        response = requests.post(docling_url, data=payload, files=files_payload)
        response.raise_for_status()

    if "application/json" in response.headers.get("Content-Type", ""):
        return response.json().get("document", {}).get("md_content", "")
    return response.text


# ================== Metadata ==================
def load_csv_metadata(csv_filepath: Path, sep: str | None = None) -> dict:
    """{doc_id: {"patient_id": ..., "eds_id": ..., "doc_id": ...}} from a CSV."""
    import pandas as pd

    meta_lookup: dict = {}
    if not os.path.isfile(csv_filepath):
        print(f"[WARNING] metadata file not found: {csv_filepath}")
        return meta_lookup

    df = pd.read_csv(csv_filepath, sep=sep, engine="python")
    cols = {c.lower(): c for c in df.columns}
    col_id = cols.get("_id") or cols.get("doc_id") or cols.get("doc_version_id")
    col_eds = cols.get("eds_id") or cols.get("eds")
    col_pat = cols.get("patient_id") or cols.get("ipp")
    if not col_id:
        print("[ERROR] no document id column (_id / doc_id / doc_version_id) in the metadata file")
        return meta_lookup

    for _, row in df.iterrows():
        doc_id_val = str(row[col_id]).strip()
        meta_lookup[doc_id_val] = {
            "patient_id": str(row[col_pat]).strip() if col_pat and not pd.isna(row[col_pat]) else "",
            "eds_id": str(row[col_eds]).strip() if col_eds and not pd.isna(row[col_eds]) else "",
            "doc_id": doc_id_val,
        }
    print(f"[metadata] {len(meta_lookup)} documents indexed")
    return meta_lookup


def get_doc_id_from_filename(filename: str) -> str:
    """Document id from a file name `...ID-<doc>...`, otherwise the whole stem."""
    base = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r"ID-([A-Za-z0-9_]+)", base, re.IGNORECASE)
    if m:
        return m.group(1)
    return base


# ================== Cleaning ==================
def clean_newlines(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def _normalize_unicode(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace(" ", " ").replace("​", "")
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    return t


def _normalize_dates_units(t: str) -> str:
    t = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b",
               lambda m: f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}", t)
    t = re.sub(r"µg\s*(?:heures?|/ ?heures?)", "µg/h", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(\d+)\s*[x×]\s*par\s*jour\b", r"\1×/j", t, flags=re.IGNORECASE)
    return t


def strip_header_metadata(t: str) -> str:
    """Remove administrative header lines, phone numbers and signature lines."""
    t = re.sub(r"^[ \t]*Admission\s+réalisée\s+par\b.*$", "", t, flags=re.MULTILINE | re.IGNORECASE)
    t = re.sub(r"\s*suivi\s+par\s+le\s+Dr[^\n]*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(Tél\.?|Téléphone)\s*:?\s*([+0-9\s\./-]{6,})", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^[ \t]*#*\s*Concerne\s*:.*$", "Concerne : Patient(e) anonymisé(e)", t,
               flags=re.MULTILINE | re.IGNORECASE)
    t = re.sub(r"^[ \t]*(Docteure?|Doctoresse|Dr\.?)\s+[A-ZÉÈÊÎÏÔÛÂÀ][a-zA-ZÉÈÊËÎÏÔÛÂÀÇÙÛÜŸéèêëàâîïôöùûüç'’\-\.\s]+$",
               "", t, flags=re.MULTILINE | re.IGNORECASE)
    return t


def _remove_headers_block(t: str) -> str:
    patterns = [
        r"^(?:Histoire du problème|Note d'admission).*$",
        r"^[ \t]*Admission supervisée par .*$",
    ]
    for p in patterns:
        t = re.sub(p, "", t, flags=re.MULTILINE)
    return t


def _remove_social_spiritual(t: str, drop_social: bool = True, drop_spiritual: bool = True) -> str:
    pats = []
    if drop_spiritual:
        pats += [r"^[ \t]*#*\s*Spiritualit[éè].*$"]
    if drop_social:
        pats += [
            r"^[ \t]*#*\s*Social\s*:.*$",
            r"^[ \t]*Contexte social.*$",
            r"^[ \t]*[Vv]it à domicile.*$",
            r"^[ \t]*IMAD.*$",
        ]
    for p in pats:
        t = re.sub(p, "", t, flags=re.MULTILINE | re.IGNORECASE)
    return t


def clean_note_for_llm(text: str) -> str:
    t = _normalize_unicode(text)
    t = _normalize_dates_units(t)
    t = strip_header_metadata(t)
    t = _remove_headers_block(t)
    t = _remove_social_spiritual(t)
    return t


ABBR = {
    r"\bTA\b": "tension artérielle",
    r"\bFC\b": "fréquence cardiaque",
    r"\bFR\b": "fréquence respiratoire",
    r"\bSpO2?\b": "saturation en oxygène",
    r"\bGCS\b|\bGSC\b": "Score de Glasgow",
    r"\bOMI\b": "œdèmes des membres inférieurs",
    r"\bNFS\b": "numération formule sanguine",
    r"\bCRP\b": "C-réactive protéine",
    r"\bECG\b": "électrocardiogramme",
    r"\bHTA\b": "hypertension artérielle",
}


def expand_abbreviations(text: str) -> str:
    out = text
    for pat, expansion in ABBR.items():
        out = re.sub(pat, lambda m, e=expansion: f"{m.group(0)} ({e})", out)
    return out


def prepare_text(raw: str, clean: bool = True, expand_abbr: bool = True) -> str:
    t = raw
    if clean:
        t = clean_note_for_llm(t)
    if expand_abbr:
        t = expand_abbreviations(t)
    return clean_newlines(t)


# ================== Main ==================
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input-dir", required=True, type=Path, help="directory of .pdf and/or .txt files")
    ap.add_argument("--output-dir", required=True, type=Path, help="directory of the JSON records")
    ap.add_argument("--metadata", type=Path, default=None, help="optional CSV with document identifiers")
    ap.add_argument("--metadata-sep", default=None, help="CSV separator (default: sniffed)")
    ap.add_argument("--docling-url", default=DEFAULT_DOCLING_URL,
                    help="Docling conversion endpoint (default %(default)s)")
    ap.add_argument("--no-clean", action="store_true", help="skip the cleaning pipeline")
    ap.add_argument("--no-abbr", action="store_true", help="skip the abbreviation expansion")
    args = ap.parse_args()

    from tqdm import tqdm

    start = time.time()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata = load_csv_metadata(args.metadata, args.metadata_sep) if args.metadata else {}

    files = sorted(list(args.input_dir.glob("*.pdf")) + list(args.input_dir.glob("*.txt")))
    for path in tqdm(files, desc="Converting"):
        doc_id_key = get_doc_id_from_filename(path.name)
        file_meta = metadata.get(doc_id_key, {"patient_id": "", "eds_id": "", "doc_id": doc_id_key})

        try:
            if path.suffix.lower() == ".pdf":
                raw = convert_pdf_to_md_docling(path, args.docling_url)
            else:
                raw = path.read_text(encoding="utf-8-sig")
        except Exception as e:
            print(f"\n[ERROR] conversion failed for {path.name}: {type(e).__name__}: {e}")
            continue
        if not raw:
            print(f"\n[WARNING] empty content for {path.name}")
            continue

        record = {
            "patient_id": file_meta["patient_id"],
            "eds_id": file_meta["eds_id"],
            "doc_id": file_meta["doc_id"],
            "text": prepare_text(raw, clean=not args.no_clean, expand_abbr=not args.no_abbr),
        }
        out_json = args.output_dir / f"{path.stem}.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"\n[DONE] {len(files)} file(s) in {time.time() - start:.1f}s, records in {args.output_dir}")


if __name__ == "__main__":
    main()
