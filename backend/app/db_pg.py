"""Postgres data-access layer for Phase 2 horizontal scaling.

Replaces the write-heavy SQLite tables (runs, iterations, llm_judgments,
jobs, worker_tasks) with Postgres, allowing multiple worker machines to
write concurrently without hitting SQLite's single-writer limit.

SQLite stays for coordinator-owned read-mostly tables:
  projects, records, ground_truth, prompt_versions

Feature flag: only activated when DATABASE_URL env var is set.
Falls back gracefully to SQLite-only mode when not set.

Connection: psycopg2 ThreadedConnectionPool (min=2, max=10).
            Shared globally; workers use get_pg_conn() context manager.
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

# --- optional import: only fail at runtime if DATABASE_URL is set but psycopg2 missing
try:
    import psycopg2
    import psycopg2.extras
    import psycopg2.pool
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None
_pool_lock = threading.Lock()


def pg_enabled() -> bool:
    """Returns True when Postgres is configured and available."""
    return bool(DATABASE_URL)


def _get_pool() -> "psycopg2.pool.ThreadedConnectionPool":
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is not None:
            return _pool
        if not _HAS_PSYCOPG2:
            raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL env var not set.")
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            dsn=DATABASE_URL,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
    return _pool


@contextmanager
def get_pg_conn():
    """Context manager: borrow a connection from the pool, auto-return on exit."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def now_pg() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS runs (
    id           SERIAL PRIMARY KEY,
    project_id   INTEGER NOT NULL,
    prompt_version_id INTEGER NOT NULL,
    model_id     TEXT NOT NULL,
    record_id    INTEGER NOT NULL,
    field_name   TEXT NOT NULL,
    raw_response TEXT,
    parsed_value_json TEXT,
    excerpt      TEXT,
    notes        TEXT,
    score        REAL,
    honesty_score REAL,
    is_correct   INTEGER,
    outcome      TEXT,
    logprob_confidence REAL,
    excerpt_verified   INTEGER,
    confidence   REAL,
    latency_ms   INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd     REAL,
    error        TEXT,
    batch_id     TEXT,
    created_at   TIMESTAMPTZ DEFAULT now(),
    co2e_grams   REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_project_field ON runs (project_id, field_name);
CREATE INDEX IF NOT EXISTS idx_runs_pv_model ON runs (prompt_version_id, model_id);

CREATE TABLE IF NOT EXISTS llm_judgments (
    id         SERIAL PRIMARY KEY,
    run_id     INTEGER NOT NULL,
    judge_model TEXT NOT NULL,
    verdict    INTEGER NOT NULL,
    reasoning  TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (run_id, judge_model)
);
CREATE INDEX IF NOT EXISTS idx_judgments_run ON llm_judgments (run_id);

