"""Step 3-hosted — fine-tune on a hosted provider (Option B: no local GPU).

Validates data/<field>/{train,val}.jsonl, then (only with --submit) uploads them
to OpenAI's fine-tuning API and launches a job. OpenAI trains AND serves the
result behind an OpenAI-compatible endpoint, so eval_distilled.py can hit it
directly with the returned model id. Uses httpx (already a dep) — no SDK needed.

By DEFAULT this only validates and estimates cost (no upload, no spend). Add
--submit to actually create the job. Needs OPENAI_API_KEY in the environment.

Fireworks / Together (open-weight Qwen/Llama/Mistral) take the SAME {train,val}
.jsonl — see README.md for their CLI; only the upload/create calls differ.

Usage:
    # validate + estimate only (safe, no spend):
    python -m backend.scripts.distill.submit_openai --field sub_sector
    # actually launch the job:
    OPENAI_API_KEY=sk-... python -m backend.scripts.distill.submit_openai \
        --field sub_sector --base-model gpt-4o-mini-2024-07-18 --submit
    # then poll:
    python -m backend.scripts.distill.submit_openai --field sub_sector --job ftjob-... --poll
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from pathlib import Path

import httpx

from backend.app.fields import FIELDS

from ._common import field_dir, setup_utf8

OPENAI_BASE = "https://api.openai.com/v1"
# Rough tokens ~= chars/4; used only for a pre-flight size/cost estimate.
CHARS_PER_TOKEN = 4


def _validate(path: Path, value_key: str) -> tuple[list[dict], list[str]]:
    """Return (rows, issues). Issues are human-readable warnings/errors."""
    issues: list[str] = []
    if not path.exists():
        return [], [f"MISSING: {path} (run build_dataset_from_gt.py first)"]
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8").split("\n")):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(f"line {i}: bad JSON ({exc})")
            continue
        msgs = obj.get("messages")
        if not msgs or [m.get("role") for m in msgs] != ["system", "user", "assistant"]:
            issues.append(f"line {i}: messages must be [system, user, assistant]")
            continue
        assistant = msgs[2].get("content", "")
        if not assistant.strip():
            issues.append(f"line {i}: empty assistant target")
        else:
            try:
                tgt = json.loads(assistant)
                if value_key not in tgt:
                    issues.append(f"line {i}: assistant JSON missing '{value_key}'")
            except json.JSONDecodeError:
                issues.append(f"line {i}: assistant content is not valid JSON")
        rows.append(obj)
    return rows, issues


def _stats(rows: list[dict], value_key: str) -> None:
    tokens = [sum(len(m["content"]) for m in r["messages"]) / CHARS_PER_TOKEN for r in rows]
    tokens.sort()
    total = sum(tokens)
    n = len(tokens)
    print(f"  examples={n}  est_tokens: total~{total/1e6:.2f}M "
          f"mean~{total/n:.0f} p95~{tokens[int(n*0.95)]:.0f} max~{tokens[-1]:.0f}")
    # Class balance (categorical): flag heavy skew.
    vals = Counter()
    for r in rows:
        try:
            vals[json.loads(r["messages"][2]["content"]).get(value_key)] += 1
        except Exception:  # noqa: BLE001
            pass
    if vals:
        top, topc = vals.most_common(1)[0]
        print(f"  distinct targets={len(vals)}  most common: {top!r} = {topc/n:.0%}")
        if topc / n > 0.4:
            print(f"  [warn] class imbalance: {top!r} is {topc/n:.0%} of examples — consider "
                  "capping it or class-weighting; a model can 'pass' by over-predicting it.")


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def _upload(base: str, key: str, path: Path) -> str:
    resp = httpx.post(f"{base}/files", headers=_headers(key),
                      files={"file": (path.name, path.read_bytes(), "application/jsonl")},
                      data={"purpose": "fine-tune"}, timeout=300.0)
    resp.raise_for_status()
    fid = resp.json()["id"]
    print(f"  uploaded {path.name} -> {fid}")
    return fid


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--base-model", default="gpt-4o-mini-2024-07-18",
                    help="OpenAI base model to fine-tune (default: %(default)s)")
    ap.add_argument("--suffix", default=None, help="name suffix for the resulting model")
    ap.add_argument("--epochs", type=int, default=None, help="n_epochs (default: OpenAI auto)")
    ap.add_argument("--submit", action="store_true", help="actually upload + create the job (spends $)")
    ap.add_argument("--job", default=None, help="existing job id to poll")
    ap.add_argument("--poll", action="store_true", help="poll --job until it finishes")
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", OPENAI_BASE))
    args = ap.parse_args()

    key = os.environ.get("OPENAI_API_KEY", "")
    value_key = "value" if FIELDS[args.field].value_type == "single_categorical" else "values"

    # --- poll mode -----------------------------------------------------------
    if args.job:
        if not key:
            raise SystemExit("OPENAI_API_KEY not set.")
        while True:
            r = httpx.get(f"{args.base_url}/fine_tuning/jobs/{args.job}", headers=_headers(key), timeout=60.0)
            r.raise_for_status()
            j = r.json()
            print(f"status={j['status']}  model={j.get('fine_tuned_model')}  "
                  f"trained_tokens={j.get('trained_tokens')}")
            if j["status"] in ("succeeded", "failed", "cancelled") or not args.poll:
                if j["status"] == "succeeded":
                    print(f"\nDone. Evaluate with:\n  OPENAI_API_KEY=$OPENAI_API_KEY \\\n"
                          f"  python -m backend.scripts.distill.eval_distilled --field {args.field} \\\n"
                          f"    --base-url {OPENAI_BASE} \\\n"
                          f"    --test-ids {field_dir(args.field)/'splits.json'} \\\n"
                          f"    --models '<teacher-id>,{j.get('fine_tuned_model')}'")
                break
            time.sleep(30)
        return

    # --- validate (always) ---------------------------------------------------
    fd = field_dir(args.field)
    print(f"Validating {fd}/train.jsonl and val.jsonl ...")
    train, ti = _validate(fd / "train.jsonl", value_key)
    val, vi = _validate(fd / "val.jsonl", value_key)
    for msg in (ti + vi)[:20]:
        print(f"  ! {msg}")
    if ti or vi:
        raise SystemExit(f"Validation found {len(ti)+len(vi)} issue(s); fix before submitting.")
    print("train:"); _stats(train, value_key)
    print("val:");   _stats(val, value_key)

    # Cost pre-flight: fine-tuning is billed per TRAINED token (~= train tokens x
    # epochs). Multiply by your provider's current FT training $/1M tokens.
    train_tokens = sum(sum(len(m["content"]) for m in r["messages"]) / CHARS_PER_TOKEN for r in train)
    epochs = args.epochs or 3  # OpenAI auto-picks ~3 for a dataset this size
    trained_m = train_tokens * epochs / 1e6
    print(f"\ncost pre-flight: ~{trained_m:.1f}M trained tokens at {epochs} epochs. "
          f"At e.g. $3/1M (check current FT pricing) ≈ ${trained_m*3:.0f}. "
          "Inference on the tuned model is billed separately, per call.")

    if not args.submit:
        print("\nValidation OK. Re-run with --submit (and OPENAI_API_KEY set) to launch the job.")
        return

    # --- submit --------------------------------------------------------------
    if not key:
        raise SystemExit("OPENAI_API_KEY not set — required with --submit.")
    print("\nUploading files...")
    train_fid = _upload(args.base_url, key, fd / "train.jsonl")
    val_fid = _upload(args.base_url, key, fd / "val.jsonl")
    body: dict = {"training_file": train_fid, "validation_file": val_fid, "model": args.base_model}
    if args.suffix:
        body["suffix"] = args.suffix
    if args.epochs:
        body["hyperparameters"] = {"n_epochs": args.epochs}
    resp = httpx.post(f"{args.base_url}/fine_tuning/jobs", headers=_headers(key), json=body, timeout=60.0)
    resp.raise_for_status()
    job = resp.json()
    print(f"\nCreated job {job['id']} (status={job['status']}). Poll with:\n"
          f"  python -m backend.scripts.distill.submit_openai --field {args.field} "
          f"--job {job['id']} --poll")


if __name__ == "__main__":
    main()
