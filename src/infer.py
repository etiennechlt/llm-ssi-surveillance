# -*- coding: utf-8 -*-
"""Inference client: send each operative report to an OpenAI-compatible
chat-completions endpoint (vLLM, llama.cpp server, LiteLLM gateway, ...) and
store the raw answer of the model.

Input : a directory of JSON records, one per document:
            {"patient_id": "...", "eds_id": "...", "doc_id": "...", "text": "<report text>"}
        (see pdf_to_text.py and README.md).
Output: one file per input record, same file name, in --out-dir:
            [{"patient_id": "...", "eds_id": "...", "doc_id": "...", "text": "<raw model answer>"}]
        The answer is parsed later by analyze.py (parsing.py).

Settings of the published runs: temperature 0.1, no max_tokens (server
default), thinking disabled, one request at a time, 540 s timeout for the
first pass and 1800 s for the single resubmission of failed documents,
warm-up call to fill the prefix cache with the constant system prompt.

Example
-------
    export OPENAI_API_KEY=...            # only if the endpoint requires a key
    python src/infer.py --base-url http://localhost:8000/v1 --model my-served-model \
        --in-dir data/texts --out-dir runs/my-model --warmup

The run can be resumed: files already present in --out-dir are skipped (an
existing output is never rewritten, even if it is unusable; delete it to have
it processed again).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx
from tqdm.asyncio import tqdm as atqdm

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_BASE_URL = "http://localhost:8000/v1"
DEFAULT_SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system_prompt_reasoning_v6_2.md"
DEFAULT_CONCURRENCY = 1
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TIMEOUT = 540
PLACEHOLDER = "{{CRO_TEXT}}"


# -----------------------------------------------------------------------------
# Prompts
# -----------------------------------------------------------------------------

def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_user_prompt(template: str, text: str) -> str:
    """Insert the report text into the user message.

    If the template contains {{CRO_TEXT}}, the placeholder is replaced by the
    text. Otherwise (no template, or a template without this placeholder) the
    built-in wrapper below is used. The built-in wrapper is the user message
    that was actually sent in the published runs; it is also provided as
    prompts/user_template_as_sent.md.
    """
    if PLACEHOLDER in template:
        return template.replace(PLACEHOLDER, text)
    return (
        "```xml\n"
        "<task>\n"
        "  Analyser le compte-rendu opératoire ci-dessous et produire la classification\n"
        "  SwissNoso SSI selon le protocole d'expertise senior en 5 étapes.\n"
        "</task>\n\n"
        "<compte_rendu_operatoire>\n"
        f"{text}\n"
        "</compte_rendu_operatoire>\n\n"
        "<instruction>\n"
        "  Appliquer le protocole complet en 5 étapes séquentielles.\n"
        "  Produire UNIQUEMENT le bloc JSON de sortie, sans texte libre avant ni après.\n"
        "  En cas de données manquantes, utiliser \"INDECIDABLE\" avec justification dans le champ concerné.\n"
        "</instruction>\n"
        "```"
    )


# -----------------------------------------------------------------------------
# LLM call
# -----------------------------------------------------------------------------

async def call_llm(client: httpx.AsyncClient, model: str, system_prompt: str, user_message: str,
                   timeout: float, temperature: float = DEFAULT_TEMPERATURE,
                   enable_thinking: bool = False, top_level_template_kwargs: bool = False) -> dict:
    """POST /chat/completions and return the JSON response."""
    payload = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        # As in the published runs, which went through a LiteLLM gateway that
        # forwards `extra_body` to the vLLM backend.
        "extra_body": {"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    }
    if top_level_template_kwargs:
        # A vLLM server called directly ignores `extra_body` and reads
        # `chat_template_kwargs` at the top level of the request.
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking}

    resp = await client.post("/chat/completions", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def answer_text(raw_response: dict) -> str:
    """Text of the answer, for the OpenAI format and for content-block lists."""
    content_blocks = raw_response.get("content", [])
    choices = raw_response.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content")
        if isinstance(content, list):
            # With thinking enabled some servers return a list of blocks:
            # [{"type": "thinking", ...}, {"type": "text", "text": "..."}]
            text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
            if not text:
                text = "\n".join(b.get("text", "") or b.get("thinking", "") for b in content)
            return text
        return content or ""
    if content_blocks:
        return " ".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
    return ""


def append_error(out_dir: Path, payload: dict) -> None:
    """Log a failed request (identifiers and error message only, never text)."""
    with (out_dir / "_errors.ndjson").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# -----------------------------------------------------------------------------
# One record
# -----------------------------------------------------------------------------

async def process_record(sem: asyncio.Semaphore, client: httpx.AsyncClient, record: dict,
                         system_prompt: str, user_template: str, args: argparse.Namespace,
                         out_dir: Path, filename: str) -> dict:
    ids = {k: record.get(k, "") for k in ("patient_id", "eds_id", "doc_id")}
    user_msg = build_user_prompt(user_template, record.get("text", ""))

    async with sem:
        t0 = time.perf_counter()
        try:
            raw_response = await call_llm(
                client, args.model, system_prompt, user_msg, timeout=args.timeout,
                temperature=args.temperature, enable_thinking=args.thinking,
                top_level_template_kwargs=args.top_level_template_kwargs,
            )
        except httpx.HTTPStatusError as e:
            err = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            append_error(out_dir, {"file": filename, "doc_id": ids["doc_id"], "error": err})
            return {**ids, "error": err}
        except Exception as e:  # timeouts stringify to "", keep the type name
            err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            append_error(out_dir, {"file": filename, "doc_id": ids["doc_id"], "error": err})
            return {**ids, "error": err}
        elapsed = time.perf_counter() - t0

    text = answer_text(raw_response)
    if not text:
        debug_path = out_dir / f"_debug_{filename}"
        debug_path.write_text(json.dumps(raw_response, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  empty answer for {filename}, raw response saved to {debug_path}")

    usage = raw_response.get("usage", {}) or {}
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if cached is not None:
        pct = cached / usage["prompt_tokens"] * 100 if usage.get("prompt_tokens") else 0
        print(f"  cache={cached} tok ({pct:.0f}%) prompt={usage.get('prompt_tokens')} "
              f"out={usage.get('completion_tokens')} elapsed={elapsed:.1f}s")

    result = {**ids, "text": text}
    (out_dir / filename).write_text(json.dumps([result], ensure_ascii=False, indent=2), encoding="utf-8")
    return result


async def warmup(client: httpx.AsyncClient, args: argparse.Namespace, system_prompt: str) -> None:
    """One minimal call to fill the server's prefix cache with the system prompt."""
    print("Warm-up of the prefix cache ...")
    try:
        r = await call_llm(client, args.model, system_prompt, "ping", timeout=args.timeout,
                           temperature=args.temperature, enable_thinking=args.thinking,
                           top_level_template_kwargs=args.top_level_template_kwargs)
        usage = r.get("usage", {}) or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", "n/a")
        print(f"   warm-up OK, prompt_tokens={usage.get('prompt_tokens')}, cached={cached}")
    except Exception as e:
        print(f"   warm-up failed ({type(e).__name__}: {e}), continuing")


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    in_dir = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    system_prompt = load_prompt(Path(args.system_prompt))
    user_template = load_prompt(Path(args.user_template)) if args.user_template else ""
    if args.user_template and PLACEHOLDER not in user_template:
        print(f"WARNING: {args.user_template} has no {PLACEHOLDER} placeholder; "
              f"the built-in wrapper is used instead (see build_user_prompt).")
    print(f"System prompt: {len(system_prompt)} characters (about {len(system_prompt) // 4} tokens)")

    input_files = sorted(in_dir.glob("*.json"))
    if not input_files:
        print(f"No .json file found in {in_dir}")
        return
    print(f"{len(input_files)} input file(s), outputs in {out_dir}")

    already_done = {p.name for p in out_dir.glob("*.json")}
    pending = [p for p in input_files if p.name not in already_done]
    if already_done:
        print(f"{len(already_done)} already processed, {len(pending)} remaining")
    if not pending:
        print("Nothing to do.")
        return

    sem = asyncio.Semaphore(args.concurrency)
    limits = httpx.Limits(max_connections=args.concurrency + 2)
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, limits=limits,
                                 timeout=args.timeout) as client:
        if args.warmup:
            await warmup(client, args, system_prompt)

        def read_record(p: Path) -> dict:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            return data[0] if isinstance(data, list) and data else data

        tasks = [
            asyncio.create_task(process_record(sem, client, read_record(p), system_prompt,
                                               user_template, args, out_dir, p.name))
            for p in pending
        ]

        results = []
        for fut in atqdm.as_completed(tasks, total=len(tasks), desc="Classification"):
            try:
                results.append(await fut)
            except Exception as e:
                append_error(out_dir, {"file": "", "error": f"{type(e).__name__}: {e}",
                                       "stage": "as_completed"})
                results.append({"error": str(e) or type(e).__name__})

    errors = [r for r in results if isinstance(r, dict) and "error" in r]
    print("\n" + "-" * 50)
    print(f"Written : {len(results) - len(errors)}/{len(results)}")
    if errors:
        print(f"Failed  : {len(errors)} (no output file written; see {out_dir / '_errors.ndjson'})")
        for e in errors[:10]:
            print(f"   doc_id={e.get('doc_id', '?')}: {e['error'][:100]}")


