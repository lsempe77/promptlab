"""Step 3-hosted (Fireworks) — fine-tune a small OPEN model via LoRA, then serve
it cheaply (serverless LoRA at base-model token rates). Option B, no local GPU.

Fireworks takes the SAME data/<field>/{train,val}.jsonl this repo already builds.
This script does the tested, local part — validate the data + a token/cost
pre-flight (reusing the OpenAI validator) — and then prints an exact `firectl`
command playbook with your paths, base model, account, and endpoint filled in.

Why a printed playbook rather than auto-running firectl: firectl subcommand
syntax drifts between versions, so running it blind would be fragile. Copy the
printed commands (verify against `firectl --help` / docs.fireworks.ai) and run
them; each is parameterised for you. eval_distilled.py then hits the Fireworks
OpenAI-compatible endpoint directly.

Usage:
    python -m backend.scripts.distill.submit_fireworks --field sub_sector \
        --account my-fw-account --model-id dep-sub-sector-v1
"""
from __future__ import annotations

import argparse
import os

from backend.app.fields import FIELDS

from ._common import field_dir, setup_utf8
from .submit_openai import CHARS_PER_TOKEN, _stats, _validate

FW_ENDPOINT = "https://api.fireworks.ai/inference/v1"
DEFAULT_BASE = "accounts/fireworks/models/qwen2p5-14b-instruct"


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--base-model", default=DEFAULT_BASE,
                    help="Fireworks base model to LoRA-tune (default: %(default)s)")
    ap.add_argument("--account", default=os.environ.get("FIREWORKS_ACCOUNT_ID", "<your-account>"),
                    help="Fireworks account id (or set FIREWORKS_ACCOUNT_ID)")
    ap.add_argument("--model-id", default=None, help="name for the tuned model (default: dep-<field>)")
    ap.add_argument("--epochs", type=int, default=2, help="training epochs (pilot default: 2)")
    ap.add_argument("--lora-rank", type=int, default=16)
    args = ap.parse_args()

    fd = field_dir(args.field)
    value_key = "value" if FIELDS[args.field].value_type == "single_categorical" else "values"
    model_id = args.model_id or f"dep-{args.field.replace('_', '-')}"

    # --- validate (tested, local) --------------------------------------------
    print(f"Validating {fd}/train.jsonl and val.jsonl ...")
    train, ti = _validate(fd / "train.jsonl", value_key)
    val, vi = _validate(fd / "val.jsonl", value_key)
    for msg in (ti + vi)[:20]:
        print(f"  ! {msg}")
    if ti or vi:
        raise SystemExit(f"Validation found {len(ti)+len(vi)} issue(s); fix before submitting.")
    print("train:"); _stats(train, value_key)
    print("val:");   _stats(val, value_key)

    train_tokens = sum(sum(len(m["content"]) for m in r["messages"]) / CHARS_PER_TOKEN for r in train)
    trained_m = train_tokens * args.epochs / 1e6
    print(f"\ncost pre-flight: ~{trained_m:.1f}M trained tokens ({args.epochs} epochs). Fireworks "
          "LoRA tuning is typically cheaper per token than OpenAI, and the big win is INFERENCE: a "
          "serverless LoRA is billed at the small base model's token rate (no fine-tune premium). "
          "Check current pricing at fireworks.ai/pricing.")

    dataset_id = f"dep-{args.field.replace('_', '-')}-train"
    tuned = f"accounts/{args.account}/models/{model_id}"
    print("\n=== firectl playbook (verify against `firectl --help` / docs.fireworks.ai) ===")
    print("# 0. one-time: install firectl + auth")
    print("#    https://docs.fireworks.ai/tools-sdks/firectl/firectl ; export FIREWORKS_API_KEY=...")
    print(f"\n# 1. upload the training set as a dataset")
    print(f"firectl create dataset {dataset_id} {fd/'train.jsonl'}")
    print(f"\n# 2. launch a LoRA supervised fine-tuning job")
    print(f"firectl create sftj \\\n"
          f"    --base-model {args.base_model} \\\n"
          f"    --dataset {dataset_id} \\\n"
          f"    --output-model {model_id} \\\n"
          f"    --lora-rank {args.lora_rank} \\\n"
          f"    --epochs {args.epochs}")
    print(f"\n# 3. when the job succeeds, deploy for serverless LoRA inference")
    print(f"firectl deploy {tuned}")
    print(f"\n# 4a. evaluate the STUDENT on the held-out TEST split (Fireworks endpoint)")
    print(f"python -m backend.scripts.distill.eval_distilled --field {args.field} \\\n"
          f"    --base-url {FW_ENDPOINT} --api-key $FIREWORKS_API_KEY \\\n"
          f"    --test-ids {fd/'splits.json'} \\\n"
          f"    --models '{tuned}'")
    print(f"\n# 4b. evaluate the TEACHER on the SAME test split (OpenRouter, default endpoint)")
    print(f"python -m backend.scripts.distill.eval_distilled --field {args.field} \\\n"
          f"    --test-ids {fd/'splits.json'} \\\n"
          f"    --models '~anthropic/claude-sonnet-latest'")
    print("\nThen compare the two printouts (gate metric + $/rec + gCO2e/rec). Teacher and student "
          "are on different endpoints, so eval_distilled's --base-url/--api-key (applied to all "
          "listed models) means one run per endpoint — not both in a single --models list.")


if __name__ == "__main__":
    main()
