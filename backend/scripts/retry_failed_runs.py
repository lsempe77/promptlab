"""Re-run just the (record, model) pairs that failed for a given field, instead
of re-running the whole batch -- useful after a transient/upstream outage
(e.g. free-tier rate-limiting) clears up.

A pair is considered "failed" if every logged run for that (record, model,
field) combination has a non-null `error` -- i.e. there's no successful run
for it yet. Retried results are written as new `runs` rows (a fresh
batch_id), same as a normal extraction run, using the incremental
on_complete-style persistence so a crash/kill only loses in-flight calls.

Usage (from DEP root, .venv active):
    python -m backend.scripts.retry_failed_runs --field sector_name
    python -m backend.scripts.retry_failed_runs --field sector_name --models meta-llama/llama-3.3-70b-instruct:free,qwen/qwen3-coder:free
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db, gateway, prompts, scoring  # noqa: E402
from backend.app.corpus import read_md  # noqa: E402
from backend.app.parsing import ParseError, parse_field_response  # noqa: E402
from backend.app.prompt_store import get_or_create_baseline  # noqa: E402


def find_failed_pairs(conn, project_id: int, field_name: str, models: list[str] | None) -> set[tuple[int, str]]:
    rows = conn.execute(
        "SELECT record_id, model_id FROM runs WHERE project_id = ? AND field_name = ? "
        "GROUP BY record_id, model_id "
        "HAVING SUM(CASE WHEN error IS NULL THEN 1 ELSE 0 END) = 0",
        (project_id, field_name),
    ).fetchall()
    pairs = {(r["record_id"], r["model_id"]) for r in rows}
    if models:
        wanted = set(models)
        pairs = {(rid, mid) for rid, mid in pairs if mid in wanted}
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="dep-extraction", help="project slug (see backend/app/projects.py)")
    ap.add_argument("--field", required=True, choices=list(prompts.BASELINE_INSTRUCTIONS.keys()))
    ap.add_argument("--models", type=str, default=None, help="comma-separated model ids to limit the retry to")
    ap.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY)
    args = ap.parse_args()

    models_filter = [m.strip() for m in args.models.split(",")] if args.models else None
    batch_id = uuid.uuid4().hex[:8]

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        pairs = find_failed_pairs(conn, project_id, args.field, models_filter)
        if not pairs:
            print(f"No fully-failed (record, model) pairs found for field={args.field}.")
            return

        # Resolve each model's OWN prompt version. Per-model prompts diverge from
        # the shared baseline once a model's optimizer accepts a rewrite, and
        # run_extraction runs each model on its per-model version. Retries must use
        # the same version, else the retried rows land under a different
        # prompt_version_id and stay invisible to per-model accounting (and get
        # re-run forever).
        models_in_pairs = {mid for _, mid in pairs}
        model_pv = {m: get_or_create_baseline(conn, project_id, args.field, m) for m in models_in_pairs}

        record_ids = {rid for rid, _ in pairs}
        records_by_id = {
            rec["id"]: rec for rec in db.get_records_with_field(conn, project_id, args.field) if rec["id"] in record_ids
        }

    print(f"Retrying {len(pairs)} failed (record, model) pairs for field={args.field}")
    print(f"Batch id: {batch_id}\n")

    jobs: list[dict] = []
    job_meta: list[tuple[dict, str]] = []
    # Keyed by (record_id, prompt_version_id): different models may resolve to
    # different templates, so the cache must not collapse them by record alone.
    prompt_cache: dict[tuple[int, int], tuple[str, str]] = {}
    skipped = 0
    for rec_id, model_id in sorted(pairs):
        rec = records_by_id.get(rec_id)
        if rec is None:
            continue
        pv = model_pv[model_id]
        cache_key = (rec_id, pv["id"])
        if cache_key not in prompt_cache:
            try:
                md_text = read_md(rec["md_path"])
            except OSError as exc:
                print(f"  [skip] record {rec_id}: cannot read md ({exc})")
                skipped += 1
                continue
            prompt_cache[cache_key] = prompts.build_prompt(args.field, rec["title"] or "", md_text, instruction=pv["template"])
        system_prompt, user_prompt = prompt_cache[cache_key]
        jobs.append({"model_id": model_id, "system_prompt": system_prompt, "user_prompt": user_prompt})
        job_meta.append((rec, model_id))

    if not jobs:
        print("Nothing left to retry (all pairs skipped).")
        return

    n_ok = 0
    n_err = 0
    try:
        with db.get_conn() as conn:

            def _on_complete(i: int, resp: gateway.ModelResponse | gateway.GatewayError) -> None:
                nonlocal n_ok, n_err
                rec, model_id = job_meta[i]
                run_kwargs = dict(
                    project_id=project_id,
                    prompt_version_id=model_pv[model_id]["id"],
                    model_id=model_id,
                    record_id=rec["id"],
                    field_name=args.field,
                    batch_id=batch_id,
                )
                if isinstance(resp, gateway.GatewayError):
                    print(f"  [error] {model_id} on record {rec['id']}: {resp}")
                    db.add_run(conn, **run_kwargs, raw_response=None, parsed_value=None, score=None,
                               is_correct=0, latency_ms=None, prompt_tokens=None, completion_tokens=None,
                               cost_usd=None, error=str(resp))
                    n_err += 1
                else:
                    try:
                        value, meta = parse_field_response(args.field, resp.content)
                    except ParseError as exc:
                        print(f"  [parse-error] {model_id} on record {rec['id']}: {exc}")
                        db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                                   is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                   completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd, error=str(exc))
                        n_err += 1
                    else:
                        result = scoring.score_field(args.field, value, rec["ground_truth"])
                        db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value,
                                   score=result.score, is_correct=int(result.is_correct), latency_ms=resp.latency_ms,
                                   prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                                   cost_usd=resp.cost_usd, error=None)
                        n_ok += 1
                conn.commit()

            gateway.call_model_batch(jobs, max_workers=args.concurrency, on_complete=_on_complete)
    finally:
        print(f"\n=== Retry results === succeeded: {n_ok}  still failing: {n_err}  skipped (no md): {skipped}")


if __name__ == "__main__":
    main()