def cli() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", required=True, help="directory of input JSON records")
    ap.add_argument("--out-dir", required=True, help="output directory (one file per record)")
    ap.add_argument("--model", required=True, help="model name as served by the endpoint")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL,
                    help="OpenAI-compatible base URL (default %(default)s)")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY"),
                    help="bearer token; defaults to the OPENAI_API_KEY environment variable")
    ap.add_argument("--system-prompt", default=str(DEFAULT_SYSTEM_PROMPT),
                    help="system prompt file (default: frozen prompt v6.2)")
    ap.add_argument("--user-template", default=None,
                    help="optional user template containing {{CRO_TEXT}}; without it the "
                         "built-in wrapper used in the published runs is sent")
    ap.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    ap.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                    help="parallel requests (default %(default)s)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help="per-request timeout in seconds (default %(default)s; 1800 was used "
                         "for the resubmission of failed documents)")
    ap.add_argument("--warmup", action="store_true",
                    help="send one warm-up request to fill the prefix cache")
    ap.add_argument("--thinking", action="store_true",
                    help="request enable_thinking=True from the chat template (off in the paper)")
    ap.add_argument("--top-level-template-kwargs", action="store_true",
                    help="also send chat_template_kwargs at the top level (needed to switch "
                         "thinking off when calling a vLLM server directly)")
    return ap.parse_args()


if __name__ == "__main__":
    asyncio.run(main(cli()))
