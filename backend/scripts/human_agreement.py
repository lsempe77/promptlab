"""Score a second curator against the existing ground truth: the HUMAN CEILING.

This is the missing reference standard. Every model number we report is agreement
with one curator's labels; this reports what a *second* curator achieves against
that same key, computed with the identical metric code (`analytics.gate_metrics`)
the production gate uses. That makes the comparison honest: "kappa 0.748 (model)
vs 0.71 (human)" is a like-for-like statement, whereas comparing a model's kappa
to a literature constant is not.

How to read the output
----------------------
The human column is not a target to beat, it is the ceiling of what the task
supports. If a model matches it, the remaining disagreement is task ambiguity
rather than model error, and further prompt optimization is spending money to
chase label noise. If a model is well below it, there is real headroom.

A model *exceeding* the human figure is not automatically a win either: it can
mean curator 1 and the model share a bias that curator 2 doesn't.

Usage
-----
    python -m backend.scripts.human_agreement --sheet curator2_sheet.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

from backend.app import analytics, db, scoring
from backend.app.fields import FIELDS

LIST_SEPARATOR = "|"


def _wilson95(p: float, n: int) -> tuple[float, float] | None:
    """95% Wilson interval — the honest width on a 30-50 record sample."""
    if n <= 0:
        return None
    z = 1.959964
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def _parse(value: str, value_type: str):
    value = (value or "").strip()
    if not value:
        return [] if value_type != "single_categorical" else None
    if value_type == "single_categorical":
        return value
    return [p.strip() for p in value.split(LIST_SEPARATOR) if p.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--sheet", required=True, help="the CSV the second curator filled in")
    args = ap.parse_args()

    sheet = Path(args.sheet)
    with sheet.open(encoding="utf-8-sig") as fh:
        filled = list(csv.DictReader(fh))
    if not filled:
        raise SystemExit(f"{sheet} has no rows.")

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        gt_rows = conn.execute(
            "SELECT record_id, field_name, value_json FROM ground_truth WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        gt = {(r["record_id"], r["field_name"]): json.loads(r["value_json"]) for r in gt_rows}

        best_model: dict[str, tuple[str, float]] = {}
        for field in FIELDS:
            rows = conn.execute(
                "SELECT r.model_id, r.parsed_value_json, g.value_json FROM runs r "
                "JOIN ground_truth g ON g.project_id = r.project_id "
                " AND g.record_id = r.record_id AND g.field_name = r.field_name "
                "WHERE r.project_id = ? AND r.field_name = ? AND r.parsed_value_json IS NOT NULL",
                (project_id, field),
            ).fetchall()
            by_model: dict[str, list[dict]] = {}
            for r in rows:
                by_model.setdefault(r["model_id"], []).append(
                    {"predicted": json.loads(r["parsed_value_json"]),
                     "truth": json.loads(r["value_json"])})
            for model_id, mrows in by_model.items():
                metric = analytics.gate_metrics(field, mrows)["metric"]
                if field not in best_model or metric > best_model[field][1]:
                    best_model[field] = (model_id, metric)

    print(f"Human double-extraction agreement — {len(filled)} records\n")
    header = f"{'field':22s} {'metric':8s} {'human':>7s} {'95% CI':>16s} {'best model':>11s} {'verdict'}"
    print(header)
    print("-" * len(header))

    skipped = []
    for field, spec in FIELDS.items():
        pairs = []
        for row in filled:
            rid = int(row["record_id"])
            truth = gt.get((rid, field))
            if truth is None:
                continue
            second = _parse(row.get(field, ""), spec.value_type)
            if second is None or second == []:
                continue  # curator left it blank: no second reading to compare
            pairs.append({"predicted": second, "truth": truth})

        if not pairs:
            skipped.append(field)
            continue

        gm = analytics.gate_metrics(field, pairs)
        ci = _wilson95(gm["metric"], gm["n"])
        ci_s = f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "n/a"
        model_id, model_metric = best_model.get(field, ("(none)", float("nan")))

        if math.isnan(model_metric):
            verdict = "no model runs"
        elif ci and model_metric >= ci[0]:
            verdict = "model within human range"
        else:
            verdict = f"model below human by {gm['metric'] - model_metric:.3f}"

        print(f"{field:22s} {gm['metric_name']:8s} {gm['metric']:>7.3f} {ci_s:>16s} "
              f"{model_metric:>11.3f}  {verdict}")

    if skipped:
        print(f"\nNo second reading for: {', '.join(skipped)} (all cells blank).")

    print("\nThe human column is a CEILING, not a target. Where a model already sits")
    print("inside the human confidence interval, the remaining gap is task ambiguity,")
    print("and further prompt optimization on that field is chasing label noise.")
    print("\nCurrent gate bars, for comparison:")
    for field in FIELDS:
        metric_name, threshold = scoring.gate_for(field)
        print(f"  {field:22s} {metric_name or 'f1':8s} >= {threshold:.2f}")


if __name__ == "__main__":
    main()
