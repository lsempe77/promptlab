"""One-off maintenance script: re-scores every existing row in `runs` against
its ground truth using the CURRENT scoring thresholds (scoring.CORRECT_THRESHOLD /
scoring.FUZZY_MATCH_THRESHOLD).

Why this is needed: `runs.score` / `runs.is_correct` are computed once, at the
time a run is logged, using whatever thresholds were active then. If the
thresholds change later (e.g. tightened from 0.8 -> 0.95), historical rows
would keep showing stale scores/correctness unless recomputed. This script
recomputes both columns in place from `parsed_value_json` + `ground_truth`,
so the dashboard's model-comparison accuracy always reflects the current
thresholds.

Usage (from DEP root, .venv active):
    python -m backend.scripts.rescore_runs [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db, scoring  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report what would change, without writing.")
    args = parser.parse_args()

    with db.get_conn() as conn:
        runs = conn.execute(
            "SELECT id, project_id, field_name, record_id, parsed_value_json, score, is_correct FROM runs"
        ).fetchall()

        gt_cache: dict[tuple[int, int, str], object] = {}
        n_changed = 0
        n_skipped_no_value = 0

        for run in runs:
            if run["parsed_value_json"] is None:
                n_skipped_no_value += 1
                continue

            key = (run["project_id"], run["record_id"], run["field_name"])
            if key not in gt_cache:
                row = conn.execute(
                    "SELECT value_json FROM ground_truth WHERE project_id = ? AND record_id = ? AND field_name = ?",
                    key,
                ).fetchone()
                gt_cache[key] = json.loads(row["value_json"]) if row else None
            truth = gt_cache[key]
            if truth is None:
                n_skipped_no_value += 1
                continue

            predicted = json.loads(run["parsed_value_json"])
            result = scoring.score_field(run["field_name"], predicted, truth)
            new_score = result.score
            new_is_correct = int(result.is_correct)

            if abs((run["score"] or 0.0) - new_score) > 1e-9 or run["is_correct"] != new_is_correct:
                n_changed += 1
                if not args.dry_run:
                    conn.execute(
                        "UPDATE runs SET score = ?, is_correct = ? WHERE id = ?",
                        (new_score, new_is_correct, run["id"]),
                    )

        print(f"runs examined: {len(runs)}")
        print(f"runs changed:  {n_changed}{' (dry-run, not written)' if args.dry_run else ''}")
        print(f"runs skipped (no parsed value / no ground truth): {n_skipped_no_value}")


if __name__ == "__main__":
    main()
