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

from backend.app import db, gateway, judging, parsing  # noqa: E402
from backend.app import db_pg  # noqa: E402
from backend.app.fields import FIELDS  # noqa: E402

_USE_PG = db_pg.pg_enabled()

# Judge logic lives in app/judging.py (shared with the optimizer's acceptance
# test in app/optimizer.py) so the posterior sweep and the in-loop judge can't
# drift apart.
_JUDGE_SYSTEM = judging.JUDGE_SYSTEM
_judge_prompt = judging.judge_prompt
_judge_for = judging.judge_for


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="dep-extraction", help="project slug (see backend/app/projects.py)")
    parser.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    parser.add_argument("--n", type=int, default=40, help="Max number of un-judged runs to judge.")
    parser.add_argument("--judge-model", default="openai/gpt-4o")
    parser.add_argument("--cross-family", action="store_true",
                        help="judge each run with a different model family (Anthropic->GPT, others->Claude) "
                             "to avoid self-preference bias; ignores --judge-model")
    parser.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY)
    args = parser.parse_args()

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)

        # -- Read runs + already-judged: always from SQLite (coordinator's ground truth) ---
        candidates = conn.execute(
            "SELECT id, model_id, record_id, parsed_value_json FROM runs "
            "WHERE project_id = ? AND field_name = ? AND parsed_value_json IS NOT NULL "
            "ORDER BY id",
            (project_id, args.field),
        ).fetchall()
        already = {
            (row["run_id"], row["judge_model"])
            for row in conn.execute(
                "SELECT j.run_id, j.judge_model FROM llm_judgments j "
                "JOIN runs r ON r.id = j.run_id WHERE r.project_id = ? AND r.field_name = ?",
                (project_id, args.field),
                )
            }

        if args.cross_family:
            runs = [r for r in candidates if (r["id"], _judge_for(r["model_id"])) not in already][: args.n]
        else:
            runs = [r for r in candidates if (r["id"], args.judge_model) not in already][: args.n]

        judge_of = {
            r["id"]: (_judge_for(r["model_id"]) if args.cross_family else args.judge_model) for r in runs
        }

        if not runs:
            print(f"No un-judged runs left for field={args.field!r}.")
        else:
            gt_cache: dict[int, object] = {}
            jobs = []
            for r in runs:
                if r["record_id"] not in gt_cache:
                    row = conn.execute(
                        "SELECT value_json FROM ground_truth WHERE project_id = ? AND record_id = ? AND field_name = ?",
                        (project_id, r["record_id"], args.field),
                    ).fetchone()
                    gt_cache[r["record_id"]] = json.loads(row["value_json"]) if row else None
                truth = gt_cache[r["record_id"]]
                predicted = json.loads(r["parsed_value_json"])
                jobs.append(
                    {
                        "model_id": judge_of[r["id"]],
                        "system_prompt": _JUDGE_SYSTEM,
                        "user_prompt": _judge_prompt(args.field, predicted, truth),
                        "temperature": 0.0,
                        "max_tokens": 400,
                        "json_mode": True,
                    }
                )

            judge_desc = "cross-family judges" if args.cross_family else args.judge_model
            print(f"Judging {len(jobs)} runs with {judge_desc}...")
            results = gateway.call_model_batch(jobs, max_workers=args.concurrency)

            n_ok, n_err = 0, 0
            _pg_write = db_pg.get_pg_conn().__enter__() if _USE_PG else None
            for r, resp in zip(runs, results):
                if isinstance(resp, gateway.GatewayError):
                    n_err += 1
                    continue
                try:
                    obj = parsing.parse_json_object(resp.content)
                    verdict = bool(obj["correct"])
                    reasoning = obj.get("reasoning")
                except Exception as exc:  # noqa: BLE001
                    n_err += 1
                    print(f"  run {r['id']}: could not parse judge response ({exc})")
                    continue
                if _USE_PG and _pg_write:
                    db_pg.add_llm_judgment_pg(_pg_write, r["id"], judge_of[r["id"]], verdict, reasoning)
                # Always write to SQLite — it is the coordinator's ground truth for unjudged counts
                db.add_llm_judgment(conn, r["id"], judge_of[r["id"]], verdict, reasoning)
                n_ok += 1
            if _USE_PG and _pg_write:
                _pg_write.commit()
            print(f"Judged: {n_ok}, failed/unparsable: {n_err}")

        # -- Report: agreement + threshold sweep over ALL judged runs -------------------
        if args.cross_family:
            judged = conn.execute(
                "SELECT r.score, r.is_correct, j.verdict FROM runs r "
                "JOIN llm_judgments j ON j.run_id = r.id "
                "WHERE r.project_id = ? AND r.field_name = ?",
                (project_id, args.field),
            ).fetchall()
        else:
            judged = conn.execute(
                "SELECT r.score, r.is_correct, j.verdict FROM runs r "
                "JOIN llm_judgments j ON j.run_id = r.id AND j.judge_model = ? "
                "WHERE r.project_id = ? AND r.field_name = ?",
                (args.judge_model, project_id, args.field),
            ).fetchall()

    if not judged:
        print("No judged runs available for a report yet.")
        return

    n = len(judged)
    agree = sum(1 for row in judged if bool(row["is_correct"]) == bool(row["verdict"]))
    judge_label = "cross-family" if args.cross_family else args.judge_model
    print(f"\n=== Report for field={args.field!r}, judge={judge_label!r}, n={n} ===")
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
