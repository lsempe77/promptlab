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
    pv = prompt_store.get_or_create_baseline(conn, project_id, field)
    pvid = pv["id"]

    rows = conn.execute(
        "SELECT model_id, COUNT(DISTINCT record_id) AS c FROM runs "
        "WHERE project_id = ? AND field_name = ? AND prompt_version_id = ? AND error IS NULL "
        "GROUP BY model_id",
        (project_id, field, pvid),
    ).fetchall()
    per_model_refs = {r["model_id"]: r["c"] for r in rows}
    refs = min((per_model_refs.get(m, 0) for m in models), default=0) if models else 0

    unjudged = conn.execute(
        "SELECT COUNT(*) AS n FROM runs r WHERE r.project_id = ? AND r.field_name = ? "
        "AND r.prompt_version_id = ? AND r.parsed_value_json IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM llm_judgments j WHERE j.run_id = r.id)",
        (project_id, field, pvid),
    ).fetchone()["n"]

    jrows = conn.execute(
        "SELECT r.model_id, AVG(CASE WHEN j.verdict = 1 THEN 1.0 ELSE 0.0 END) AS acc, "
        "COUNT(*) AS n FROM llm_judgments j JOIN runs r ON r.id = j.run_id "
        "WHERE r.project_id = ? AND r.field_name = ? AND r.prompt_version_id = ? GROUP BY r.model_id",
        (project_id, field, pvid),
    ).fetchall()
    judged = {r["model_id"]: (r["acc"], r["n"]) for r in jrows if (r["n"] or 0) > 0}

    # Persistent exhaustion: we already proposed a candidate off THIS baseline
    # version and it was rejected, so re-optimizing the same baseline is unlikely
    # to help -- don't (this is what keeps a daemon from burning money in a loop).
    # When the baseline later advances (a candidate IS accepted), the new baseline
    # has no rejected children yet, so optimization resumes.
    exhausted = conn.execute(
        "SELECT 1 FROM iterations i JOIN prompt_versions pv ON pv.id = i.prompt_version_id "
        "WHERE i.project_id = ? AND i.field_name = ? AND i.accepted = 0 AND pv.parent_id = ? LIMIT 1",
        (project_id, field, pvid),
    ).fetchone() is not None

    return {"pv": pv, "pvid": pvid, "refs": refs, "unjudged": unjudged, "judged": judged, "exhausted": exhausted}


def _decide(state: dict, models: list[str]) -> tuple[str, int | None, str]:
    """Return (action, arg, reason). action in extract/judge/optimize/done/stuck."""
    stages = list(config.PRODUCTION_ROLLOUT_STAGES)
    final = stages[-1]
    refs, unjudged, judged = state["refs"], state["unjudged"], state["judged"]

    if refs == 0:
        return "extract", stages[0], "no production runs yet -> bootstrap stage 1"
    if unjudged > 0:
        return "judge", None, f"{unjudged} unjudged runs -> refresh the gate"

    judged_models = [m for m in models if m in judged]
    if not judged_models:
        return "judge", None, "runs exist but none judged yet -> judge"

    below = [m for m in judged_models if judged[m][0] < scoring.GATE_THRESHOLD]
    if below:
        if state["exhausted"]:
            return "stuck", None, (
                f"{len(below)} model(s) below gate {scoring.GATE_THRESHOLD:.0%} and this baseline's "
                "optimizer attempt was already rejected -> leave gated (needs prompt/ground-truth work)"
            )
        return "optimize", None, f"{len(below)} model(s) below gate {scoring.GATE_THRESHOLD:.0%} -> optimize prompt"

    next_stage = next((s for s in stages if s > refs), None)
    if next_stage is not None:
        return "extract", next_stage, f"all judged models pass; advance {refs} -> {next_stage} refs"
    return "done", None, f"at final stage ({final}) and all judged models pass -> production-ready"


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
                action, arg, reason = _decide(state, models)
                passing = sum(1 for _m, (acc, _n) in state["judged"].items() if acc >= scoring.GATE_THRESHOLD)
                _log(f"[{field}] v{state['pv']['version']} refs={state['refs']} unjudged={state['unjudged']} "
                     f"judged={len(state['judged'])} passing={passing}{' exhausted' if state['exhausted'] else ''} "
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
                    v_before = state["pv"]["version"]
                    _run(["backend.scripts.optimize_prompt", "--project", args.project, "--field", field,
                          "--model", args.model, "--reflector-model", args.reflector_model])
                    with db.get_conn() as conn:
                        project_id = db.get_project_id(conn, args.project)
                        v_after = prompt_store.get_or_create_baseline(conn, project_id, field)["version"]
                    if v_after != v_before:
                        _log(f"[{field}] optimizer ACCEPTED a new prompt v{v_before} -> v{v_after}")
                    else:
                        _log(f"[{field}] optimizer: no accepted improvement (baseline stays v{v_before}; "
                             "this baseline will not be re-optimized)")

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
