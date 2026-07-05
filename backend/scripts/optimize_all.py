"""Runs the GEPA-lite optimizer for every (field, model) combination in one
sweep, applying a cross-family reflector rule so a model is never
self-critiqued by a same-family model: Anthropic models are reflected on by
~openai/gpt-latest; everything else is reflected on by
~anthropic/claude-opus-latest.

One failing (field, model) pair does not stop the sweep -- it's logged and
the script moves on, so a long unattended run survives individual failures
(e.g. a model that's still rate-limited upstream).

Usage (from DEP root, .venv active):
    python -m backend.scripts.optimize_all
    python -m backend.scripts.optimize_all --fields sector_name,authors
    python -m backend.scripts.optimize_all --skip-models "meta-llama/llama-3.3-70b-instruct:free,qwen/qwen3-coder:free,google/gemma-4-26b-a4b-it:free"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import config, gateway  # noqa: E402
from backend.app.optimizer import optimize_field  # noqa: E402
from backend.app.prompts import BASELINE_INSTRUCTIONS  # noqa: E402

ANTHROPIC_REFLECTOR = "~openai/gpt-latest"
DEFAULT_REFLECTOR = "~anthropic/claude-opus-latest"


def reflector_for(model_id: str) -> str:
    return ANTHROPIC_REFLECTOR if model_id.startswith("~anthropic/") else DEFAULT_REFLECTOR


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fields", type=str, default=None, help="comma-separated fields (default: all 5)")
    ap.add_argument("--models", type=str, default=None, help="comma-separated model ids (default: full models.yaml roster)")
    ap.add_argument("--skip-models", type=str, default=None, help="comma-separated model ids to exclude")
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--no-improve-limit", type=int, default=3)
    ap.add_argument("--minibatch-size", type=int, default=8)
    ap.add_argument("--val-size", type=int, default=12)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--candidates-per-iter", type=int, default=1)
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else list(BASELINE_INSTRUCTIONS.keys())
    if args.models:
        models = [m.strip() for m in args.models.split(",")]
    else:
        models = [m["id"] for m in config.load_models()]
    skip = {m.strip() for m in args.skip_models.split(",")} if args.skip_models else set()
    models = [m for m in models if m not in skip]

    total = len(fields) * len(models)
    done = 0
    print(f"Optimizing {len(models)} models x {len(fields)} fields = {total} runs\n")

    for field in fields:
        for model_id in models:
            done += 1
            reflector = reflector_for(model_id)
            print(f"=== [{done}/{total}] field={field} model={model_id} reflector={reflector} ===")
            try:
                result = optimize_field(
                    field_name=field,
                    model_id=model_id,
                    reflector_model=reflector,
                    max_iterations=args.max_iterations,
                    no_improve_limit=args.no_improve_limit,
                    minibatch_size=args.minibatch_size,
                    val_size=args.val_size,
                    seed=42,
                    max_workers=args.concurrency,
                    candidates_per_iteration=args.candidates_per_iter,
                )
                print(f"  baseline={result.baseline_score:.3f} best={result.best_score:.3f} "
                      f"iters={len(result.iterations)}\n")
            except Exception as exc:  # noqa: BLE001 - one failing pair must not kill the whole sweep
                print(f"  [error] {field}/{model_id}: {exc!r}\n")

    print("Sweep complete.")


if __name__ == "__main__":
    main()
