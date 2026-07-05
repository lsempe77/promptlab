"""CLI entry point for the GEPA-lite prompt optimizer.

Usage (from DEP root, .venv active):
    python -m backend.scripts.optimize_prompt --field sector_name \
        --model openai/gpt-4o-mini --reflector-model openai/gpt-4o \
        --max-iterations 8 --no-improve-limit 3

The reflector model should generally be a stronger/pricier model than the
one being optimized for — it's proposing the instruction, not following it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import gateway  # noqa: E402
from backend.app.optimizer import optimize_field  # noqa: E402
from backend.app.prompts import BASELINE_INSTRUCTIONS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, choices=list(BASELINE_INSTRUCTIONS.keys()))
    ap.add_argument("--model", required=True, help="model being optimized for (the one that runs the extraction)")
    ap.add_argument("--reflector-model", required=True, help="stronger model used to propose revised instructions")
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--no-improve-limit", type=int, default=3)
    ap.add_argument("--minibatch-size", type=int, default=8)
    ap.add_argument("--val-size", type=int, default=12, help="fixed number of records held out for candidate comparison")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY,
                     help="max concurrent API calls per evaluation batch")
    ap.add_argument("--candidates-per-iter", type=int, default=1,
                     help="best-of-N: propose this many independent revisions per iteration, keep only the best")
    ap.add_argument("--history-window", type=int, default=3,
                     help="how many recently-rejected instructions to show the reflector, to avoid repeats")
    args = ap.parse_args()

    result = optimize_field(
        field_name=args.field,
        model_id=args.model,
        reflector_model=args.reflector_model,
        max_iterations=args.max_iterations,
        no_improve_limit=args.no_improve_limit,
        minibatch_size=args.minibatch_size,
        val_size=args.val_size,
        seed=args.seed,
        max_workers=args.concurrency,
        candidates_per_iteration=args.candidates_per_iter,
        history_window=args.history_window,
    )

    print("\n=== Optimization summary ===")
    print(f"Field: {result.field_name} | model: {args.model} | reflector: {args.reflector_model}")
    print(f"Baseline val score: {result.baseline_score:.3f}")
    print(f"Best val score:     {result.best_score:.3f}")
    print(f"Iterations run:     {len(result.iterations)}")
    print(f"\nBest instruction:\n{result.best_instruction}")


if __name__ == "__main__":
    main()
