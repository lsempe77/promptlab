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

from backend.app import gateway, optimizer  # noqa: E402
from backend.app.optimizer import optimize_field  # noqa: E402
from backend.app.prompts import BASELINE_INSTRUCTIONS  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="dep-extraction", help="project slug (see backend/app/projects.py)")
    ap.add_argument("--field", required=True, choices=list(BASELINE_INSTRUCTIONS.keys()))
    ap.add_argument("--model", required=True, help="model being optimized for (the one that runs the extraction)")
    ap.add_argument("--reflector-model", required=True, help="stronger model used to propose revised instructions")
    ap.add_argument("--max-iterations", type=int, default=10)
    ap.add_argument("--no-improve-limit", type=int, default=4,
                     help="stop after this many consecutive rejected iterations (bold mode kicks in before then)")
    ap.add_argument("--minibatch-size", type=int, default=8)
    ap.add_argument("--val-size", type=int, default=50, help="fixed number of records held out for LLM-judged candidate comparison (seed 42 => stable set; every candidate is re-judged on all of them each iteration, so cost scales linearly with this)")
    ap.add_argument("--holdout-size", type=int, default=optimizer.DEFAULT_HOLDOUT_SIZE,
                     help="records held out ENTIRELY from selection, used only for the cross-model generalization gate (seed 42, disjoint from val)")
    ap.add_argument("--holdout-models", default=None,
                     help="comma-separated models for the cross-model generalization check (default: the optimized model + a cheap different-family reference)")
    ap.add_argument("--bold-after", type=int, default=optimizer.DEFAULT_BOLD_AFTER,
                     help="after this many consecutive rejections, ask the reflector for a bold/structural rewrite instead of a small edit")
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
        project_slug=args.project,
        max_iterations=args.max_iterations,
        no_improve_limit=args.no_improve_limit,
        minibatch_size=args.minibatch_size,
        val_size=args.val_size,
        holdout_size=args.holdout_size,
        holdout_models=[m.strip() for m in args.holdout_models.split(",") if m.strip()] if args.holdout_models else None,
        bold_after=args.bold_after,
        seed=args.seed,
        max_workers=args.concurrency,
        candidates_per_iteration=args.candidates_per_iter,
        history_window=args.history_window,
    )

    print("\n=== Optimization summary ===")
    print(f"Field: {result.field_name} | model: {args.model} | reflector: {args.reflector_model}")
    print(f"Baseline gate metric (val): {result.baseline_score:.3f}")
    print(f"Best gate metric (val):     {result.best_score:.3f}")
    print(f"Iterations run:     {len(result.iterations)}")
    print(f"\nBest instruction:\n{result.best_instruction}")


if __name__ == "__main__":
    main()
