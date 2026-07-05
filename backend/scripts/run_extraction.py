"""Multi-model batch extraction + scoring harness.

For a given field, samples N ground-truthed records, runs every configured
model (models.yaml) against the current baseline (or a given) prompt version,
scores each response against ground truth, stores every run in SQLite, and
prints a per-model comparison table.

Production rollout policy: validate at --n 50 first, then 100, then 300.
--n is hard-capped at config.MAX_PRODUCTION_RECORDS (300) to avoid accidentally
kicking off a run against the full ~7,600-record ground-truth pool.

Scaling up is incremental by default: a (record, model) pair that already has
a logged run for this field+prompt-version is skipped, so re-running with a
bigger --n (e.g. 50 -> 100 -> 300) only pays for the NEW records, not the ones
already done at the smaller stage. Pass --force to re-run everything anyway
(e.g. after a prompt/corpus change you want fully re-tested).

Usage (from DEP root, .venv active):
    python -m backend.scripts.run_extraction --field sector_name --n 50
    python -m backend.scripts.run_extraction --field authors --n 50 --models openai/gpt-4o-mini,anthropic/claude-3-5-haiku
    python -m backend.scripts.run_extraction --field sector_name --n 50 --tiers free,cheap --concurrency 8
"""
from __future__ import annotations

import argparse
import random
import sys
import uuid
from pathlib import Path

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


