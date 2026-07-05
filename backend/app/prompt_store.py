"""Thin helpers around the `prompt_versions` table: get-or-create the v1
baseline instruction for a field, and add a new version with lineage."""
from __future__ import annotations

import sqlite3

from . import db, prompts


def get_or_create_baseline(conn: sqlite3.Connection, project_id: int, field_name: str) -> sqlite3.Row | None:
    """Returns the current best ACCEPTED prompt version for this field (i.e.
    the one production extraction should use), creating v1 from
    `BASELINE_INSTRUCTIONS` if none exists yet. Deliberately ignores
    higher-numbered but rejected optimizer candidates — those still exist as
    permanent rows for provenance, but must never shadow the real best.
    """
    existing = db.best_accepted_prompt_version(conn, project_id, field_name)
    if existing:
        return existing
    instruction = prompts.BASELINE_INSTRUCTIONS[field_name]
    db.add_prompt_version(conn, project_id, field_name, version=1, template=instruction, parent_id=None, notes="baseline v1", accepted=1)
    return db.best_accepted_prompt_version(conn, project_id, field_name)


def add_version(
    conn: sqlite3.Connection, project_id: int, field_name: str, instruction: str, parent_id: int | None,
    notes: str | None, accepted: bool = False,
) -> sqlite3.Row | None:
    """Persists a new candidate instruction. `accepted` defaults to False
    (pending judgement) — callers that later decide whether the candidate
    beat its incumbent should call `db.set_prompt_version_accepted(...)`.
    Version numbers always increment off the absolute latest row (accepted or
    not) so every candidate — accepted or rejected — gets a unique, permanent
    slot in the lineage.
    """
    latest = db.latest_prompt_version(conn, project_id, field_name)
    next_version = (latest["version"] + 1) if latest else 1
    db.add_prompt_version(
        conn, project_id, field_name, version=next_version, template=instruction, parent_id=parent_id, notes=notes,
        accepted=int(accepted),
    )
    return conn.execute(
        "SELECT * FROM prompt_versions WHERE project_id = ? AND field_name = ? AND version = ?",
        (project_id, field_name, next_version),
    ).fetchone()
