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
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    id INTEGER NOT NULL,
    title TEXT,
    md_path TEXT NOT NULL,
    PRIMARY KEY (project_id, id)
);

CREATE TABLE IF NOT EXISTS ground_truth (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    record_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (project_id, record_id, field_name),
    FOREIGN KEY (project_id, record_id) REFERENCES records(project_id, id)
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    field_name TEXT NOT NULL,
    model_id TEXT,
    version INTEGER NOT NULL,
    template TEXT NOT NULL,
    parent_id INTEGER REFERENCES prompt_versions(id),
    notes TEXT,
    accepted INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, field_name, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id),
    model_id TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    field_name TEXT NOT NULL,
    raw_response TEXT,
    parsed_value_json TEXT,
    excerpt TEXT,
    notes TEXT,
    score REAL,
    honesty_score REAL,
    is_correct INTEGER,
    outcome TEXT,
    logprob_confidence REAL,
    excerpt_verified INTEGER,
    confidence REAL,
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cost_usd REAL,
    co2e_grams REAL,
    error TEXT,
    batch_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, record_id) REFERENCES records(project_id, id)
);

CREATE TABLE IF NOT EXISTS iterations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
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
    project_id INTEGER NOT NULL REFERENCES projects(id),
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

-- Self-consistency validation study (opt-in, N samples per record at temp>0):
-- `agreement` = fraction of the N samples that landed in the modal answer
-- cluster; `modal_value_json` = that consensus value. One row per
-- (project, field, model, record).
CREATE TABLE IF NOT EXISTS self_consistency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    field_name TEXT NOT NULL,
    model_id TEXT NOT NULL,
    record_id INTEGER NOT NULL,
    n_samples INTEGER NOT NULL,
    agreement REAL NOT NULL,
    modal_value_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, field_name, model_id, record_id),
    FOREIGN KEY (project_id, record_id) REFERENCES records(project_id, id)
);
"""

# Indexes are created separately from SCHEMA (and only AFTER
# `_migrate_to_multi_project` has run) because they reference `project_id` —
# on a pre-multi-project DB that column doesn't exist yet at the time SCHEMA's
# `CREATE TABLE IF NOT EXISTS` statements are (no-op) applied, so creating
# these indexes in the same script would fail before the migration ever gets
# a chance to add the column.
SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_runs_project_field_model ON runs(project_id, field_name, model_id);
CREATE INDEX IF NOT EXISTS idx_ground_truth_project_field ON ground_truth(project_id, field_name);
CREATE INDEX IF NOT EXISTS idx_jobs_project_field_status ON jobs(project_id, field_name, status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # timeout=30: Python-native busy-wait — more reliable than PRAGMA for
    # multi-process concurrent writers (--parallelism > 1).
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL mode: concurrent readers + serialised writers, no reader blocking.
    conn.execute("PRAGMA journal_mode=WAL")
    # Belt-and-suspenders: also set at the SQLite C-library level.
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent, additive migrations for DBs created before a column existed
    (CREATE TABLE IF NOT EXISTS never alters an already-existing table). The
    production DB lives on a persisted volume, so missing columns are added in
    place rather than requiring a wipe/rebuild."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    for col, coltype in (("excerpt", "TEXT"), ("notes", "TEXT"), ("outcome", "TEXT"),
                         ("honesty_score", "REAL"), ("logprob_confidence", "REAL"),
                         ("excerpt_verified", "INTEGER"), ("confidence", "REAL"),
                         ("co2e_grams", "REAL")):
        if col not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {coltype}")

    # Per-model prompts: prompt_versions gains an owner model_id (NULL = the
    # original shared/field-level baseline). Additive, so existing shared
    # baselines and their runs stay valid.
    pv_cols = {row["name"] for row in conn.execute("PRAGMA table_info(prompt_versions)")}
    if "model_id" not in pv_cols:
        conn.execute("ALTER TABLE prompt_versions ADD COLUMN model_id TEXT")


def _needs_multi_project_migration(conn: sqlite3.Connection) -> bool:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
    return "project_id" not in cols


def _migrate_to_multi_project(conn: sqlite3.Connection) -> None:
    """One-time migration for DBs created before multi-project support: adds
    `project_id` (composite-keyed where relevant) to every table that used to
    assume a single implicit project, backfilling all existing rows into a
    default 'dep-extraction' project (id=1). No-op once `records.project_id`
    already exists (including on a brand-new DB, whose tables are created
    with `project_id` from the SCHEMA above already)."""
    if not _needs_multi_project_migration(conn):
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT OR IGNORE INTO projects (id, slug, name, description, created_at) "
        "VALUES (1, 'dep-extraction', 'DEP \u2014 Data Extraction', "
        "'Migrated from the original single-project setup.', ?)",
        (now(),),
    )

    conn.execute("ALTER TABLE records RENAME TO records_old")
    conn.execute(
        "CREATE TABLE records (project_id INTEGER NOT NULL REFERENCES projects(id), "
        "id INTEGER NOT NULL, title TEXT, md_path TEXT NOT NULL, PRIMARY KEY (project_id, id))"
    )
    conn.execute("INSERT INTO records (project_id, id, title, md_path) SELECT 1, id, title, md_path FROM records_old")
    conn.execute("DROP TABLE records_old")

    conn.execute("ALTER TABLE ground_truth RENAME TO ground_truth_old")
    conn.execute(
        "CREATE TABLE ground_truth (project_id INTEGER NOT NULL REFERENCES projects(id), "
        "record_id INTEGER NOT NULL, field_name TEXT NOT NULL, value_json TEXT NOT NULL, "
        "PRIMARY KEY (project_id, record_id, field_name), "
        "FOREIGN KEY (project_id, record_id) REFERENCES records(project_id, id))"
    )
    conn.execute(
        "INSERT INTO ground_truth (project_id, record_id, field_name, value_json) "
        "SELECT 1, record_id, field_name, value_json FROM ground_truth_old"
    )
    conn.execute("DROP TABLE ground_truth_old")

    conn.execute("ALTER TABLE prompt_versions RENAME TO prompt_versions_old")
    conn.execute(
        "CREATE TABLE prompt_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_id INTEGER NOT NULL REFERENCES projects(id), field_name TEXT NOT NULL, "
        "version INTEGER NOT NULL, template TEXT NOT NULL, "
        "parent_id INTEGER REFERENCES prompt_versions(id), notes TEXT, "
        "accepted INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, "
        "UNIQUE (project_id, field_name, version))"
    )
    conn.execute(
        "INSERT INTO prompt_versions (id, project_id, field_name, version, template, parent_id, "
        "notes, accepted, created_at) "
        "SELECT id, 1, field_name, version, template, parent_id, notes, accepted, created_at "
        "FROM prompt_versions_old"
    )
    conn.execute("DROP TABLE prompt_versions_old")

    conn.execute("ALTER TABLE runs RENAME TO runs_old")
    conn.execute(
        "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_id INTEGER NOT NULL REFERENCES projects(id), "
        "prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id), model_id TEXT NOT NULL, "
        "record_id INTEGER NOT NULL, field_name TEXT NOT NULL, raw_response TEXT, "
        "parsed_value_json TEXT, score REAL, is_correct INTEGER, latency_ms INTEGER, "
        "prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL, error TEXT, "
        "batch_id TEXT, created_at TEXT NOT NULL, "
        "FOREIGN KEY (project_id, record_id) REFERENCES records(project_id, id))"
    )
    conn.execute(
        "INSERT INTO runs (id, project_id, prompt_version_id, model_id, record_id, field_name, "
        "raw_response, parsed_value_json, score, is_correct, latency_ms, prompt_tokens, "
        "completion_tokens, cost_usd, error, batch_id, created_at) "
        "SELECT id, 1, prompt_version_id, model_id, record_id, field_name, raw_response, "
        "parsed_value_json, score, is_correct, latency_ms, prompt_tokens, completion_tokens, "
        "cost_usd, error, batch_id, created_at FROM runs_old"
    )
    conn.execute("DROP TABLE runs_old")

    conn.execute("ALTER TABLE iterations RENAME TO iterations_old")
    conn.execute(
        "CREATE TABLE iterations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_id INTEGER NOT NULL REFERENCES projects(id), field_name TEXT NOT NULL, "
        "iteration_num INTEGER NOT NULL, prompt_version_id INTEGER NOT NULL REFERENCES prompt_versions(id), "
        "model_id TEXT NOT NULL, mean_score REAL, n_records INTEGER, feedback TEXT, accepted INTEGER, "
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO iterations (id, project_id, field_name, iteration_num, prompt_version_id, "
        "model_id, mean_score, n_records, feedback, accepted, created_at) "
        "SELECT id, 1, field_name, iteration_num, prompt_version_id, model_id, mean_score, "
        "n_records, feedback, accepted, created_at FROM iterations_old"
    )
    conn.execute("DROP TABLE iterations_old")

    conn.execute("ALTER TABLE jobs RENAME TO jobs_old")
    conn.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "project_id INTEGER NOT NULL REFERENCES projects(id), field_name TEXT NOT NULL, "
        "model_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running', "
        "total INTEGER, completed INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, finished_at TEXT, error TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs (id, project_id, field_name, model_id, kind, status, total, completed, "
        "started_at, updated_at, finished_at, error) "
        "SELECT id, 1, field_name, model_id, kind, status, total, completed, started_at, "
        "updated_at, finished_at, error FROM jobs_old"
    )
    conn.execute("DROP TABLE jobs_old")

    conn.execute("PRAGMA foreign_keys = ON")


def sync_projects(conn: sqlite3.Connection) -> None:
    """Upsert every project in `app.projects.PROJECTS` into the `projects`
    table (by slug), so new projects just need a code change + restart, no
    manual DB setup. Called on every `init_db`."""
    from .projects import PROJECTS

    for spec in PROJECTS.values():
        conn.execute(
            "INSERT INTO projects (slug, name, description, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, description=excluded.description",
            (spec.slug, spec.name, spec.description, now()),
        )


def get_project_id(conn: sqlite3.Connection, slug: str) -> int:
    row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown project slug: {slug!r} (not seeded \u2014 check app.projects.PROJECTS)")
    return row["id"]


def list_projects(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM projects ORDER BY id").fetchall()


def init_db(db_path: Path = DB_PATH) -> None:
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
        _migrate_to_multi_project(conn)
        _migrate(conn)
        conn.executescript(SCHEMA_INDEXES)
        sync_projects(conn)


def upsert_record(conn: sqlite3.Connection, project_id: int, id_: int, title: str | None, md_path: str) -> None:
    conn.execute(
        "INSERT INTO records (project_id, id, title, md_path) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id, id) DO UPDATE SET title=excluded.title, md_path=excluded.md_path",
        (project_id, id_, title, md_path),
    )


def upsert_ground_truth(conn: sqlite3.Connection, project_id: int, record_id: int, field_name: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO ground_truth (project_id, record_id, field_name, value_json) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(project_id, record_id, field_name) DO UPDATE SET value_json=excluded.value_json",
        (project_id, record_id, field_name, json.dumps(value, ensure_ascii=False)),
    )


def get_records_with_field(
    conn: sqlite3.Connection, project_id: int, field_name: str, limit: int | None = None
) -> list[dict]:
    q = (
        "SELECT r.id, r.title, r.md_path, g.value_json FROM records r "
        "JOIN ground_truth g ON g.project_id = r.project_id AND g.record_id = r.id "
        "WHERE r.project_id = ? AND g.field_name = ? ORDER BY r.id"
    )
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = conn.execute(q, (project_id, field_name)).fetchall()
    return [
        {"id": r["id"], "title": r["title"], "md_path": r["md_path"], "ground_truth": json.loads(r["value_json"])}
        for r in rows
    ]


def add_prompt_version(
    conn: sqlite3.Connection, project_id: int, field_name: str, version: int, template: str,
    parent_id: int | None, notes: str | None, accepted: int = 1, model_id: str | None = None,
) -> int | None:
    cur = conn.execute(
        "INSERT INTO prompt_versions (project_id, field_name, model_id, version, template, parent_id, notes, "
        "accepted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project_id, field_name, model_id, version, template, parent_id, notes, accepted, now()),
    )
    return cur.lastrowid


def latest_prompt_version(conn: sqlite3.Connection, project_id: int, field_name: str) -> sqlite3.Row | None:
    """Absolute latest version by number, regardless of accept/reject status.
    Used only for version-numbering (avoiding UNIQUE clashes); NOT what
    production extraction should read from a candidate could be rejected and
    still be "latest". Use `best_accepted_prompt_version` for that.
    """
    return conn.execute(
        "SELECT * FROM prompt_versions WHERE project_id = ? AND field_name = ? ORDER BY version DESC LIMIT 1",
        (project_id, field_name),
    ).fetchone()


def best_accepted_prompt_version(
    conn: sqlite3.Connection, project_id: int, field_name: str, model_id: str | None = None
) -> sqlite3.Row | None:
    """Highest-version ACCEPTED row for this field, scoped to a model when
    `model_id` is given (per-model prompts), else the shared/field-level
    baseline (model_id IS NULL). This is what production extraction should use;
    rejected candidates still get a permanent row but must never shadow the best.
    """
    if model_id is None:
        return conn.execute(
            "SELECT * FROM prompt_versions WHERE project_id = ? AND field_name = ? AND model_id IS NULL "
            "AND accepted = 1 ORDER BY version DESC LIMIT 1",
            (project_id, field_name),
        ).fetchone()
    return conn.execute(
        "SELECT * FROM prompt_versions WHERE project_id = ? AND field_name = ? AND model_id = ? "
        "AND accepted = 1 ORDER BY version DESC LIMIT 1",
        (project_id, field_name, model_id),
    ).fetchone()


def set_prompt_version_accepted(conn: sqlite3.Connection, prompt_version_id: int, accepted: bool) -> None:
    conn.execute(
        "UPDATE prompt_versions SET accepted = ? WHERE id = ?",
        (int(accepted), prompt_version_id),
    )


def add_run(conn: sqlite3.Connection, **kwargs: Any) -> int | None:
    """Caller must include `project_id` in kwargs (all data-writing scripts
    resolve it once via `get_project_id` at startup)."""
    kwargs.setdefault("created_at", now())
    if "parsed_value" in kwargs:
        kwargs["parsed_value_json"] = json.dumps(kwargs.pop("parsed_value"), ensure_ascii=False)
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    cur = conn.execute(f"INSERT INTO runs ({cols}) VALUES ({placeholders})", tuple(kwargs.values()))
    return cur.lastrowid


def add_iteration(conn: sqlite3.Connection, **kwargs: Any) -> int | None:
    """Caller must include `project_id` in kwargs (see `add_run`)."""
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


def add_self_consistency(
    conn: sqlite3.Connection, project_id: int, field_name: str, model_id: str, record_id: int,
    n_samples: int, agreement: float, modal_value: Any,
) -> int | None:
    cur = conn.execute(
        "INSERT INTO self_consistency (project_id, field_name, model_id, record_id, n_samples, "
        "agreement, modal_value_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(project_id, field_name, model_id, record_id) DO UPDATE SET "
        "n_samples=excluded.n_samples, agreement=excluded.agreement, "
        "modal_value_json=excluded.modal_value_json, created_at=excluded.created_at",
        (project_id, field_name, model_id, record_id, n_samples, agreement,
         json.dumps(modal_value, ensure_ascii=False), now()),
    )
    return cur.lastrowid


def get_runs_without_judgment(
    conn: sqlite3.Connection, project_id: int, field_name: str, judge_model: str, limit: int | None = None
) -> list[sqlite3.Row]:
    """Runs for `field_name` that have a parsed value and no judgment yet from
    `judge_model` (so re-running the judge script is idempotent/resumable)."""
    q = (
        "SELECT r.* FROM runs r "
        "LEFT JOIN llm_judgments j ON j.run_id = r.id AND j.judge_model = ? "
        "WHERE r.project_id = ? AND r.field_name = ? AND r.parsed_value_json IS NOT NULL AND j.id IS NULL "
        "ORDER BY r.id"
    )
    params: list = [judge_model, project_id, field_name]
    if limit:
        q += " LIMIT ?"
        params.append(limit)
    return conn.execute(q, params).fetchall()


def start_job(
    conn: sqlite3.Connection, project_id: int, field_name: str, model_id: str, kind: str,
    total: int | None = None,
) -> int | None:
    """Records that a `run_extraction`/`optimize_prompt` invocation has started
    working on (field_name, model_id), so the dashboard can show a "currently
    running" indicator. Callers should call `finish_job` when done (including
    on failure) so the job doesn't look stuck forever.
    """
    ts = now()
    cur = conn.execute(
        "INSERT INTO jobs (project_id, field_name, model_id, kind, status, total, completed, "
        "started_at, updated_at) VALUES (?, ?, ?, ?, 'running', ?, 0, ?, ?)",
        (project_id, field_name, model_id, kind, total, ts, ts),
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


def get_jobs_for_field(conn: sqlite3.Connection, project_id: int, field_name: str, limit: int = 20) -> list[sqlite3.Row]:
    """Most recent jobs for a field (running or finished), newest first. The
    API layer derives a `stale` flag from `updated_at` vs. `JOB_STALE_AFTER_SECONDS`
    rather than trusting `status` alone."""
    return conn.execute(
        "SELECT * FROM jobs WHERE project_id = ? AND field_name = ? ORDER BY started_at DESC LIMIT ?",
        (project_id, field_name, limit),
    ).fetchall()

