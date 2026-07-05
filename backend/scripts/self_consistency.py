"""Self-consistency validation study.

Samples the SAME (record, model) prompt N times at temperature > 0 and measures
how often the model lands on the same answer. The agreement rate (fraction of
samples that fall in the modal answer cluster) is a model-intrinsic confidence
signal: low agreement = the model is unstable/guessing on that record, high
agreement = it is consistently committed. This costs N x the calls, so it is an
opt-in validation study over a SMALL sample, NOT part of the main rollout.

Results are stored in the `self_consistency` table (one row per field+model+
record) and surfaced via GET /api/fields/{field}/self-consistency.

Usage (from DEP root, .venv active):
    python -m backend.scripts.self_consistency --field sector_name --n 20 --samples 5
    python -m backend.scripts.self_consistency --field authors --n 15 --samples 5 \
        --models openai/gpt-4o-mini --temperature 0.7
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import config, db, gateway, prompts, scoring  # noqa: E402
from backend.app.corpus import read_md  # noqa: E402
from backend.app.parsing import ParseError, parse_field_response  # noqa: E402
from backend.app.prompt_store import get_or_create_baseline  # noqa: E402

SEED = 42


def select_models(args: argparse.Namespace) -> list[str]:
    if args.models:
        return [m.strip() for m in args.models.split(",") if m.strip()]
    roster = config.load_models()
    if args.tiers:
        wanted = {t.strip() for t in args.tiers.split(",")}
        roster = [m for m in roster if m["tier"] in wanted]
    return [m["id"] for m in roster]


def consensus(field_name: str, values: list[Any]) -> tuple[Any, float]:
    """Cluster the repeat samples by mutual agreement (scorer >=
    CORRECT_THRESHOLD, so "WHO" and "World Health Organization" count as the
    same answer) and return (modal_value, agreement) where agreement is the
    size of the largest cluster over the number of samples."""
    if not values:
        return None, 0.0
    clusters: list[list[Any]] = []
    for v in values:
        placed = False
        for c in clusters:
            try:
                same = scoring.score_field(field_name, v, c[0]).score >= scoring.CORRECT_THRESHOLD
            except Exception:  # noqa: BLE001 - a weird value must not kill the study
                same = False
            if same:
                c.append(v)
                placed = True
                break
        if not placed:
            clusters.append([v])
    largest = max(clusters, key=len)
    return largest[0], len(largest) / len(values)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, choices=list(prompts.BASELINE_INSTRUCTIONS.keys()))
    ap.add_argument("--n", type=int, default=20, help="number of records to sample")
    ap.add_argument("--samples", type=int, default=5, help="repeat samples per (record, model)")
    ap.add_argument("--temperature", type=float, default=0.7, help="sampling temperature (must be > 0)")
    ap.add_argument("--models", type=str, default=None, help="comma-separated OpenRouter model ids")
    ap.add_argument("--tiers", type=str, default=None, help="comma-separated model tiers from models.yaml")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY)
    ap.add_argument("--force", action="store_true",
                     help="recompute (field, model, record) triples already stored")
    args = ap.parse_args()

    if args.temperature <= 0:
        print("[warn] --temperature must be > 0 for self-consistency to mean anything; using 0.7.")
        args.temperature = 0.7
    if args.n > config.MAX_PRODUCTION_RECORDS:
        print(f"[warn] --n {args.n} exceeds the production cap of {config.MAX_PRODUCTION_RECORDS}; clamping.")
        args.n = config.MAX_PRODUCTION_RECORDS

    with db.get_conn() as conn:
        pv = get_or_create_baseline(conn, args.field)
        records = db.get_records_with_field(conn, args.field)
    if pv is None:
        print(f"No baseline prompt for field={args.field}.")
        return
    if not records:
        print(f"No ground-truth records for field={args.field}. Run build_ground_truth first.")
        return

    random.Random(args.seed).shuffle(records)
    records = records[: args.n]
    models = select_models(args)
    print(f"Self-consistency: field={args.field} records={len(records)} models={len(models)} "
          f"samples={args.samples} temp={args.temperature}")

    with db.get_conn() as conn:
        done: set[tuple[str, int]] = set()
        if not args.force:
            rows = conn.execute(
                "SELECT model_id, record_id FROM self_consistency WHERE field_name = ?",
                (args.field,),
            ).fetchall()
            done = {(r["model_id"], r["record_id"]) for r in rows}

    # One job per (record, model, sample); all fired concurrently.
    jobs: list[dict] = []
    meta: list[tuple[int, str]] = []
    for rec in records:
        try:
            md_text = read_md(rec["md_path"])
        except OSError as exc:
            print(f"  [skip] record {rec['id']}: cannot read md ({exc})")
            continue
        sys_p, usr_p = prompts.build_prompt(args.field, rec["title"] or "", md_text, instruction=pv["template"])
        for model_id in models:
            if (model_id, rec["id"]) in done:
                continue
            for _ in range(args.samples):
                jobs.append({"model_id": model_id, "system_prompt": sys_p, "user_prompt": usr_p,
                             "temperature": args.temperature})
                meta.append((rec["id"], model_id))

    if not jobs:
        print("Nothing new to sample (use --force to recompute).")
        return
    print(f"Firing {len(jobs)} calls ({args.samples} per (record, model))...")

    responses = gateway.call_model_batch(jobs, max_workers=args.concurrency)

    grouped: dict[tuple[int, str], list[Any]] = defaultdict(list)
    for (rid, mid), resp in zip(meta, responses):
        if isinstance(resp, gateway.GatewayError):
            continue
        try:
            value, _meta = parse_field_response(args.field, resp.content)
        except Exception:  # noqa: BLE001 - unparseable sample is just dropped
            continue
        grouped[(rid, mid)].append(value)

    per_model: dict[str, list[float]] = defaultdict(list)
    with db.get_conn() as conn:
        for (rid, mid), values in grouped.items():
            if not values:
                continue
            modal, agreement = consensus(args.field, values)
            db.add_self_consistency(conn, args.field, mid, rid, len(values), agreement, modal)
            per_model[mid].append(agreement)

    print("\nMean self-consistency (agreement) by model:")
    for mid in sorted(per_model, key=lambda m: -(sum(per_model[m]) / len(per_model[m]))):
        vals = per_model[mid]
        print(f"  {mid:45s} {sum(vals) / len(vals):.2f}  (n={len(vals)})")


if __name__ == "__main__":
    main()
