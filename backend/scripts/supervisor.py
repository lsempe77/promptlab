"""Autonomous supervisor for the agentic pipeline.

Drives the whole loop end to end with no manual step:
    extract -> judge -> gate-check -> (optimize | advance) -> repeat
across every field of a project, until each field is either production-ready
(at the final rollout stage with every model past the quality gate) or can't be
pushed further (optimizer exhausted). It shells out to the existing, tested
scripts (run_extraction / llm_judge / optimize_prompt) and reads the DB to
decide what to do next, so it reuses their logic and their own internal caps.

SAFETY / STOP-RULES (this loop spends money, so it is deliberately bounded):
  * self-terminating: stops as soon as no field has an actionable step, or after
    --max-cycles cycles (hard cap), whichever comes first.
  * never extracts beyond config.MAX_PRODUCTION_RECORDS (run_extraction clamps).
  * a field whose optimizer run produced no accepted improvement is marked
    "exhausted" and is not re-optimized again in this supervisor run.
  * --dry-run prints the decision plan and spends nothing.

Usage (from repo root):
    python -m backend.scripts.supervisor --project dep-extraction --dry-run
    python -m backend.scripts.supervisor --project dep-extraction --max-cycles 8
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - Windows console cp1252 guard
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.app import analytics, config, db, prompt_store, scoring  # noqa: E402
from backend.app.optimizer import FIELD_IMPROVEMENT_EPSILON, DEFAULT_IMPROVEMENT_EPSILON  # noqa: E402
from backend.app.scoring import RECALL_FLOOR  # noqa: E402
from backend.app import db_pg  # noqa: E402 -- Phase 2: task-queue mode
from backend.app.prompts import BASELINE_INSTRUCTIONS  # noqa: E402

_USE_PG = db_pg.pg_enabled()


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def _run(args: list[str], stagger_s: float = 0.0) -> int:
    """Run one of the pipeline scripts as a subprocess, streaming its output."""
    if stagger_s:
        time.sleep(stagger_s)
    cmd = [sys.executable, "-m", *args]
    _log(f"$ {' '.join(args)}")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def _run_parallel(cmds: list[list[str]], max_workers: int) -> list[int]:
    """Run multiple pipeline-script commands concurrently (one subprocess each).
    Workers are staggered by 2 s to avoid simultaneous DB write collisions at startup.
    Each command is a list of module+args as passed to _run. Returns exit codes."""
    if len(cmds) == 1:
        return [_run(cmds[0])]
    _log(f"Launching {len(cmds)} tasks in parallel (max_workers={max_workers})")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_run, cmd, stagger_s=i * 2.0): cmd for i, cmd in enumerate(cmds)}
        results = []
        for f in as_completed(futures):
            results.append(f.result())
    return results


def _target_models(tiers: list[str] | None) -> list[str]:
    roster = config.load_models()
    if tiers:
        roster = [m for m in roster if m["tier"] in tiers]
    return [m["id"] for m in roster]


def _field_state(conn, project_id: int, field: str, models: list[str]) -> dict:
    """Current pipeline state for a field, scoped to its current baseline/accepted
    prompt version. `refs` is the MIN distinct-record count across target models,
    so a stage only counts as reached once every model has that many records."""
    pv_of = {m: prompt_store.get_or_create_baseline(conn, project_id, field, model_id=m) for m in models}
    per_model_refs: dict[str, int] = {}
    unjudged = 0
    judged: dict[str, tuple[float, int]] = {}
    gate: dict[str, tuple[float, int]] = {}  # runs-based gate metric (F1 for lists / accuracy for categorical)
    exhausted: dict[str, bool] = {}
    for m in models:
        pvid = pv_of[m]["id"]
        per_model_refs[m] = conn.execute(
            "SELECT COUNT(DISTINCT record_id) AS c FROM runs WHERE project_id = ? AND field_name = ? "
            "AND model_id = ? AND prompt_version_id = ? AND error IS NULL",
            (project_id, field, m, pvid),
        ).fetchone()["c"]
        unjudged += conn.execute(
            "SELECT COUNT(*) AS n FROM runs r WHERE r.project_id = ? AND r.field_name = ? AND r.model_id = ? "
            "AND r.prompt_version_id = ? AND r.parsed_value_json IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM llm_judgments j WHERE j.run_id = r.id)",
            (project_id, field, m, pvid),
        ).fetchone()["n"]
        jr = conn.execute(
            "SELECT AVG(CASE WHEN j.verdict = 1 THEN 1.0 ELSE 0.0 END) AS acc, COUNT(*) AS n "
            "FROM llm_judgments j JOIN runs r ON r.id = j.run_id "
            "WHERE r.project_id = ? AND r.field_name = ? AND r.model_id = ? AND r.prompt_version_id = ?",
            (project_id, field, m, pvid),
        ).fetchone()
        if jr and (jr["n"] or 0) > 0:
            judged[m] = (jr["acc"], jr["n"])
        # Gate metric: computed from runs (no judge needed), matching the dashboard's
        # stage-status gate -- F1 for list fields, accuracy for categorical.
        grows = conn.execute(
            "SELECT r.parsed_value_json, g.value_json FROM runs r "
            "JOIN ground_truth g ON g.project_id = r.project_id AND g.record_id = r.record_id "
            "AND g.field_name = r.field_name "
            "WHERE r.project_id = ? AND r.field_name = ? AND r.model_id = ? AND r.prompt_version_id = ? "
            "AND r.parsed_value_json IS NOT NULL",
            (project_id, field, m, pvid),
        ).fetchall()
        if grows:
            mrows = [{"predicted": json.loads(x["parsed_value_json"]), "truth": json.loads(x["value_json"])}
                     for x in grows]
            gm = analytics.gate_metrics(field, mrows)
            # gate[m] = (primary_metric, n, recall_or_None)
            # recall is only meaningful for list fields; None for categorical.
            gate[m] = (gm["metric"], gm["n"], gm.get("recall"))
        # per-model persistent exhaustion: a rejected candidate off THIS model's
        # current prompt means don't re-optimize this model on this prompt.
        exhausted[m] = conn.execute(
            "SELECT 1 FROM iterations i JOIN prompt_versions pv ON pv.id = i.prompt_version_id "
            "WHERE i.project_id = ? AND i.field_name = ? AND i.model_id = ? AND i.accepted = 0 "
            "AND pv.parent_id = ? LIMIT 1",
            (project_id, field, m, pvid),
        ).fetchone() is not None
    refs = min((per_model_refs.get(m, 0) for m in models), default=0) if models else 0
    return {"pv_of": pv_of, "refs": refs, "per_model_refs": per_model_refs,
            "unjudged": unjudged, "judged": judged, "gate": gate, "exhausted": exhausted}


def _decide(state: dict, models: list[str]) -> tuple[str, int | None, list[str], str]:
    """Return (action, arg, optimize_models, reason)."""
    stages = list(config.PRODUCTION_ROLLOUT_STAGES)
    final = stages[-1]
    refs, unjudged, gate = state["refs"], state["unjudged"], state["gate"]

    if refs == 0:
        return "extract", stages[0], [], "a model has no runs on its current prompt -> bootstrap/refill stage 1"
    if unjudged > 0:
        return "judge", None, [], f"{unjudged} unjudged runs -> refresh the concordance metric"

    evaluated = [m for m in models if m in gate]
    if not evaluated:
        return "judge", None, [], "runs exist but no gate metric yet -> judge"

    def _passes_gate(m: str) -> bool:
        """True if model passes both F1/accuracy gate AND the recall floor (list fields only)."""
        metric, _n, recall = gate[m]
        if metric < scoring.GATE_THRESHOLD:
            return False
        # Recall floor applies only to list fields (recall is None for categorical)
        if recall is not None and recall < RECALL_FLOOR:
            return False
        return True

    # Gate on the runs-based quality metric (F1 for list fields, accuracy for
    # categorical) -- same metric the dashboard shows -- not judged accuracy.
    below = [m for m in evaluated if not _passes_gate(m)]
    optimizable = [m for m in below if not state["exhausted"].get(m)]
    if optimizable:
        return "optimize", None, optimizable, (
            f"{len(optimizable)} model(s) below gate {scoring.GATE_THRESHOLD:.0%} -> optimize each on its own prompt"
        )

    # Advance when the BEST model passes gate (not "all must pass").
    # Weaker models continue to be optimized at the next stage level.
    # This prevents a single broken/weak model from blocking field progression.
    best_metric = max((gate[m][0] for m in evaluated if _passes_gate(m)), default=0.0)
    if best_metric >= scoring.GATE_THRESHOLD:
        next_stage = next((s for s in stages if s > refs), None)
        if next_stage is not None:
            return "extract", next_stage, [], (
                f"best model at {best_metric:.2%} (>= gate); advance {refs} -> {next_stage} refs"
            )
        return "done", None, [], f"at final stage ({stages[-1]}) and best model passes gate -> production-ready"

    if below:
        return "stuck", None, [], (
            f"{len(below)} model(s) below gate {scoring.GATE_THRESHOLD:.0%}, each already has a rejected "
            "candidate -> gated (needs prompt/ground-truth work)"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--fields", type=str, default=None, help="comma-separated subset (default: all)")
    ap.add_argument("--tiers", type=str, default="cheap,mid,expensive")
    ap.add_argument("--model", default="~openai/gpt-mini-latest", help="model the optimizer improves the prompt for")
    ap.add_argument("--reflector-model", default="~anthropic/claude-sonnet-latest")
    ap.add_argument("--max-cycles", type=int, default=8, help="hard cap on supervisor cycles per run (safety brake)")
    ap.add_argument("--loop", action="store_true", help="run forever (daemon): re-check every --interval seconds")
    ap.add_argument("--interval", type=int, default=10800, help="seconds to sleep between runs when --loop (default 3h)")
    ap.add_argument("--max-runs", type=int, default=0, help="stop the daemon after this many runs (0 = unlimited)")
    ap.add_argument("--dry-run", action="store_true", help="print the decision plan without running anything")
    ap.add_argument("--parallelism", type=int, default=1,
                    help="max concurrent model-level tasks (extract / optimize). "
                         "Each task is a subprocess. Default 1 = sequential (safe for any machine size). "
                         "Set to 4-8 on a 2+ CPU Fly machine for N× throughput.")
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else list(BASELINE_INSTRUCTIONS.keys())
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()] or None
    models = _target_models(tiers)

    db.init_db()  # idempotent: ensures schema + migrations (e.g. prompt_versions.model_id) are applied

    _log(f"Supervisor start | project={args.project} | fields={fields} | models={len(models)} "
         f"| stages={config.PRODUCTION_ROLLOUT_STAGES} | gate={scoring.GATE_THRESHOLD:.0%} "
         f"| max_cycles={args.max_cycles} | parallelism={args.parallelism}"
         f"{' | LOOP every %ds' % args.interval if args.loop else ''}"
         f"{' | DRY RUN' if args.dry_run else ''}")

    run_no = 0
    while True:
        run_no += 1
        _log(f"===== supervisor run {run_no}{' (daemon)' if args.loop else ''} =====")

        for cycle in range(1, args.max_cycles + 1):
            _log(f"--- cycle {cycle}/{args.max_cycles} ---")
            any_action = False

            for field in fields:
                with db.get_conn() as conn:
                    project_id = db.get_project_id(conn, args.project)
                    state = _field_state(conn, project_id, field, models)
                action, arg, opt_models, reason = _decide(state, models)
                passing = sum(
                    1 for _m, (score, _n, recall) in state["gate"].items()
                    if score >= scoring.GATE_THRESHOLD and (recall is None or recall >= RECALL_FLOOR)
                )
                _log(f"[{field}] refs={state['refs']} unjudged={state['unjudged']} "
                     f"judged={len(state['judged'])} passing={passing}/{len(models)} "
                     f"-> {action.upper()} {arg if arg is not None else ''} ({reason})")

                if action in ("done", "stuck"):
                    continue
                any_action = True
                if args.dry_run:
                    continue

                if action == "extract":
                    if _USE_PG:
                        # Phase 2: enqueue one extraction task per model
                        with db_pg.get_pg_conn() as pg:
                            with db.get_conn() as conn:
                                project_id = db.get_project_id(conn, args.project)
                            enqueued = sum(
                                1 for m in models
                                if db_pg.enqueue_task(pg, project_id, field, m, "extraction", {
                                    "project": args.project, "n": arg,
                                }, priority=2) is not None
                            )
                        _log(f"[{field}] Enqueued {enqueued}/{len(models)} extraction tasks → stage {arg}.")
                    elif args.parallelism > 1:
                        cmds = [
                            ["backend.scripts.run_extraction", "--project", args.project,
                             "--field", field, "--n", str(arg), "--models", m, "--logprobs"]
                            for m in models
                        ]
                        _run_parallel(cmds, max_workers=args.parallelism)
                    else:
                        _run(["backend.scripts.run_extraction", "--project", args.project, "--field", field,
                              "--n", str(arg), "--tiers", args.tiers, "--logprobs"])
                elif action == "judge":
                    if _USE_PG:
                        with db_pg.get_pg_conn() as pg:
                            with db.get_conn() as conn:
                                project_id = db.get_project_id(conn, args.project)
                            result = db_pg.enqueue_task(pg, project_id, field, None, "judge", {
                                "project": args.project,
                            }, priority=3)
                        _log(f"[{field}] {'Enqueued judge task.' if result else 'Judge task already queued.'}")
                    else:
                        _run(["backend.scripts.llm_judge", "--project", args.project, "--field", field,
                              "--n", "100000", "--cross-family"])
                elif action == "optimize":
                    eps = FIELD_IMPROVEMENT_EPSILON.get(field, DEFAULT_IMPROVEMENT_EPSILON)
                    if _USE_PG:
                        # Phase 2: enqueue tasks — workers execute in parallel
                        with db_pg.get_pg_conn() as pg:
                            with db.get_conn() as conn:
                                project_id = db.get_project_id(conn, args.project)
                            enqueued = sum(
                                1 for m in opt_models
                                if db_pg.enqueue_task(pg, project_id, field, m, "optimization", {
                                    "project": args.project,
                                    "reflector_model": args.reflector_model,
                                    "improvement_epsilon": eps,
                                }, priority=1) is not None
                            )
                        _log(f"[{field}] Enqueued {enqueued}/{len(opt_models)} optimization tasks (rest already queued).")
                    else:
                        # Phase 1 fallback: sequential shell-out
                        for m in opt_models:
                            _run(["backend.scripts.optimize_prompt", "--project", args.project, "--field", field,
                                  "--model", m, "--reflector-model", args.reflector_model,
                                  "--improvement-epsilon", str(eps)])

            if not any_action:
                _log("Converged: no field has an actionable step this run.")
                break
        else:
            _log(f"Reached max_cycles={args.max_cycles} (safety cap) for this run.")

        if args.dry_run or not args.loop:
            break
        if args.max_runs and run_no >= args.max_runs:
            _log(f"Reached max_runs={args.max_runs}. Stopping daemon.")
            break

        # Phase 2: in PG/task-queue mode, wait for all enqueued tasks to finish
        # before sleeping, so the next cycle sees the updated SQLite state.
        if _USE_PG:
            with db.get_conn() as conn:
                project_id = db.get_project_id(conn, args.project)
            with db_pg.get_pg_conn() as pg:
                n_pending = db_pg.pending_task_count(pg, project_id)
            if n_pending > 0:
                _log(f"Waiting for {n_pending} worker task(s) to complete before next run...")
                while n_pending > 0:
                    time.sleep(30)
                    with db_pg.get_pg_conn() as pg:
                        n_pending = db_pg.pending_task_count(pg, project_id)
                _log("Queue drained — starting next run.")

        _log(f"Sleeping {args.interval}s before next run...")
        time.sleep(args.interval)

    _log("Supervisor done.")


if __name__ == "__main__":
    main()
