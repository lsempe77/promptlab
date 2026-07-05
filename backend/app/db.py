"""SQLite schema + thin data-access layer for the prompt-lab backend.

Design intentionally avoids an ORM/task-queue (per project decision): plain
`sqlite3`, a handful of tables, and small helper functions. Good enough for an
experiment-tracking tool used by a handful of people locally.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# Overridable via DEP_DB_PATH (e.g. a mounted volume path in a cloud deploy) so
# the same code works locally and remotely without a fork.
DB_PATH = Path(os.environ.get("DEP_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "promptlab.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    title TEXT,
    md_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ground_truth (
    record_id INTEGER NOT NULL REFERENCES records(id),
    field_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (record_id, field_name)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field_name TEXT NOT NULL,
    version INTEGER NOT NULL,
    template TEXT NOT NULL,
    parent_id INTEGER REFERENCES prompt_versions(id),
    notes TEXT,
    accepted INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (field_name, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
    model_id TEXT NOT NULL,
    record_id INTEGER NOT NULL REFERENCES records(id),
    field_name TEXT NOT NULL,
    raw_response TEXT,
    parsed_value_json TEXT,
    score REAL,
    is_correct INTEGER,
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd REAL,
    error TEXT,
    batch_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field_name TEXT NOT NULL,
    iteration_num INTEGER NOT NULL,
    prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
    model_id TEXT NOT NULL,
    mean_score REAL,
    n_records INTEGER,
    feedback TEXT,
    accepted INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_judgments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    judge_model TEXT NOT NULL,
    verdict INTEGER NOT NULL,
    reasoning TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, judge_model)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    field_name TEXT NOT NULL,
    model_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    total INTEGER,
    completed INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_runs_field_model ON runs(field_name, model_id);
CREATE INDEX IF NOT EXISTS idx_ground_truth_field ON ground_truth(field_name);
CREATE INDEX IF NOT EXISTS idx_jobs_field_status ON jobs(field_name, status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_record(conn: sqlite3.Connection, id_: int, title: str | None, md_path: str) -> None:
    conn.execute(
        "INSERT INTO records (id, title, md_path) VALUES (?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, md_path=excluded.md_path",
        (id_, title, md_path),
    )


def upsert_ground_truth(conn: sqlite3.Connection, record_id: int, field_name: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO ground_truth (record_id, field_name, value_json) VALUES (?, ?, ?) "
        "ON CONFLICT(record_id, field_name) DO UPDATE SET value_json=excluded.value_json",
        (record_id, field_name, json.dumps(value, ensure_ascii=False)),
    )


def get_records_with_field(conn: sqlite3.Connection, field_name: str, limit: int | None = None) -> list[dict]:
    q = (
        "SELECT r.id, r.title, r.md_path, g.value_json FROM records r "
        "JOIN ground_truth g ON g.record_id = r.id "
        "WHERE g.field_name = ? ORDER BY r.id"
    )
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q, (field_name,)).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "md_path": r["md_path"], "ground_truth": json.loads(r["value_json"])}
        for r in rows
    ]


def add_prompt_version(
    conn: sqlite3.Connection, field_name: str, version: int, template: str, parent_id: int | None, notes: str | None,
    accepted: int = 1,
) -> int | None:
    cur = conn.execute(
        "INSERT INTO prompt_versions (field_name, version, template, parent_id, notes, accepted, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (field_name, version, template, parent_id, notes, accepted, now()),
    )
    return cur.lastrowid


def latest_prompt_version(conn: sqlite3.Connection, field_name: str) -> sqlite3.Row | None:
    """Absolute latest version by number, regardless of accept/reject status.
    Used only for version-numbering (avoiding UNIQUE clashes); NOT what
    production extraction should read from a candidate could be rejected and
    still be "latest". Use `best_accepted_prompt_version` for that.
    """
    return conn.execute(
        "SELECT * FROM prompt_versions WHERE field_name = ? ORDER BY version DESC LIMIT 1",
        (field_name,),
    ).fetchone()


def best_accepted_prompt_version(conn: sqlite3.Connection, field_name: str) -> sqlite3.Row | None:
    """Highest-version row for this field that was actually accepted (i.e. the
    baseline, or an optimizer candidate that beat its incumbent). This is what
    production extraction runs should use — rejected candidates still get a
    permanent row (for full provenance) but must never shadow the real best.
    """
    return conn.execute(
        "SELECT * FROM prompt_versions WHERE field_name = ? AND accepted = 1 ORDER BY version DESC LIMIT 1",
        (field_name,),
    ).fetchone()


def set_prompt_version_accepted(conn: sqlite3.Connection, prompt_version_id: int, accepted: bool) -> None:
    conn.execute(
        "UPDATE prompt_versions SET accepted = ? WHERE id = ?",
        (int(accepted), prompt_version_id),
    )


def add_run(conn: sqlite3.Connection, **kwargs: Any) -> int | None:
    kwargs.setdefault("created_at", now())
    if "parsed_value" in kwargs:
        kwargs["parsed_value_json"] = json.dumps(kwargs.pop("parsed_value"), ensure_ascii=False)
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    cur = conn.execute(f"INSERT INTO runs ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
    return cur.lastrowid


def add_iteration(conn: sqlite3.Connection, **kwargs: Any) -> int | None:
    kwargs.setdefault("created_at", now())
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    cur = conn.execute(f"INSERT INTO iterations ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
    return cur.lastrowid


def add_llm_judgment(
    conn: sqlite3.Connection, run_id: int, judge_model: str, verdict: bool, reasoning: str | None
) -> int | None:
    cur = conn.execute(
        "INSERT INTO llm_judgments (run_id, judge_model, verdict, reasoning, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, judge_model) DO UPDATE SET verdict=excluded.verdict, "
        "reasoning=excluded.reasoning, created_at=excluded.created_at",
        (run_id, judge_model, int(verdict), reasoning, now()),
    )
    return cur.lastrowid


def get_runs_without_judgment(
    conn: sqlite3.Connection, field_name: str, judge_model: str, limit: int | None = None
) -> list[sqlite3.Row]:
    """Runs for `field_name` that have a parsed value and no judgment yet from
    `judge_model` (so re-running the judge script is idempotent/resumable)."""
    q = (
        "SELECT r.* FROM runs r "
        "LEFT JOIN llm_judgments j ON j.run_id = r.id AND j.judge_model = ? "
        "WHERE r.field_name = ? AND r.parsed_value_json IS NOT NULL AND j.id IS NULL "
        "ORDER BY r.id"
    )
    params: list = [judge_model, field_name]
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    return conn.execute(q, params).fetchall()


def start_job(conn: sqlite3.Connection, field_name: str, model_id: str, kind: str, total: int | None = None) -> int | None:
    """Records that a `run_extraction`/`optimize_prompt` invocation has started
    working on (field_name, model_id), so the dashboard can show a "currently
    running" indicator. Callers should call `finish_job` when done (including
    on failure) so the job doesn't look stuck forever.
    """
    ts = now()
    cur = conn.execute(
        "INSERT INTO jobs (field_name, model_id, kind, status, total, completed, started_at, updated_at) "
        "VALUES (?, ?, ?, 'running', ?, 0, ?, ?)",
        (field_name, model_id, kind, total, ts, ts),
    )
    return cur.lastrowid


def update_job_progress(conn: sqlite3.Connection, job_id: int, completed: int) -> None:
    conn.execute("UPDATE jobs SET completed = ?, updated_at = ? WHERE id = ?", (completed, now(), job_id))


def finish_job(conn: sqlite3.Connection, job_id: int, status: str = "completed", error: str | None = None) -> None:
    ts = now()
    conn.execute(
        "UPDATE jobs SET status = ?, error = ?, updated_at = ?, finished_at = ? WHERE id = ?",
        (status, error, ts, ts, job_id),
    )


# A job whose last progress update is older than this is treated as abandoned
# (e.g. the script crashed or was killed without reaching `finish_job`) rather
# than genuinely still running.
JOB_STALE_AFTER_SECONDS = 300


def get_jobs_for_field(conn: sqlite3.Connection, field_name: str, limit: int = 20) -> list[sqlite3.Row]:
    """Most recent jobs for a field (running or finished), newest first. The
    API layer derives a `stale` flag from `updated_at` vs. `JOB_STALE_AFTER_SECONDS`
    rather than trusting `status` alone."""
    return conn.execute(
        "SELECT * FROM jobs WHERE field_name = ? ORDER BY started_at DESC LIMIT ?",
        (field_name, limit),
    ).fetchall()
