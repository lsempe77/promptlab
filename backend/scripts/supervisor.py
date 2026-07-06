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
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - Windows console cp1252 guard
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.app import config, db, prompt_store, scoring  # noqa: E402
from backend.app.prompts import BASELINE_INSTRUCTIONS  # noqa: E402


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def _run(args: list[str]) -> int:
    """Run one of the pipeline scripts as a subprocess, streaming its output."""
    cmd = [sys.executable, "-m", *args]
    _log(f"$ {' '.join(args)}")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


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
            "unjudged": unjudged, "judged": judged, "exhausted": exhausted}


def _decide(state: dict, models: list[str]) -> tuple[str, int | None, list[str], str]:
    """Return (action, arg, optimize_models, reason)."""
    stages = list(config.PRODUCTION_ROLLOUT_STAGES)
    final = stages[-1]
    refs, unjudged, judged = state["refs"], state["unjudged"], state["judged"]

    if refs == 0:
        return "extract", stages[0], [], "a model has no runs on its current prompt -> bootstrap/refill stage 1"
    if unjudged > 0:
        return "judge", None, [], f"{unjudged} unjudged runs -> refresh the gate"

    judged_models = [m for m in models if m in judged]
    if not judged_models:
        return "judge", None, [], "runs exist but none judged yet -> judge"

    below = [m for m in judged_models if judged[m][0] < scoring.GATE_THRESHOLD]
    optimizable = [m for m in below if not state["exhausted"].get(m)]
    if optimizable:
        return "optimize", None, optimizable, (
            f"{len(optimizable)} model(s) below gate {scoring.GATE_THRESHOLD:.0%} -> optimize each on its own prompt"
        )
    if below:
        return "stuck", None, [], (
            f"{len(below)} model(s) below gate {scoring.GATE_THRESHOLD:.0%}, each already has a rejected "
            "candidate -> gated (needs prompt/ground-truth work)"
        )

    next_stage = next((s for s in stages if s > refs), None)
    if next_stage is not None:
        return "extract", next_stage, [], f"all judged models pass; advance {refs} -> {next_stage} refs"
    return "done", None, [], f"at final stage ({final}) and all judged models pass -> production-ready"


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
    args = ap.parse_args()

    fields = [f.strip() for f in args.fields.split(",")] if args.fields else list(BASELINE_INSTRUCTIONS.keys())
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()] or None
    models = _target_models(tiers)

    db.init_db()  # idempotent: ensures schema + migrations (e.g. prompt_versions.model_id) are applied

    _log(f"Supervisor start | project={args.project} | fields={fields} | models={len(models)} "
         f"| stages={config.PRODUCTION_ROLLOUT_STAGES} | gate={scoring.GATE_THRESHOLD:.0%} "
         f"| max_cycles={args.max_cycles}"
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
                passing = sum(1 for _m, (acc, _n) in state["judged"].items() if acc >= scoring.GATE_THRESHOLD)
                _log(f"[{field}] refs={state['refs']} unjudged={state['unjudged']} "
                     f"judged={len(state['judged'])} passing={passing}/{len(models)} "
                     f"-> {action.upper()} {arg if arg is not None else ''} ({reason})")

                if action in ("done", "stuck"):
                    continue
                any_action = True
                if args.dry_run:
                    continue

                if action == "extract":
                    _run(["backend.scripts.run_extraction", "--project", args.project, "--field", field,
                          "--n", str(arg), "--tiers", args.tiers, "--logprobs"])
                elif action == "judge":
                    _run(["backend.scripts.llm_judge", "--project", args.project, "--field", field,
                          "--n", "100000", "--cross-family"])
                elif action == "optimize":
                    # per-model prompts: optimize each below-gate model on its own lineage
                    for m in opt_models:
                        _run(["backend.scripts.optimize_prompt", "--project", args.project, "--field", field,
                              "--model", m, "--reflector-model", args.reflector_model])

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
        _log(f"Sleeping {args.interval}s before next run...")
        time.sleep(args.interval)

    _log("Supervisor done.")


if __name__ == "__main__":
    main()
