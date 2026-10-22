# -*- coding: utf-8 -*-
"""Tolerant parsing, coercion and schema validation of the LLM outputs.

A model answer is free text that should contain one JSON object (prompt v6.2,
see prompts/system_prompt_reasoning_v6_2.md). This module:

1. extracts the JSON block from the raw answer (fenced ```json block first,
   otherwise the first balanced {...} or [...]);
2. applies minimal, non-destructive repairs (trailing commas, control
   characters);
3. coerces known type variants (a quotation returned as a list, a boolean
   returned as "oui", ...) so that a model is not penalised for a
   serialisation detail;
4. validates the result against a Pydantic schema;
5. provides the normalisation helpers used by the evaluation (procedure codes,
   yes/no values, surgical approach, confidence levels).

Field names follow the French output schema of the prompt.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

# =============================================================================
# 1) Raw answer -> Python object
# =============================================================================

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
_TRAILING_COMMAS_RE = re.compile(r",\s*([}\]])")

NO_JSON_MESSAGE = "No valid JSON block found"


def _extract_first_json_block(text: str) -> str:
    """Return the first JSON block of `text`.

    Priority to the content of the first fenced block (```json ... ```) when it
    starts with { or [; otherwise the first balanced {...} object, then the
    first balanced [...] array.
    """
    m = _FENCE_RE.search(text)
    if m:
        candidate = m.group(1).strip()
        if candidate.startswith("{") or candidate.startswith("["):
            return candidate

    for opener, closer in [("{", "}"), ("[", "]")]:
        start = text.find(opener)
        if start == -1:
            continue

        depth = 0
        in_str = False
        esc = False

        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]

    raise ValueError(NO_JSON_MESSAGE)


def parse_llm_output_to_dict(raw: Union[str, Dict, List]) -> Any:
    """Turn a model answer (usually text around a JSON block) into a dict/list."""
    if isinstance(raw, (dict, list)):
        return raw
    if raw is None:
        raise ValueError("raw is None")

    s = str(raw).lstrip("﻿").strip()
    s = _extract_first_json_block(s)
    s = _TRAILING_COMMAS_RE.sub(r"\1", s)

    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    cleaned = "".join(ch for ch in s if (ch >= " ") or ch in "\t\n\r")
    cleaned = _TRAILING_COMMAS_RE.sub(r"\1", cleaned)
    return json.loads(cleaned)


# =============================================================================
# 2) Output schema (Pydantic)
# =============================================================================
# The schema accepts the field names of prompt v6.x and of earlier prompt
# versions (e.g. `niveau_certitude` instead of `confiance`), so that outputs
# of older runs can be parsed with the same code. Unknown fields are dropped.

class CategorieOperation(BaseModel):
    decision: Optional[str] = None
    label: Optional[str] = None
    reason: Optional[str] = None
    raison: Optional[str] = None
    raison_methodologique: Optional[str] = None
    chop_codes_detectes: List[str] = Field(default_factory=list)
    est_inclus: Optional[bool] = None
    confiance: Optional[str] = None
    niveau_certitude: Optional[str] = None
    biais_selection_identifies: List[str] = Field(default_factory=list)
    exclusions_verifiees: Optional[Union[str, List[str]]] = None


class ClasseDeContamination(BaseModel):
    classe: Optional[str] = None
    justification: Optional[str] = None
    justification_academique: Optional[str] = None
    mots_cles_detectes: Dict[str, List[str]] = Field(default_factory=dict)
    confiance: Optional[str] = None
    niveau_certitude: Optional[str] = None
    facteurs_confondants: List[str] = Field(default_factory=list)


class ImplantPose(BaseModel):
    value: Optional[bool] = None
    type: Optional[str] = None
    details: Optional[str] = None
    details_anatomiques: Optional[str] = None
    materiau: Optional[str] = None
    citation_cro: Optional[str] = None
    confiance: Optional[str] = None
    niveau_certitude: Optional[str] = None


class VoieAbord(BaseModel):
    scopie: Optional[str] = None
    conversion_scopie_vers_tomie: Optional[bool] = None
    justification: Optional[str] = None
    justification_technique: Optional[str] = None
    moment_conversion: Optional[str] = None
    raison_conversion: Optional[str] = None
    citation_cro: Optional[str] = None
    confiance: Optional[str] = None
    niveau_certitude: Optional[str] = None


class CancerColorectal(BaseModel):
    applicable: Optional[bool] = None
    value: Optional[str] = None
    justification: Optional[str] = None
    justification_oncologique: Optional[str] = None
    type_histologique: Optional[str] = None
    stade_tnm: Optional[str] = None
    confiance: Optional[str] = None
    niveau_certitude: Optional[str] = None


class EvidenceItem(BaseModel):
    field: Optional[str] = None
    quote: Optional[str] = None
    source: Optional[str] = None
    confidence: Optional[Literal["low", "medium", "high"]] = None
    note: Optional[str] = None


class AlertesQualite(BaseModel):
    besoin_verification_humaine: Optional[bool] = False
    champs_manquants: List[str] = Field(default_factory=list)


class ValidationQualiteExpert(BaseModel):
    besoin_revision_clinique: Optional[bool] = None
    raisons_revision: List[str] = Field(default_factory=list)
    donnees_manquantes_critiques: List[str] = Field(default_factory=list)
    biais_methodologiques_identifies: List[str] = Field(default_factory=list)
    recommandations_amelioration: List[str] = Field(default_factory=list)
    niveau_confiance_global: Optional[str] = None
    notes_expertise: Optional[str] = None


class Qualite(BaseModel):
    besoin_revision: Optional[bool] = None
    raisons_revision: List[str] = Field(default_factory=list)
    donnees_manquantes: List[str] = Field(default_factory=list)
    confiance_globale: Optional[str] = None


class SsiOutput(BaseModel):
    task: Optional[str] = None
    version: Optional[str] = None
    timestamp_analyse: Optional[str] = None
    timestamp_cro: Optional[str] = None
    validateur: Optional[str] = None

    categorie_operation: Optional[CategorieOperation] = None
    classe_de_contamination: Optional[Union[str, ClasseDeContamination]] = None
    implant_pose: Optional[ImplantPose] = None
    voie_abord: Optional[VoieAbord] = None
    cancer_colorectal: Optional[CancerColorectal] = None

    evidence: List[EvidenceItem] = Field(default_factory=list)
    alertes_qualite: AlertesQualite = Field(default_factory=AlertesQualite)
    evidence_scientifique: Dict[str, List[EvidenceItem]] = Field(default_factory=dict)
    validation_qualite_expert: Optional[ValidationQualiteExpert] = None
    qualite: Optional[Qualite] = None


def validate_with_pydantic(data: Any) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"Parsed output is not a dict: {type(data)}")
    obj = SsiOutput.model_validate(data)
    return obj.model_dump()


# =============================================================================
# 3) Tolerant coercion of known type variants (applied before validation)
# =============================================================================

_TEXTISH = {
    ("categorie_operation", "label"), ("categorie_operation", "raison"),
    ("categorie_operation", "reason"), ("categorie_operation", "raison_methodologique"),
    ("classe_de_contamination", "justification"),
    ("classe_de_contamination", "justification_academique"),
    ("implant_pose", "type"), ("implant_pose", "details"),
    ("implant_pose", "details_anatomiques"), ("implant_pose", "materiau"),
    ("implant_pose", "citation_cro"),
    ("voie_abord", "scopie"), ("voie_abord", "justification"),
    ("voie_abord", "justification_technique"), ("voie_abord", "moment_conversion"),
    ("voie_abord", "raison_conversion"), ("voie_abord", "citation_cro"),
    ("cancer_colorectal", "value"), ("cancer_colorectal", "justification"),
    ("cancer_colorectal", "justification_oncologique"),
    ("cancer_colorectal", "type_histologique"), ("cancer_colorectal", "stade_tnm"),
}
_BOOLISH = {
    ("implant_pose", "value"),
    ("voie_abord", "conversion_scopie_vers_tomie"),
    ("categorie_operation", "est_inclus"),
    ("cancer_colorectal", "applicable"),
}
_STR_LISTS = {
    ("categorie_operation", "chop_codes_detectes"),
    ("categorie_operation", "biais_selection_identifies"),
    ("classe_de_contamination", "facteurs_confondants"),
}

_TRUE_WORDS = {"oui", "yes", "true", "1"}
_FALSE_WORDS = {"non", "no", "false", "0", "aucun"}


def _as_text(v: Any) -> Optional[str]:
    if v is None or isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return " | ".join(x for x in (_as_text(i) for i in v) if x) or None
    if isinstance(v, dict):
        return " | ".join(f"{k}: {t}" for k, t in ((k, _as_text(val)) for k, val in v.items()) if t) or None
    return str(v)


def _as_bool(v: Any) -> Optional[bool]:
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    s = str(v).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return None  # "INDECIDABLE" and similar: information absent


def coerce_payload(d: Any) -> Any:
    """Normalise the known type variants before Pydantic validation."""
    if not isinstance(d, dict):
        return d
    for (block, field) in _TEXTISH:
        sub = d.get(block)
        if isinstance(sub, dict) and field in sub:
            sub[field] = _as_text(sub[field])
    for (block, field) in _BOOLISH:
        sub = d.get(block)
        if isinstance(sub, dict) and field in sub:
            sub[field] = _as_bool(sub[field])
    for (block, field) in _STR_LISTS:
        sub = d.get(block)
        if isinstance(sub, dict) and field in sub:
            val = sub[field]
            if isinstance(val, (list, tuple)):
                sub[field] = [str(x) for x in val]
            elif val is None:
                sub[field] = []
            else:
                sub[field] = [str(val)]
    # classe_de_contamination.mots_cles_detectes: Dict[str, List[str]]
    sub = d.get("classe_de_contamination")
    if isinstance(sub, dict) and isinstance(sub.get("mots_cles_detectes"), dict):
        sub["mots_cles_detectes"] = {
            str(k): ([str(x) for x in v] if isinstance(v, (list, tuple))
                     else ([] if v is None else [str(v)]))
            for k, v in sub["mots_cles_detectes"].items()
        }
    # classe_de_contamination returned as a bare string: minimal structure
    if isinstance(d.get("classe_de_contamination"), str):
        d["classe_de_contamination"] = {"classe": d["classe_de_contamination"]}
    return d


# =============================================================================
# 4) Reading one output file of a run
# =============================================================================

_CONTENT_FIELDS = ("output", "text", "response", "content")


def raw_model_text(data: Any) -> str:
    """Raw model answer stored in an output file, whatever the wrapper."""
    row = data[0] if isinstance(data, list) and data else data
    if not isinstance(row, dict):
        return ""
    for k in _CONTENT_FIELDS:
        if isinstance(row.get(k), str):
            return row[k]
    return ""


def extract_payload_from_loaded_json(data: Any) -> pd.DataFrame:
    """Fallback for files that already contain a structured prediction."""
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
        row = data[0]

        raw_out = None
        found_field = None
        for candidate in _CONTENT_FIELDS:
            if candidate in row:
                raw_out = row[candidate]
                found_field = candidate
                break

        if raw_out is not None:
            parsed = parse_llm_output_to_dict(raw_out)
            parsed = validate_with_pydantic(parsed)

            meta = {k: v for k, v in row.items() if k != found_field}
            df_meta = pd.json_normalize(meta)
            df_parsed = pd.json_normalize(parsed)
            return pd.concat(
                [df_meta.reset_index(drop=True), df_parsed.reset_index(drop=True)],
                axis=1,
            )

        normalized_rows = []
        for item in data:
            if isinstance(item, dict):
                normalized_rows.append(validate_with_pydantic(item))
            else:
                normalized_rows.append(item)
        return pd.json_normalize(normalized_rows)

    if isinstance(data, dict):
        parsed = validate_with_pydantic(data)
        return pd.json_normalize(parsed)

    if isinstance(data, list):
        return pd.json_normalize(data)

    raise ValueError(f"Unexpected JSON format: {type(data)}")


def parse_output_file(path: Path) -> pd.DataFrame:
    """Read one output file of a run and return a one-row DataFrame
    (file metadata + flattened, validated prediction)."""
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)

    row = data[0] if isinstance(data, list) and data else data
    raw = raw_model_text(data)
    if not raw:
        # No text field: the file already holds a structured prediction.
        return extract_payload_from_loaded_json(data)

    parsed = coerce_payload(parse_llm_output_to_dict(raw))
    parsed = validate_with_pydantic(parsed)

    meta = {k: v for k, v in row.items() if k not in _CONTENT_FIELDS}
    df_meta = pd.json_normalize(meta)
    df_parsed = pd.json_normalize(parsed)
    return pd.concat([df_meta.reset_index(drop=True), df_parsed.reset_index(drop=True)], axis=1)


def classify_error(exc: Exception) -> str:
    """Pre-specified taxonomy of unparseable outputs."""
    name = type(exc).__name__
    if name == "ValidationError":
        return "schema_violation"
    if name == "JSONDecodeError":
        return "invalid_json"
    if NO_JSON_MESSAGE in str(exc):
        return "no_json_output"
    return "other"


# =============================================================================
# 5) Normalisation helpers used by the evaluation
# =============================================================================

def ensure_contamination_column(df: pd.DataFrame) -> pd.DataFrame:
    if "classe_de_contamination.classe" in df.columns:
        df["classe_de_contamination_pred"] = df["classe_de_contamination.classe"]
    elif "classe_de_contamination" in df.columns:
        df["classe_de_contamination_pred"] = df["classe_de_contamination"]
    else:
        df["classe_de_contamination_pred"] = np.nan
    return df


def to_bool_scopie(x: Any):
    """Minimally invasive approach as a boolean.

    Note: "INDECIDABLE" is mapped to False ("not minimally invasive"). This is
    the convention used in the study and is reported as a limitation; the
    abstention tables of analyze.py report how often it happens.
    """
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return np.nan
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        if x in (0, 1):
            return bool(x)
        return np.nan

    s = str(x).strip().lower()
    if s in {"non", "no", "false", "0", "indecidable", "indécidable"}:
        return False
    if s in {
        "oui", "yes", "true", "1",
        "laparoscopie", "minimale_invasive",
        "robot-assistee", "robot-assistée",
        "abord transanal",
    }:
        return True
    return np.nan


def norm_oui_non(x: Any) -> Optional[str]:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    if isinstance(x, (bool, np.bool_)):
        return "oui" if x else "non"
    if isinstance(x, (int, np.integer)):
        if x == 1:
            return "oui"
        if x == 0:
            return "non"
    s = str(x).strip().lower()
    if s in {"oui", "yes", "true", "1"}:
        return "oui"
    if s in {"non", "no", "false", "0"}:
        return "non"
    return None


def normalize_proc(val: Any) -> str:
    """Procedure code as a string ("6", "281", ...); missing -> "INDECIDABLE"."""
    if pd.isna(val):
        return "INDECIDABLE"
    try:
        f_val = float(val)
        return str(int(f_val))
    except (ValueError, TypeError):
        return str(val).strip()


# =============================================================================
# 6) Self-reported confidence
# =============================================================================

# Normalised buckets (values as produced by the French prompt).
CONFIDENCE_BUCKETS = ["haute", "moyenne", "faible", "je_ne_sais_pas", "null"]
CONFIDENCE_BUCKETS_EN = {
    "haute": "high",
    "moyenne": "medium",
    "faible": "low",
    "je_ne_sais_pas": "dont_know",
    "null": "not_reported",
}

_CONFIDENCE_MAP = {
    # high
    "haute": "haute", "haut": "haute", "high": "haute",
    "elevee": "haute", "élevée": "haute", "elevé": "haute", "élevé": "haute",
    # medium
    "moyenne": "moyenne", "moyen": "moyenne", "medium": "moyenne",
    "modérée": "moyenne", "moderee": "moyenne",
    "intermediaire": "moyenne", "intermédiaire": "moyenne",
    # low
    "faible": "faible", "basse": "faible", "bas": "faible", "low": "faible",
    # explicit "don't know"
    "je ne sais pas": "je_ne_sais_pas", "je_ne_sais_pas": "je_ne_sais_pas",
    "inconnue": "je_ne_sais_pas", "inconnu": "je_ne_sais_pas",
    "indecidable": "je_ne_sais_pas", "indécidable": "je_ne_sais_pas",
    "unknown": "je_ne_sais_pas", "n/a": "je_ne_sais_pas", "na": "je_ne_sais_pas",
}


def normalize_confidence(x: Any) -> str:
    """Return one of CONFIDENCE_BUCKETS."""
    if x is None:
        return "null"
    if isinstance(x, float) and pd.isna(x):
        return "null"
    s = str(x).strip().lower()
    if s == "" or s == "none" or s == "nan":
        return "null"
    return _CONFIDENCE_MAP.get(s, "null")


def pick_confidence_column(df: pd.DataFrame, prefix: str) -> Optional[str]:
    """Confidence column of a variable: `<prefix>.confiance`, else `<prefix>.niveau_certitude`."""
    for suffix in ("confiance", "niveau_certitude"):
        col = f"{prefix}.{suffix}"
        if col in df.columns:
            return col
    return None


def add_confidence_bucket(df: pd.DataFrame, prefix: str, out_col: str) -> pd.DataFrame:
    src_col = pick_confidence_column(df, prefix)
    if src_col is None:
        df[out_col] = "null"
    else:
        df[out_col] = df[src_col].map(normalize_confidence)
    return df
