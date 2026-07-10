"""Multi-model batch extraction + scoring harness.

For a given field, samples N ground-truthed records, runs every configured
model (models.yaml) against the current baseline (or a given) prompt version,
scores each response against ground truth, stores every run in SQLite, and
prints a per-model comparison table.

Production rollout policy: validate at --n 100 first, then 200, then 300.
--n is hard-capped at config.MAX_PRODUCTION_RECORDS (300) to avoid accidentally
kicking off a run against the full ~7,600-record ground-truth pool.

Scaling up is incremental by default: a (record, model) pair that already has
a logged run for this field+prompt-version is skipped, so re-running with a
bigger --n (e.g. 30 -> 60 -> 100) only pays for the NEW records, not the ones
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

# Windows consoles default to cp1252, which raises UnicodeEncodeError when we
# print model output / author names with diacritics. Force UTF-8 so a single
# non-ASCII progress line can't kill the whole batch.
try:  # pragma: no cover
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.app import config, db, gateway, prompts, scoring  # noqa: E402
from backend.app import carbon  # noqa: E402
from backend.app import db_pg  # noqa: E402 -- Postgres layer (Phase 2)
from backend.app.corpus import read_md  # noqa: E402
from backend.app.parsing import ParseError, parse_field_response  # noqa: E402
from backend.app.prompt_store import get_or_create_baseline  # noqa: E402

# Phase 2 feature flag: when DATABASE_URL is set, writes go to Postgres.
# Reads (projects, GT, prompt_versions) always stay in SQLite on the coordinator.
_USE_PG = db_pg.pg_enabled()

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
    ap.add_argument("--logprobs", action="store_true",
                     help="request per-token logprobs and store a per-run logprob confidence "
                          "(mean token probability). Off by default: some providers reject the "
                          "param, and it's not needed for the core accuracy rollout.")
    args = ap.parse_args()

    if args.n > config.MAX_PRODUCTION_RECORDS:
        print(f"[warn] --n {args.n} exceeds the production cap of {config.MAX_PRODUCTION_RECORDS}; "
              f"clamping to {config.MAX_PRODUCTION_RECORDS}. Roll out in stages: "
              f"{', '.join(str(s) for s in config.PRODUCTION_ROLLOUT_STAGES)}.")
        args.n = config.MAX_PRODUCTION_RECORDS

    models = select_models(args)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        records = db.get_records_with_field(conn, project_id, args.field)
        # Per-model accepted baseline: each model runs its OWN current best
        # prompt (seeded from the shared baseline on first use).
        model_pv = {m: get_or_create_baseline(conn, project_id, args.field, model_id=m) for m in models}
        done_pairs: set[tuple[int, str]] = set()
        if not args.force:
            for m in models:
                for r in conn.execute(
                    "SELECT DISTINCT record_id FROM runs WHERE project_id = ? AND field_name = ? "
                    "AND model_id = ? AND prompt_version_id = ? AND error IS NULL",
                    (project_id, args.field, m, model_pv[m]["id"]),
                ).fetchall():
                    done_pairs.add((r["record_id"], m))

    if any(model_pv[m] is None for m in models):
        print(f"Failed to create/load a baseline prompt version for field={args.field}.")
        return
    if not records:
        print(f"No ground-truth records found for field={args.field}. Run build_ground_truth first.")
        return

    random.Random(args.seed).shuffle(records)
    records = records[: args.n]
    batch_id = uuid.uuid4().hex[:8]

    ver_summary = ", ".join(f"{m.split('/')[-1]}=v{model_pv[m]['version']}" for m in models)
    print(f"Field: {args.field} | records: {len(records)} | models: {len(models)} | concurrency: {args.concurrency}")
    print(f"Per-model prompt versions: {ver_summary}")
    print(f"Batch id: {batch_id}\n")

    results: dict[str, list[float]] = {m: [] for m in models}
    errors: dict[str, int] = {m: 0 for m in models}

    # Build every (record, model) job up front so all calls for this batch run
    # concurrently, instead of one record/model at a time sequentially. Pairs
    # already logged for this field+prompt version are skipped (unless
    # --force) so scaling --n up doesn't re-pay for smaller-stage records.
    jobs: list[dict] = []
    job_meta: list[tuple[dict, str]] = []
    source_cache: dict[int, str] = {}  # truncated md text the model actually saw, for excerpt verification
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
        source_cache[rec["id"]] = md_text[: prompts.MAX_CHARS]
        for model_id in needed_models:
            # each model uses its own accepted prompt template
            system_prompt, user_prompt = prompts.build_prompt(
                args.field, rec["title"] or "", md_text, instruction=model_pv[model_id]["template"]
            )
            jobs.append({"model_id": model_id, "system_prompt": system_prompt, "user_prompt": user_prompt,
                         "logprobs": args.logprobs})
            job_meta.append((rec, model_id))
            new_counts[model_id] += 1

    if skipped:
        print(f"Skipping {skipped} (record, model) pairs already logged for this field+prompt version "
              f"(use --force to re-run them).")
    if not jobs:
        print("Nothing new to run.")
        return

    # Phase 2: open Postgres connection alongside SQLite when DATABASE_URL is set.
    # Writes (runs, jobs) go to Postgres; reads stay in SQLite.
    _pg_ctx = db_pg.get_pg_conn() if _USE_PG else None
    _pg_conn = _pg_ctx.__enter__() if _pg_ctx else None

    def _w_add_run(sqlite_conn, **kwargs):
        # Always write to SQLite — it is the coordinator's ground truth.
        db.add_run(sqlite_conn, **kwargs)
        # Also mirror to Postgres when available (for analytics / multi-machine).
        if _pg_conn:
            db_pg.add_run_pg(_pg_conn, **kwargs)

    def _w_start_job(sqlite_conn, *args, **kwargs):
        if _pg_conn:
            return db_pg.start_job_pg(_pg_conn, *args, **kwargs)
        return db.start_job(sqlite_conn, *args, **kwargs)

    def _w_update_job(sqlite_conn, job_id, n_done):
        if _pg_conn:
            db_pg.update_job_progress_pg(_pg_conn, job_id, n_done)
        else:
            db.update_job_progress(sqlite_conn, job_id, n_done)

    def _w_finish_job(sqlite_conn, job_id, **kwargs):
        if _pg_conn:
            db_pg.finish_job_pg(_pg_conn, job_id, **kwargs)
        else:
            db.finish_job(sqlite_conn, job_id, **kwargs)

    try:
        with db.get_conn() as conn:
            job_ids = {m: _w_start_job(conn, project_id, args.field, m, kind="extraction", total=new_counts[m]) for m in models}

        try:
            with db.get_conn() as conn:

                def _on_complete(i: int, resp: gateway.ModelResponse | gateway.GatewayError) -> None:
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
                        _w_add_run(conn, **run_kwargs, raw_response=None, parsed_value=None, score=None,
                                   is_correct=0, latency_ms=None, prompt_tokens=None, completion_tokens=None,
                                   cost_usd=None, error=str(resp))
                        errors[model_id] += 1
                    else:
                        try:
                            value, meta = parse_field_response(args.field, resp.content)
                        except ParseError as exc:
                            print(f"  [parse-error] {model_id} on record {rec['id']}: {exc}")
                            _w_add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                                       is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                       completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd, error=str(exc))
                            results[model_id].append(0.0)
                        except Exception as exc:  # noqa: BLE001
                            print(f"  [unexpected-error] {model_id} on record {rec['id']}: {exc!r}")
                            _w_add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                                       is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                       completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd,
                                       error=f"unexpected: {exc!r}")
                            errors[model_id] += 1
                        else:
                            verified = scoring.verify_excerpt(meta.get("excerpt"), source_cache.get(rec["id"]))
                            verified_col = None if verified is None else int(verified)
                            try:
                                result = scoring.score_field(args.field, value, rec["ground_truth"],
                                                             excerpt_verified=verified)
                            except Exception as exc:  # noqa: BLE001
                                print(f"  [unexpected-error] {model_id} on record {rec['id']}: {exc!r}")
                                _w_add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value, score=0.0,
                                           excerpt=meta.get("excerpt"), notes=meta.get("notes"),
                                           excerpt_verified=verified_col, confidence=meta.get("confidence"),
                                           is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                                           completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd,
                                           error=f"unexpected: {exc!r}")
                                errors[model_id] += 1
                            else:
                                _w_add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value,
                                           excerpt=meta.get("excerpt"), notes=meta.get("notes"),
                                           excerpt_verified=verified_col, confidence=meta.get("confidence"),
                                           outcome=result.outcome, honesty_score=result.honesty_score,
                                           logprob_confidence=resp.logprob_confidence,
                                           score=result.score, is_correct=int(result.is_correct), latency_ms=resp.latency_ms,
                                           prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                                           cost_usd=resp.cost_usd,
                                           co2e_grams=carbon.estimate_co2e_grams(model_id, resp.completion_tokens, resp.latency_ms),
                                           error=None)
                                results[model_id].append(result.score)

                    job_id = job_ids.get(model_id)
                    if job_id is not None:
                        n_done = len(results[model_id]) + errors[model_id]
                        _w_update_job(conn, job_id, n_done)

                    # Commit per result so a crash/kill only loses in-flight calls.
                    # SQLite is the coordinator's ground truth and is always written;
                    # the PG mirror (psycopg2 is NOT autocommit) must be committed too.
                    conn.commit()
                    if _pg_conn:
                        _pg_conn.commit()

                gateway.call_model_batch(jobs, max_workers=args.concurrency, on_complete=_on_complete)

                for model_id, job_id in job_ids.items():
                    if job_id is not None:
                        _w_finish_job(conn, job_id, status="completed")
        except Exception as exc:
            with db.get_conn() as conn:
                for model_id, job_id in job_ids.items():
                    if job_id is not None:
                        _w_finish_job(conn, job_id, status="failed", error=str(exc))
            raise
    finally:
        if _pg_ctx and _pg_conn:
            _pg_ctx.__exit__(None, None, None)

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