CREATE TABLE IF NOT EXISTS iterations (
    id               SERIAL PRIMARY KEY,
    project_id       INTEGER NOT NULL,
    field_name       TEXT NOT NULL,
    iteration_num    INTEGER NOT NULL,
    prompt_version_id INTEGER NOT NULL,
    model_id         TEXT,
    mean_score       REAL,
    n_records        INTEGER,
    feedback         TEXT,
    accepted         INTEGER DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_iterations_field ON iterations (project_id, field_name, model_id);

CREATE TABLE IF NOT EXISTS jobs (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    field_name  TEXT NOT NULL,
    model_id    TEXT,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    total       INTEGER,
    completed   INTEGER DEFAULT 0,
    started_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_project_field ON jobs (project_id, field_name, status);

CREATE TABLE IF NOT EXISTS worker_tasks (
    id          SERIAL PRIMARY KEY,
    project_id  INTEGER NOT NULL,
    field_name  TEXT NOT NULL,
    model_id    TEXT,
    kind        TEXT NOT NULL,       -- 'extraction' | 'optimization' | 'judge'
    args_json   JSONB NOT NULL DEFAULT '{}',
    priority    INTEGER DEFAULT 0,   -- higher = more urgent
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed
    worker_id   TEXT,
    claimed_at  TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error       TEXT,
    created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tasks_pending ON worker_tasks (status, priority DESC, id)
    WHERE status = 'pending';
"""


def init_pg() -> None:
    """Create all Postgres tables (idempotent — safe to call on every startup)."""
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_PG)


# ── Write helpers ─────────────────────────────────────────────────────────────

def add_run_pg(conn, **kwargs: Any) -> int | None:
    """Insert one run row into Postgres. Same kwargs interface as db.add_run()."""
    kwargs.setdefault("created_at", now_pg())
    if "parsed_value" in kwargs:
        kwargs["parsed_value_json"] = json.dumps(kwargs.pop("parsed_value"), ensure_ascii=False)
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f"%({k})s" for k in kwargs)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO runs ({cols}) VALUES ({placeholders}) RETURNING id", kwargs
        )
        row = cur.fetchone()
        return row["id"] if row else None


def add_iteration_pg(conn, **kwargs: Any) -> int | None:
    kwargs.setdefault("created_at", now_pg())
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f"%({k})s" for k in kwargs)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO iterations ({cols}) VALUES ({placeholders}) RETURNING id", kwargs
        )
        row = cur.fetchone()
        return row["id"] if row else None


def add_llm_judgment_pg(conn, run_id: int, judge_model: str, verdict: bool, reasoning: str | None) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO llm_judgments (run_id, judge_model, verdict, reasoning) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (run_id, judge_model) DO UPDATE SET verdict=EXCLUDED.verdict, "
            "reasoning=EXCLUDED.reasoning, created_at=now() RETURNING id",
            (run_id, judge_model, int(verdict), reasoning),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def start_job_pg(conn, project_id: int, field_name: str, model_id: str | None,
                 kind: str, total: int | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (project_id, field_name, model_id, kind, total, status) "
            "VALUES (%s, %s, %s, %s, %s, 'running') RETURNING id",
            (project_id, field_name, model_id, kind, total),
        )
        return cur.fetchone()["id"]


def update_job_progress_pg(conn, job_id: int, completed: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET completed=%s, updated_at=now() WHERE id=%s",
            (completed, job_id),
        )


def finish_job_pg(conn, job_id: int, status: str = "completed", error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status=%s, finished_at=now(), updated_at=now(), error=%s WHERE id=%s",
            (status, error, job_id),
        )


# ── Read helpers (for API) ────────────────────────────────────────────────────

def get_runs_pg(conn, project_id: int, field_name: str, prompt_version_id: int,
                model_id: str) -> list[dict]:
    """Read runs from Postgres for a specific (project, field, pv, model)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM runs WHERE project_id=%s AND field_name=%s "
            "AND prompt_version_id=%s AND model_id=%s",
            (project_id, field_name, prompt_version_id, model_id),
        )
        return [dict(r) for r in cur.fetchall()]


def get_jobs_pg(conn, project_id: int, field_name: str) -> list[dict]:
    """Read recent jobs for a field from Postgres."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM jobs WHERE project_id=%s AND field_name=%s ORDER BY id DESC LIMIT 20",
            (project_id, field_name),
        )
        return [dict(r) for r in cur.fetchall()]


def get_judgments_pg(conn, run_ids: list[int]) -> list[dict]:
    """Read LLM judgments for a list of run_ids."""
    if not run_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM llm_judgments WHERE run_id = ANY(%s)",
            (run_ids,),
        )
        return [dict(r) for r in cur.fetchall()]


# ── Worker task queue ─────────────────────────────────────────────────────────

def enqueue_task(conn, project_id: int, field_name: str, model_id: str | None,
                 kind: str, args: dict, priority: int = 0) -> int | None:
    """Add a task to the worker queue. Returns the task id, or None if a
    matching pending/running task already exists (deduplication)."""
    with conn.cursor() as cur:
        # Skip if an identical pending or running task exists
        cur.execute(
            "SELECT id FROM worker_tasks "
            "WHERE project_id=%s AND field_name=%s AND model_id IS NOT DISTINCT FROM %s "
            "  AND kind=%s AND status IN ('pending', 'running') LIMIT 1",
            (project_id, field_name, model_id, kind),
        )
        if cur.fetchone():
            return None  # duplicate — already queued
        cur.execute(
            "INSERT INTO worker_tasks (project_id, field_name, model_id, kind, args_json, priority) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (project_id, field_name, model_id, kind, json.dumps(args), priority),
        )
        return cur.fetchone()["id"]


def claim_task(conn, worker_id: str) -> dict | None:
    """Atomically claim the highest-priority pending task. Returns None if queue is empty."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_tasks SET status='running', worker_id=%s, claimed_at=now() "
            "WHERE id = ("
            "  SELECT id FROM worker_tasks WHERE status='pending' "
            "  ORDER BY priority DESC, id ASC LIMIT 1 FOR UPDATE SKIP LOCKED"
            ") RETURNING *",
            (worker_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def finish_task(conn, task_id: int, status: str = "done", error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE worker_tasks SET status=%s, finished_at=now(), error=%s WHERE id=%s",
            (status, error, task_id),
        )


def pending_task_count(conn, project_id: int | None = None) -> int:
    with conn.cursor() as cur:
        if project_id:
            cur.execute(
                "SELECT COUNT(*) AS n FROM worker_tasks WHERE status='pending' AND project_id=%s",
                (project_id,),
            )
        else:
            cur.execute("SELECT COUNT(*) AS n FROM worker_tasks WHERE status='pending'")
        row = cur.fetchone()
        return row["n"] if row else 0
