# Extraction of Swissnoso SSI surveillance variables from operative reports with on-premise LLMs

Code and prompts accompanying the paper:

> [paper title]. [authors]. [journal, year]. [paper link]

The study evaluated open-weight large language models (LLMs), run entirely on hospital infrastructure, that read French-language operative reports and fill in five fields of the Swissnoso surgical site infection (SSI) surveillance form:

1. procedure category (Swissnoso procedure code and eligibility, "excluded" being a valid answer);
2. wound contamination class (Altemeier I to IV);
3. implant (non-autologous prosthetic material left in place);
4. minimally invasive approach;
5. colorectal cancer (colorectal procedures only).

For every field the model returns a decision, a justification, a verbatim quotation from the report and a self-reported confidence level, or abstains ("INDECIDABLE"). Predictions are compared with the surveillance forms completed by infection prevention and control nurses.

**No patient data are distributed with this repository.** The operative reports and the surveillance data of the study cannot be shared (see [Data and confidentiality](#data-and-confidentiality)). A small synthetic example, written for this repository, lets you run every step.

## Repository layout

```
prompts/
  system_prompt_reasoning_v6_2.md   frozen system prompt v6.2 used in the paper (French)
  user_template_as_sent.md          user message wrapper actually sent in the published runs
  user_prompt_reasoning_v6.md       checklist user template (see "User message" below)
src/
  pdf_to_text.py                    PDF (via a local Docling server) or .txt -> JSON input records
  split_dataset.py                  patient-level development / validation split
  add_pathology_reports.py          append pathology reports to the records (sub-study)
  infer.py                          inference client for any OpenAI-compatible endpoint
  parsing.py                        tolerant JSON extraction, coercion, Pydantic schema
  metrics.py                        quadratic weighted kappa, exact McNemar test, Wilson interval
  analyze.py                        linkage to the reference standard, metrics, tables, figures
  derived_stats.py                  derived statistics of the paper, recomputed from the runs
scripts/
  make_flow_figure.py               Figure 1 (pipeline and study flow, aggregate counts)
  make_polar_variables.py           per-variable polar charts (aggregate metrics)
  derived_stats_published.py        confidence intervals recomputed from the published counts
examples/synthetic/
  reports/                          three fictitious operative reports (French)
  expected_output/                  hand-written reference answers in the run output format
  reference/                        fictitious document and operation tables
```

## Pipeline

```
operative report (PDF)
  -> plain text (pdf_to_text.py: local Docling server + light cleaning)
  -> JSON record {patient_id, eds_id, doc_id, text}
  -> LLM call (infer.py): frozen system prompt v6.2 + user message wrapping the text,
     temperature 0.1, thinking disabled, one request at a time
  -> raw answer stored per document
  -> parsing and schema validation (parsing.py): JSON block extraction, light repairs,
     coercion of known type variants, Pydantic schema; unparseable outputs classified as
     no JSON / invalid JSON / schema violation / other
  -> linkage of each document to one surveilled operation on (patient_id, date) (analyze.py)
  -> metrics per variable (analyze.py, derived_stats.py): accuracy with Wilson 95% CI,
     weighted and macro F1, Cohen's kappa, quadratic weighted kappa for the contamination
     class, sensitivity/specificity/PPV/NPV for binary fields, abstentions, confidence
     distribution, all predictions and high-confidence predictions only, exact McNemar test
     for paired comparisons
  -> tables (Markdown, CSV) and figures (PNG)
```

## Installation

```bash
git clone https://github.com/<owner>/llm-ssi-surveillance.git
cd llm-ssi-surveillance
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Python 3.10 or later. The scripts are run from the repository root as `python src/<script>.py`; no package installation is needed.

You also need:

- an OpenAI-compatible chat-completions endpoint serving the model to evaluate, for example a local [vLLM](https://github.com/vllm-project/vllm) server (`vllm serve <model> --port 8000`), a llama.cpp server or a LiteLLM gateway;
- for PDF input only, a local [docling-serve](https://github.com/docling-project/docling-serve) instance. Plain-text reports (`.txt`) need no converter.

Keep both services inside your institution when you process real reports.

## Input formats

### Operative reports

`pdf_to_text.py` reads a folder of `.pdf` and/or `.txt` files and writes one JSON record per document:

```json
{"patient_id": "P0001", "eds_id": "S0001", "doc_id": "EX-0001", "text": "COMPTE-RENDU OPÉRATOIRE ..."}
```

Identifiers are taken from an optional metadata table (`--metadata`, columns `doc_id` or `_id`, `patient_id`, `eds_id`) matched on the file name. Use pseudonymous identifiers. `infer.py` accepts any folder of such records, whatever produced them.

Example of a report (fictitious, written for this repository, abridged; the three full example reports are in `examples/synthetic/reports/`):

```text
EXEMPLE FICTIF : ce compte-rendu a été rédigé pour la documentation du code.
Il ne concerne aucun patient réel.

COMPTE-RENDU OPÉRATOIRE

Indication : adénocarcinome du côlon ascendant (biopsies coloscopiques), sans signe
d'occlusion ni de perforation.

Intervention : hémicolectomie droite par laparoscopie avec curage ganglionnaire.

Déroulement : open-coelioscopie ombilicale, mise en place de trois trocarts opérateurs.
Ligature des vaisseaux iléo-coliques à leur origine. Extraction de la pièce par une
mini-laparotomie médiane péri-ombilicale de 5 cm sous protection pariétale. Anastomose
iléo-colique latéro-latérale mécanique extracorporelle. Aucune souillure peropératoire.

Fermeture : aponévrose par surjet de fil résorbable, peau par agrafes.
```

### Reference standard (evaluation only)

Semicolon-separated CSV files (`--sep` to change). Column names are those of the original exports; the alternatives in brackets are also accepted.

`documents.csv`, one row per document:

| column | content |
|---|---|
| `doc_id` (`_id`) | document identifier, as in the input records |
| `patient_id` | pseudonymous patient identifier |
| `date` (`eds_passages_begin_date`, `link_date`) | date used to link the document to an operation, `YYYY-MM-DD` or `DD.MM.YYYY` |
| `eds_id` | optional stay identifier |

`operations.csv`, one row per surveilled operation:

| column | content |
|---|---|
| `patient_id` | pseudonymous patient identifier |
| `date_op` (`date`) | operation date, `YYYY-MM-DD` or `DD.MM.YYYY` |
| `procedure` | Swissnoso procedure code; codes 6, 281, 43, 44, 45 and 50 are kept by default (`--procedures`) |
| `class_contamination` | Altemeier class as an integer, 1 to 4 |
| `implant_bool` | `True` / `False` |
| `SCOPIE` | minimally invasive approach, `oui` / `non` |
| `COLORECTAL_CANCER` | `oui` / `non`, empty when not applicable |
| `eds_id` | optional; key used with `--forms` |

`SCOPIE` and `COLORECTAL_CANCER` may instead come from a separate table (`--forms`, columns `eds_id`, `SCOPIE`, `COLORECTAL_CANCER`) merged on `eds_id`, as in the study. Use text values `oui` / `non` for these two fields: a numeric column with missing values is read as floats (1.0 / 0.0), which the normalisation does not recognise.

Each parsed prediction is linked to the operation that has the same `patient_id` and the same date. Documents without a matching operation are not evaluable. The document identifier of an output file is read from its file name (`CRO_PAT-<patient>_EDS-<stay>_ID-<doc>.json` or `<prefix>.<patient>_<doc>.json`), otherwise from its `doc_id` field, otherwise from the file stem.

## Running the pipeline

### 1. Convert the reports

```bash
python src/pdf_to_text.py --input-dir data/reports --output-dir data/texts \
    --metadata data/documents.csv --docling-url http://localhost:5001/v1/convert/file
```

### 2. Split development and validation sets (optional)

```bash
python src/split_dataset.py --input data/operations_all.csv \
    --out-dev data/operations_dev.csv --out-val data/operations_val.csv
```

The split is made at the patient level and stratified on each patient's most frequent procedure (development fraction 0.20, seed 42, as in the study).

### 3. Inference on any OpenAI-compatible endpoint

```bash
export OPENAI_API_KEY=...        # only if the endpoint requires a key
python src/infer.py --base-url http://localhost:8000/v1 --model <served-model-name> \
    --in-dir data/texts --out-dir runs/<model> --warmup
```

- `--base-url` defaults to `http://localhost:8000/v1`; the API key is read from `OPENAI_API_KEY` or `--api-key`.
- The frozen system prompt v6.2 is used by default (`--system-prompt` to change it).
- Defaults follow the paper: temperature 0.1, thinking disabled, one request at a time, 540 s timeout, `max_tokens` left to the server default. `--warmup` sends one request first so that the server caches the constant system prompt.
- The request carries `extra_body.chat_template_kwargs.enable_thinking = false`, as in the published runs, which went through a LiteLLM gateway. A vLLM server called directly ignores `extra_body`; add `--top-level-template-kwargs` to send `chat_template_kwargs` at the top level and switch thinking off for hybrid reasoning models.
- One output file is written per document, with the same name as the input record: `[{"patient_id", "eds_id", "doc_id", "text": <raw answer>}]`. Failed requests (HTTP error, timeout) write no output file and are logged in `<out-dir>/_errors.ndjson` (identifiers and error message only).
- The run resumes where it stopped: existing output files are skipped and never rewritten.

**Resubmission of failed documents.** In the paper, each model processed the validation set in an initial pass, then the documents that failed were submitted once more to the same model with the same prompt and parameters, with a 1800 s timeout. To do the same, run `analyze.py` on the first pass (unparseable outputs are listed in `parse_errors_<run>.csv`), copy the run folder, delete those files from the copy and run `infer.py` again on the copy with `--timeout 1800`. Documents without an output file (timeouts, HTTP errors) are picked up automatically. Report first-pass and consolidated figures separately.

### 4. Evaluation

```bash
python src/analyze.py \
    --run "MedGemma-27B=runs/medgemma" --run "Qwen3.6-35B-A3B=runs/qwen" \
    --run "Ministral-14B=runs/ministral" --run "Apertus-8B=runs/apertus" \
    --documents data/documents.csv --operations data/operations_val.csv [--forms data/forms.csv] \
    --source-dir data/texts --out results/validation
```

Options:

- `--paired`: score every run on the documents parseable by all runs;
- `--common-files`: score every run on the documents that have an output file in all runs;
- `--mcnemar LABEL_A LABEL_B`: add a document-level comparison of two runs (exact McNemar test) and their raw agreement;
- `--source-dir`: folder of the input records, used to count outputs that only reproduce the input text.

Outputs in `--out`:

| file | content |
|---|---|
| `report.md` | coverage, rankings (all predictions and high confidence only), abstentions, confidence levels |
| `coverage.csv` | files, parsed outputs, error categories, linked documents and evaluable rows per run |
| `leaderboard.csv` | accuracy, weighted and macro F1, kappa, quadratic weighted kappa and N per run and variable |
| `abstention_summary.csv`, `confidence_summary.csv` | abstentions and self-reported confidence per run and variable |
| `report_<variable>_<run>.csv` | per-class precision, recall and F1 |
| `parse_errors_<run>.csv` | unparseable output files with their error category (file names only) |
| `paired_mcnemar.csv`, `run_agreement.csv` | with `--mcnemar` |
| `figures/` | confusion matrices per variable, F1 and kappa heatmaps, confidence heatmap |

Abstentions count as errors for the procedure category and the contamination class. For the minimally invasive approach, an abstention is mapped to "not minimally invasive" (convention of the study, reported as a limitation); `abstention_summary.csv` shows how often this happens.

### 5. Derived statistics

```bash
python src/derived_stats.py --run "MedGemma-27B=runs/medgemma" [--run ...] \
    --documents data/documents.csv --operations data/operations_val.csv [--forms data/forms.csv] \
    > derived_stats.txt
```

Prints the confusion matrices, sensitivity, specificity and predictive values with Wilson 95% CI, accuracy with CI on all and high-confidence predictions, the gain and coverage of the high-confidence filter, the distribution of the reference standard, the post-hoc harmonisation of procedure codes 46/47 into 43, and the prompt sizes.

`scripts/derived_stats_published.py` recomputes the confidence intervals and binary statistics of the paper from the published aggregate counts, without any data.

### 6. Pathology report sub-study

```bash
python src/add_pathology_reports.py --in-dir data/texts --out-dir data/texts_with_pathology \
    --reports data/pathology_reports.csv --patients data/digestive_patients.csv
python src/infer.py --model <served-model-name> --in-dir data/texts_with_pathology --out-dir runs/with_pathology
python src/analyze.py --run "without=runs/without_pathology" --run "with=runs/with_pathology" \
    --common-files --mcnemar without with \
    --documents data/documents.csv --operations data/operations_dev.csv --out results/pathology
```

The operative report and the pathology reports of the same patient are wrapped in `<compte_rendu_operatoire>` and `<rapport_pathologie>` tags. In the paper, the run without pathology reports also used an earlier prompt version, so that comparison mixes both effects.

### 7. Figures of the paper

```bash
python scripts/make_flow_figure.py --out figures/fig1_study_flow.png
python scripts/make_polar_variables.py --out-dir figures
```

Both scripts draw from the published aggregate values; they read no data.

## Synthetic example

The folder `examples/synthetic/` contains three fictitious reports (colectomy, laminectomy, coronary bypass), the answers a correct classification should give (`expected_output/`, written by hand in the run output format, not produced by a model) and fictitious reference tables.

```bash
# text records
python src/pdf_to_text.py --input-dir examples/synthetic/reports --output-dir runs/example/texts \
    --metadata examples/synthetic/reference/documents.csv

# evaluation of the hand-written answers (no model needed)
python src/analyze.py --run "Expected=examples/synthetic/expected_output" \
    --documents examples/synthetic/reference/documents.csv \
    --operations examples/synthetic/reference/operations.csv --out results/example

# with a model of your own
python src/infer.py --base-url http://localhost:8000/v1 --model <served-model-name> \
    --in-dir runs/example/texts --out-dir runs/example/my-model --warmup
python src/analyze.py --run "Expected=examples/synthetic/expected_output" \
    --run "My model=runs/example/my-model" --source-dir runs/example/texts \
    --documents examples/synthetic/reference/documents.csv \
    --operations examples/synthetic/reference/operations.csv --out results/example
```

Three documents are enough to check that the pipeline runs end to end; the metrics computed on them mean nothing.

## Prompts

The system prompt `prompts/system_prompt_reasoning_v6_2.md` (54,331 characters, about 14,000 tokens) is the frozen version evaluated on the validation set. It is written in French and encodes the surveillance rules: uncertainty rule, procedure codes with inclusion, exclusion and disambiguation rules, ordered Altemeier tests with negation handling, implant definitions per specialty, approach and conversion rules, conditional colorectal cancer field, confidence calibration and the JSON output schema, followed by eleven short worked examples. It is distributed unchanged so that the published results can be reproduced (SHA-256 `a65f39447166cbc8e3861de423153ff751517cfe4c8ed22a0a803bac5ab395fd`, UTF-8 with LF line endings).

### User message

The user message wraps the report text in the short wrapper of `prompts/user_template_as_sent.md`, which is also built into `infer.py`. In the published runs the command line also passed `--user-template prompts/user_prompt_reasoning_v6.md`, but this file uses the placeholder syntax of an earlier workflow (`{{$json.body.text}}`) instead of `{{CRO_TEXT}}`; the client therefore fell back to the built-in wrapper, and the checklist in that file was not sent. Running `infer.py` without `--user-template` reproduces the published runs. To use the checklist template, replace its placeholder with `{{CRO_TEXT}}` and pass it with `--user-template`; `infer.py` prints a warning when a template has no `{{CRO_TEXT}}` placeholder.

### Parsing

`parsing.py` takes the first fenced ```` ```json ```` block of the answer, otherwise the first balanced `{...}` object, removes trailing commas and control characters, coerces known type variants (for example a quotation returned as a list, or a boolean returned as `"oui"`) and validates the result with a Pydantic schema. Unknown fields are dropped.

## Data and confidentiality

- The operative reports and surveillance data of the study are confidential health data. They cannot be shared and are not included here, in any form. Only aggregate results are published with the paper.
- Run the models and the PDF converter on infrastructure approved for clinical data. In the study, no document left the hospital: all models were served on hospital hardware and no external API was called.
- Model outputs contain verbatim quotations of the reports and must be treated as clinical data. `.gitignore` excludes data folders, run outputs, CSV, JSON, PDF, logs and `.env` files, except the synthetic example; check `git status` before every commit.
- The cleaning step of `pdf_to_text.py` removes a few administrative header lines. It is not a de-identification tool.

## Limitations

- The results of the paper cannot be reproduced without the study data; the code documents exactly how they were computed and can be applied to other data.
- The prompt, the field definitions and the cleaning rules are specific to French-language reports and to the local Swissnoso surveillance module (procedure codes 6, 281, 43, 44, 45 and 50). Other institutions will need to adapt them and validate the models on their own reports.
- The reference standard is the routine surveillance form, which is itself imperfect.
- Outputs at temperature 0.1 are not strictly deterministic, and they depend on the model weights, the quantisation and the serving software.
- The linkage of documents to operations on patient and date excludes documents that cannot be linked, so denominators differ between models and variables.

## Citation

If you use this code, please cite the paper:

```bibtex
@article{[key],
  title   = {[paper title]},
  author  = {[authors]},
  journal = {[journal]},
  year    = {[year]},
  doi     = {[doi]}
}
```
