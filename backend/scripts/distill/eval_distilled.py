"""Step 4 — evaluate one or more models on the human ground truth using the
SAME production gate (scoring.score_field + analytics.gate_metrics), and print
gate pass/fail alongside cost and CO2e per record.

This is where teacher vs. student is decided. Nothing here is persisted to the
runs table — it's a read-only comparison against the untouched 100 GT records.

Endpoints: by default every listed model is called through OpenRouter. To point
at a model served elsewhere (local vLLM, Fireworks/Together deployment), pass
--base-url / --api-key (or env DISTILL_BASE_URL / DISTILL_API_KEY); the override
applies to ALL models in the run, so if the teacher is on OpenRouter and the
student is on a custom endpoint, run this twice (once per endpoint) and compare
the two printouts.

Usage:
    # both reachable via OpenRouter:
    python -m backend.scripts.distill.eval_distilled --field sub_sector \
        --models "~anthropic/claude-sonnet-latest,my-distilled-sub-sector"

    # student on a local vLLM server:
    DISTILL_BASE_URL=http://localhost:8000/v1 DISTILL_API_KEY=x \
    python -m backend.scripts.distill.eval_distilled --field sub_sector \
        --models my-distilled-sub-sector
"""
from __future__ import annotations

import argparse
import os

from backend.app import analytics, carbon, config, db, gateway, prompts, scoring
from backend.app.fields import FIELDS
from backend.app.parsing import ParseError, parse_field_response
from backend.app.prompt_store import get_or_create_baseline

from ._common import setup_utf8


def _extract_pass(model_id, template, field, records, md_cache, contexts, concurrency):
    """One extraction pass. Returns (rows, cost_usd, co2e_g, errors) where rows is
    [{predicted, truth}] for analytics.gate_metrics."""
    jobs, meta = [], []
    for rec in records:
        system_prompt, user_prompt = prompts.build_prompt(
            field, rec["title"] or "", md_cache[rec["id"]], instruction=template,
            context_value=contexts.get(rec["id"]),
        )
        jobs.append({"model_id": model_id, "system_prompt": system_prompt,
                     "user_prompt": user_prompt, "logprobs": False})
        meta.append(rec)

    rows, cost, co2e, errors = [], 0.0, 0.0, 0
    results = gateway.call_model_batch(jobs, max_workers=concurrency)
    values: dict[int, object] = {}
    for rec, resp in zip(meta, results):
        if isinstance(resp, gateway.GatewayError):
            errors += 1
            continue
        try:
            value, _m = parse_field_response(field, resp.content)
        except ParseError:
            value = None
        values[rec["id"]] = value
        rows.append({"predicted": value, "truth": rec["ground_truth"]})
        if resp.cost_usd:
            cost += resp.cost_usd
        g = carbon.estimate_co2e_grams(model_id, resp.completion_tokens, resp.latency_ms)
        if g:
            co2e += g
    return rows, cost, co2e, errors, values


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--models", required=True, help="comma-separated model ids (teacher,student,...)")
    ap.add_argument("--n", type=int, default=100, help="GT records to evaluate on")
    ap.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY)
    ap.add_argument("--single-step", action="store_true", help="for sub_sector: skip sector context")
    ap.add_argument("--base-url", default=os.environ.get("DISTILL_BASE_URL"))
    ap.add_argument("--api-key", default=os.environ.get("DISTILL_API_KEY"))
    args = ap.parse_args()

    # Retarget the shared gateway at a custom OpenAI-compatible endpoint if asked.
    if args.base_url:
        config.OPENROUTER_BASE_URL = args.base_url.rstrip("/")
    if args.api_key:
        config.OPENROUTER_API_KEY = args.api_key
    if not config.OPENROUTER_API_KEY:
        raise SystemExit("No API key (set OPENROUTER_API_KEY or --api-key/DISTILL_API_KEY).")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    field = args.field
    two_step = field == "sub_sector" and not args.single_step

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        records = db.get_records_with_field(conn, project_id, field, limit=args.n)
        templates = {m: get_or_create_baseline(conn, project_id, field, model_id=m)["template"]
                     for m in models}
        sector_templates = (
            {m: get_or_create_baseline(conn, project_id, "sector_name", model_id=m)["template"]
             for m in models} if two_step else {})

    if not records:
        raise SystemExit(f"No ground-truth records for field={field}.")

    md_cache: dict[int, str] = {}
    keep = []
    for rec in records:
        try:
            from backend.app.corpus import read_md
            md_cache[rec["id"]] = read_md(rec["md_path"])[: prompts.MAX_CHARS]
            keep.append(rec)
        except OSError:
            pass
    records = keep

    gate = scoring.GATE_THRESHOLD
    floor = scoring.RECALL_FLOOR
    print(f"Field={field}  records={len(records)}  gate>={gate}"
          f"{f' recall>={floor}' if FIELDS[field].value_type != 'single_categorical' else ''}\n")
    header = f"{'model':42s} {'metric':>8s} {'value':>7s} {'2nd':>7s} {'pass':>5s} " \
             f"{'$/rec':>9s} {'gCO2e/rec':>10s} {'err':>4s}"
    print(header)
    print("-" * len(header))

    for m in models:
        # Two-step sub_sector: this model first extracts sector_name for context.
        contexts: dict[int, object] = {}
        if two_step:
            _r, _c, _e, _err, sec_vals = _extract_pass(
                m, sector_templates[m], "sector_name", records, md_cache, {}, args.concurrency)
            contexts = {rid: v for rid, v in sec_vals.items() if v}

        rows, cost, co2e, errors, _vals = _extract_pass(
            m, templates[m], field, records, md_cache, contexts, args.concurrency)
        if not rows:
            print(f"{m:42s}  (no successful extractions; {errors} errors)")
            continue

        gm = analytics.gate_metrics(field, rows)
        n = max(1, len(rows))
        if gm["metric_name"] == "accuracy":
            value = gm["metric"]
            second = gm.get("kappa")  # Cohen's kappa
            passed = value >= gate
            second_s = f"{second:.3f}" if second is not None else "  n/a"
        else:
            value = gm["metric"]  # f1
            second = gm.get("recall")
            passed = (value >= gate) and (second is not None and second >= floor)
            second_s = f"{second:.3f}" if second is not None else "  n/a"
        print(f"{m:42s} {gm['metric_name']:>8s} {value:7.3f} {second_s:>7s} "
              f"{('YES' if passed else 'no'):>5s} {cost/n:9.5f} {co2e/n:10.3f} {errors:4d}")

    print("\n2nd column = Cohen's kappa (categorical) or recall (list fields).")
    print("A student that PASSES with materially lower $/rec and gCO2e/rec is a new "
          "cost-quality frontier point — take it to human review for models.yaml.")


if __name__ == "__main__":
    main()
