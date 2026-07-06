"""Thin helpers around the `prompt_versions` table: get-or-create the v1
baseline instruction for a field, and add a new version with lineage."""
from __future__ import annotations

import sqlite3

from . import db, prompts


def get_or_create_baseline(
    conn: sqlite3.Connection, project_id: int, field_name: str, model_id: str | None = None
) -> sqlite3.Row | None:
    """Current best ACCEPTED prompt for this field. When `model_id` is given
    (per-model prompts) it returns that model's own accepted prompt if it has
    diverged, otherwise it FALLS BACK to the shared/field-level baseline -- so a
    model keeps using the shared prompt (and all its existing shared-prompt runs
    stay valid) until its own optimizer accepts a rewrite. The shared baseline is
    created from `BASELINE_INSTRUCTIONS` if missing. Rejected candidates never
    shadow the best.
    """
    if model_id is not None:
        per_model = db.best_accepted_prompt_version(conn, project_id, field_name, model_id)
        if per_model:
            return per_model
        # not diverged yet -> use the shared baseline
        return get_or_create_baseline(conn, project_id, field_name, None)

    existing = db.best_accepted_prompt_version(conn, project_id, field_name, None)
    if existing:
        return existing
    latest = db.latest_prompt_version(conn, project_id, field_name)
    next_version = (latest["version"] + 1) if latest else 1
    db.add_prompt_version(
        conn, project_id, field_name, version=next_version, template=prompts.BASELINE_INSTRUCTIONS[field_name],
        parent_id=None, notes="baseline v1", accepted=1, model_id=None,
    )
    return db.best_accepted_prompt_version(conn, project_id, field_name, None)


def add_version(
    conn: sqlite3.Connection, project_id: int, field_name: str, instruction: str, parent_id: int | None,
    notes: str | None, accepted: bool = False, model_id: str | None = None,
) -> sqlite3.Row | None:
    """Persists a new candidate instruction (optionally owned by `model_id` for
    per-model prompts). `accepted` defaults to False (pending judgement) —
    callers that later decide whether the candidate beat its incumbent should
    call `db.set_prompt_version_accepted(...)`. Version numbers always increment
    off the absolute latest row for the field (accepted or not) so every
    candidate gets a unique, permanent slot in the lineage.
    """
    latest = db.latest_prompt_version(conn, project_id, field_name)
    next_version = (latest["version"] + 1) if latest else 1
    db.add_prompt_version(
        conn, project_id, field_name, version=next_version, template=instruction, parent_id=parent_id, notes=notes,
        accepted=int(accepted), model_id=model_id,
    )
    return conn.execute(
        "SELECT * FROM prompt_versions WHERE project_id = ? AND field_name = ? AND version = ?",
        (project_id, field_name, next_version),
    ).fetchone()