def existing_pairs(conn, project_id: int, field_name: str, prompt_version_id: int) -> set[tuple[int, str]]:
    """(record_id, model_id) pairs that already have a logged run for this
    field + prompt version -- used to make scaling --n up incremental."""
    rows = conn.execute(
        "SELECT DISTINCT record_id, model_id FROM runs WHERE project_id = ? AND field_name = ? AND prompt_version_id = ?",
        (project_id, field_name, prompt_version_id),
    ).fetchall()
    return {(r["record_id"], r["model_id"]) for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="dep-extraction", help="project slug (see backend/app/projects.py)")
    ap.add_argument("--field", required=True, choices=list(prompts.BASELINE_INSTRUCTIONS.keys()))
    ap.add_argument("--n", type=int, default=50, help="number of records to sample (capped at %d)" % config.MAX_PRODUCTION_RECORDS)
    ap.add_argument("--models", type=str, default=None, help="comma-separated OpenRouter model ids")
    ap.add_argument("--tiers", type=str, default=None, help="comma-separated model tiers from models.yaml")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY,
                     help="max concurrent API calls in flight at once")
    ap.add_argument("--force", action="store_true",
                     help="re-run (record, model) pairs even if already logged for this field+prompt version "
                          "(default: skip already-done pairs, so scaling --n up is incremental)")
    args = ap.parse_args()

    if args.n > config.MAX_PRODUCTION_RECORDS:
        print(f"[warn] --n {args.n} exceeds the production cap of {config.MAX_PRODUCTION_RECORDS}; "
              f"clamping to {config.MAX_PRODUCTION_RECORDS}. Roll out in stages: "
              f"{', '.join(str(s) for s in config.PRODUCTION_ROLLOUT_STAGES)}.")
        args.n = config.MAX_PRODUCTION_RECORDS

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        pv = get_or_create_baseline(conn, project_id, args.field)
        records = db.get_records_with_field(conn, project_id, args.field)
        done_pairs = set() if args.force else existing_pairs(conn, project_id, args.field, pv["id"]) if pv else set()

    if pv is None:
        print(f"Failed to create/load a baseline prompt version for field={args.field}.")
        return
    if not records:
        print(f"No ground-truth records found for field={args.field}. Run build_ground_truth first.")
        return

    random.Random(args.seed).shuffle(records)
    records = records[: args.n]
    models = select_models(args)
    batch_id = uuid.uuid4().hex[:8]

    print(f"Field: {args.field} | prompt v{pv['version']} | records: {len(records)} | models: {len(models)} | concurrency: {args.concurrency}")
    print(f"Batch id: {batch_id}\n")

    results: dict[str, list[float]] = {m: [] for m in models}
    errors: dict[str, int] = {m: 0 for m in models}

    # Build every (record, model) job up front so all calls for this batch run
    # concurrently, instead of one record/model at a time sequentially. Pairs
    # already logged for this field+prompt version are skipped (unless
    # --force) so scaling --n up doesn't re-pay for smaller-stage records.
    jobs: list[dict] = []
    job_meta: list[tuple[dict, str]] = []
    prompt_cache: dict[int, tuple[str, str]] = {}
    new_counts: dict[str, int] = {m: 0 for m in models}
    skipped = 0
    for rec in records:
        needed_models = [m for m in models if (rec["id"], m) not in done_pairs]
        if not needed_models:
            skipped += len(models)
            continue
        try:
            md_text = read_md(rec["md_path"])
        except OSError as exc:
            print(f"  [skip] record {rec['id']}: cannot read md ({exc})")
            skipped += len(needed_models)
            continue
        system_prompt, user_prompt = prompts.build_prompt(args.field, rec["title"] or "", md_text, instruction=pv["template"])
        prompt_cache[rec["id"]] = (system_prompt, user_prompt)
        for model_id in needed_models:
            jobs.append({"model_id": model_id, "system_prompt": system_prompt, "user_prompt": user_prompt})
            job_meta.append((rec, model_id))
            new_counts[model_id] += 1

    if skipped:
        print(f"Skipping {skipped} (record, model) pairs already logged for this field+prompt version "
              f"(use --force to re-run them).")
    if not jobs:
        print("Nothing new to run.")
        return

    with db.get_conn() as conn:
        job_ids = {m: db.start_job(conn, project_id, args.field, m, kind="extraction", total=new_counts[m]) for m in models}

    try:
        with db.get_conn() as conn:

            def _on_complete(i: int, resp: gateway.ModelResponse | gateway.GatewayError) -> None:
                rec, model_id = job_meta[i]
                run_kwargs = dict(
                    project_id=project_id,
                    prompt_version_id=pv["id"],
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
                    errors[model_id] += 1
                else:
                    try:
                        value, meta = parse_field_response(args.field, resp.content)
                    except ParseError as exc:
                        print(f"  [parse-error] {model_id} on record {rec['id']}: {exc}")
                        db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                                   is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                   completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd, error=str(exc))
                        results[model_id].append(0.0)
                    except Exception as exc:  # noqa: BLE001 - one bad response must not kill the whole batch
                        print(f"  [unexpected-error] {model_id} on record {rec['id']}: {exc!r}")
                        db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                                   is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                   completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd,
                                   error=f"unexpected: {exc!r}")
                        errors[model_id] += 1
                    else:
                        try:
                            result = scoring.score_field(args.field, value, rec["ground_truth"])
                        except Exception as exc:  # noqa: BLE001 - same rationale as above
                            print(f"  [unexpected-error] {model_id} on record {rec['id']}: {exc!r}")
                            db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value, score=0.0,
                                       is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                       completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd,
                                       error=f"unexpected: {exc!r}")
                            errors[model_id] += 1
                        else:
                            db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value,
                                       score=result.score, is_correct=int(result.is_correct), latency_ms=resp.latency_ms,
                                       prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                                       cost_usd=resp.cost_usd, error=None)
                            results[model_id].append(result.score)

                job_id = job_ids.get(model_id)
                if job_id is not None:
                    n_done = len(results[model_id]) + errors[model_id]
                    db.update_job_progress(conn, job_id, n_done)

                # Commit after every single result (not just once at the end
                # of the whole batch/context-manager) so a crash/kill
                # partway through doesn't lose already-completed, already
                # paid-for results.
                conn.commit()

            # Results are persisted to SQLite one at a time as each call
            # completes (see _on_complete), instead of only after the whole
            # batch finishes -- so a crash/kill partway through a large batch
            # doesn't lose every result already paid for.
            gateway.call_model_batch(jobs, max_workers=args.concurrency, on_complete=_on_complete)

            for model_id, job_id in job_ids.items():
                if job_id is not None:
                    db.finish_job(conn, job_id, status="completed")
    except Exception as exc:
        with db.get_conn() as conn:
            for model_id, job_id in job_ids.items():
                if job_id is not None:
                    db.finish_job(conn, job_id, status="failed", error=str(exc))
        raise

    print("\n=== Results ===")
    print(f"{'model':45s} {'n':>4s} {'mean_score':>10s} {'accuracy':>9s} {'errors':>7s}")
    for model_id in models:
        scores = results[model_id]
        n = len(scores)
        mean_score = sum(scores) / n if n else 0.0
        acc = sum(1 for s in scores if s >= scoring.CORRECT_THRESHOLD) / n if n else 0.0
        print(f"{model_id:45s} {n:4d} {mean_score:10.3f} {acc:9.1%} {errors[model_id]:7d}")


if __name__ == "__main__":
    main()
