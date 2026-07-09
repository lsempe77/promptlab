"""Stateless worker process for Phase 2 horizontal scaling.

Polls the `worker_tasks` table in Postgres, atomically claims one task
(FOR UPDATE SKIP LOCKED), executes it by shelling out to the existing scripts,
and marks it done/failed. Multiple workers can run on different machines
simultaneously — the FOR UPDATE SKIP LOCKED guarantee prevents double-claiming.

Usage:
    python -m backend.scripts.worker --project dep-extraction

On Fly.io:
    fly machine clone <coordinator_id> --app dep-promptlab-api --no-volumes
    # Then SSH in and run:
    nohup python -m backend.scripts.worker --loop >> /tmp/worker.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from backend.app import db_pg  # noqa: E402

WORKER_ID = f"{socket.gethostname()}-{id(object())}"
POLL_INTERVAL_S = 5   # seconds between polls when queue is empty
HEARTBEAT_S = 30      # update updated_at so coordinator knows worker is alive


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [worker:{WORKER_ID[:12]}] {msg}", flush=True)


def _execute_task(task: dict) -> None:
    """Shell out to the right script based on task kind."""
    kind = task["kind"]
    args = task.get("args_json") or {}
    if isinstance(args, str):
        args = json.loads(args)

    project = args.get("project", "dep-extraction")
    field = task.get("field_name", "")
    model = task.get("model_id") or ""

    if kind == "extraction":
        cmd = [
            sys.executable, "-m", "backend.scripts.run_extraction",
            "--project", project,
            "--field", field,
            "--n", str(args.get("n", 100)),
        ]
        if model:
            cmd += ["--models", model]

    elif kind == "optimization":
        reflector = args.get("reflector_model", "~anthropic/claude-sonnet-latest")
        eps = str(args.get("improvement_epsilon", 0.01))
        cmd = [
            sys.executable, "-m", "backend.scripts.optimize_prompt",
            "--project", project,
            "--field", field,
            "--model", model,
            "--reflector-model", reflector,
            "--improvement-epsilon", eps,
        ]

    elif kind == "judge":
        cmd = [
            sys.executable, "-m", "backend.scripts.llm_judge",
            "--project", project,
            "--field", field,
            "--n", str(args.get("n", 100000)),
            "--cross-family",
        ]

    else:
        raise ValueError(f"Unknown task kind: {kind!r}")

    _log(f"Executing: {' '.join(cmd[2:])}")
    proc = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[2]))
    if proc.returncode != 0:
        raise RuntimeError(f"Script exited with code {proc.returncode}")


def run_once(project_id: int | None = None) -> bool:
    """Claim and execute one task. Returns True if a task was processed."""
    with db_pg.get_pg_conn() as pg:
        task = db_pg.claim_task(pg, WORKER_ID)
        if not task:
            return False

        task_id = task["id"]
        _log(f"Claimed task {task_id}: {task['kind']} / {task.get('field_name')} / {task.get('model_id','*')}")

    try:
        _execute_task(task)
        with db_pg.get_pg_conn() as pg:
            db_pg.finish_task(pg, task_id, status="done")
        _log(f"Task {task_id} done.")
    except Exception as exc:
        _log(f"Task {task_id} FAILED: {exc}")
        with db_pg.get_pg_conn() as pg:
            db_pg.finish_task(pg, task_id, status="failed", error=str(exc)[:500])
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--loop", action="store_true", help="Run forever, polling for tasks")
    ap.add_argument("--project", default=None, help="Filter tasks by project slug (optional)")
    ap.add_argument("--poll-interval", type=int, default=POLL_INTERVAL_S)
    args = ap.parse_args()

    if not db_pg.pg_enabled():
        print("ERROR: DATABASE_URL is not set. Workers require Postgres.", file=sys.stderr)
        sys.exit(1)

    _log(f"Worker starting (loop={args.loop}, poll={args.poll_interval}s)")

    if args.loop:
        while True:
            try:
                did_work = run_once()
                if not did_work:
                    time.sleep(args.poll_interval)
            except KeyboardInterrupt:
                _log("Shutting down.")
                break
            except Exception as exc:
                _log(f"Unexpected error: {exc}. Retrying in {args.poll_interval}s.")
                time.sleep(args.poll_interval)
    else:
        run_once()
        _log("Done (single-shot mode).")


if __name__ == "__main__":
    main()
