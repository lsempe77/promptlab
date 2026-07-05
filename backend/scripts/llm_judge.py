"""Posterior LLM-as-judge pass over already-logged runs.

Our automated scorer (`app/scoring.py`) is deliberately simple: exact string
match, rapidfuzz fuzzy match, and set-based F1 for list fields. That's fast
and free, but it's a string-matching heuristic, not a semantic judgment — it
can be wrong in both directions (e.g. "USA" vs "United States" is obviously
the same country to a human but may fail fuzzy matching; conversely a fuzzy
match above threshold can still be a real error).

This script asks an LLM to independently judge each run's predicted value
against ground truth (True/False + short reasoning), stores the verdicts in
`llm_judgments`, and reports:
  - how often the automated scorer agrees with the LLM judge
  - for each of a sweep of candidate CORRECT_THRESHOLD values, what
    precision/recall/F1 the automated scorer would have against the LLM
    judge's verdicts as ground truth

This does NOT change `scoring.CORRECT_THRESHOLD` automatically — it's meant
to build up empirical evidence for a human to use when deciding on that
value later (see backend/README.md).

Usage (from DEP root, .venv active):
    python -m backend.scripts.llm_judge --field sector_name --n 40 --judge-model openai/gpt-4o
    python -m backend.scripts.llm_judge --field authors --n 40 --judge-model openai/gpt-4o --concurrency 6
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db, gateway, parsing  # noqa: E402
from backend.app.fields import FIELDS  # noqa: E402

_JUDGE_SYSTEM = (
    "You are a strict but fair data-quality auditor for a research metadata extraction "
    "system. You will be shown a field being extracted from an academic paper, a value an "
    "LLM predicted, and the ground-truth value from a human-curated dataset. Decide whether "
    "the prediction is CORRECT — i.e. conveys the same real-world information as the ground "
    "truth, allowing for harmless differences in spelling, abbreviation, ordering, or "
    "formatting, but NOT allowing genuinely different entities/values. "
    'Respond with a JSON object: {"correct": true|false, "reasoning": "<one sentence>"}.'
)


def _judge_prompt(field_name: str, predicted, truth) -> str:
    spec = FIELDS[field_name]
    return (
        f"Field: {spec.label} ({spec.description})\n"
        f"Predicted value: {json.dumps(predicted, ensure_ascii=False)}\n"
        f"Ground truth value: {json.dumps(truth, ensure_ascii=False)}\n\n"
        "Is the predicted value correct?"
    )


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    parser.add_argument("--n", type=int, default=40, help="Max number of un-judged runs to judge.")
    parser.add_argument("--judge-model", default="openai/gpt-4o")
    parser.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY)
    args = parser.parse_args()

    with db.get_conn() as conn:
        runs = db.get_runs_without_judgment(conn, args.field, args.judge_model, limit=args.n)
        if not runs:
            print(f"No un-judged runs left for field={args.field!r} judge_model={args.judge_model!r}.")
        else:
            gt_cache: dict[int, object] = {}
            jobs = []
            for r in runs:
                if r["record_id"] not in gt_cache:
                    row = conn.execute(
                        "SELECT value_json FROM ground_truth WHERE record_id = ? AND field_name = ?",
                        (r["record_id"], args.field),
                    ).fetchone()
                    gt_cache[r["record_id"]] = json.loads(row["value_json"]) if row else None
                truth = gt_cache[r["record_id"]]
                predicted = json.loads(r["parsed_value_json"])
                jobs.append(
                    {
                        "model_id": args.judge_model,
                        "system_prompt": _JUDGE_SYSTEM,
                        "user_prompt": _judge_prompt(args.field, predicted, truth),
                        "temperature": 0.0,
                        "max_tokens": 200,
                    }
                )

            print(f"Judging {len(jobs)} runs with {args.judge_model}...")
            results = gateway.call_model_batch(jobs, max_workers=args.concurrency)

            n_ok, n_err = 0, 0
            for r, resp in zip(runs, results):
                if isinstance(resp, gateway.GatewayError):
                    n_err += 1
                    continue
                try:
                    obj = parsing.parse_json_object(resp.content)
                    verdict = bool(obj["correct"])
                    reasoning = obj.get("reasoning")
                except Exception as exc:  # noqa: BLE001 - just skip malformed judge output
                    n_err += 1
                    print(f"  run {r['id']}: could not parse judge response ({exc})")
                    continue
                db.add_llm_judgment(conn, r["id"], args.judge_model, verdict, reasoning)
                n_ok += 1
            print(f"Judged: {n_ok}, failed/unparsable: {n_err}")

        # --- Report: agreement + threshold sweep, over ALL judged runs for this field ---
        judged = conn.execute(
            "SELECT r.score, r.is_correct, j.verdict FROM runs r "
            "JOIN llm_judgments j ON j.run_id = r.id AND j.judge_model = ? "
            "WHERE r.field_name = ?",
            (args.judge_model, args.field),
        ).fetchall()

    if not judged:
        print("No judged runs available for a report yet.")
        return

    n = len(judged)
    agree = sum(1 for row in judged if bool(row["is_correct"]) == bool(row["verdict"]))
    print(f"\n=== Report for field={args.field!r}, judge={args.judge_model!r}, n={n} ===")
    print(f"Agreement between scorer.is_correct and LLM judge: {agree}/{n} ({agree / n:.1%})")

    print("\nThreshold sweep (scorer correct if score >= T, vs LLM judge as ground truth):")
    print(f"{'T':>5}  {'precision':>9}  {'recall':>7}  {'f1':>6}")
    for t in [round(0.5 + 0.05 * i, 2) for i in range(11)]:
        tp = sum(1 for row in judged if row["score"] >= t and row["verdict"])
        fp = sum(1 for row in judged if row["score"] >= t and not row["verdict"])
        fn = sum(1 for row in judged if row["score"] < t and row["verdict"])
        precision, recall, f1 = _prf(tp, fp, fn)
        print(f"{t:>5.2f}  {precision:>9.3f}  {recall:>7.3f}  {f1:>6.3f}")


if __name__ == "__main__":
    main()
