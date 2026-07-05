"""Read-only FastAPI layer over promptlab.db, for the observability frontend.

Runs locally (this is a single-user local tool, same as the rest of the
backend — no auth, no task queue). CORS is opened up for the Vite dev server
and the deployed GitHub Pages frontend so the dashboard can hit this API
while it's running on the developer's own machine.

Run with:
    uvicorn backend.app.api:app --reload --port 8000
or:
    python -m backend.scripts.serve
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import analytics, db, scoring
from .fields import FIELDS

app = FastAPI(title="3ie DEP Prompt Lab API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://sempe.dev",
        "https://lsempe77.github.io",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _field_or_404(field_name: str) -> None:
    if field_name not in FIELDS:
        raise HTTPException(status_code=404, detail=f"Unknown field: {field_name!r}")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/fields")
def list_fields() -> list[dict]:
    return [
        {
            "name": spec.name,
            "label": spec.label,
            "value_type": spec.value_type,
            "taxonomy_key": spec.taxonomy_key,
            "description": spec.description,
        }
        for spec in FIELDS.values()
    ]


@app.get("/api/fields/{field_name}/prompt-versions")
def prompt_versions(field_name: str) -> list[dict]:
    _field_or_404(field_name)
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, field_name, version, template, parent_id, notes, accepted, created_at "
            "FROM prompt_versions WHERE field_name = ? ORDER BY version",
            (field_name,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/fields/{field_name}/models-summary")
def models_summary(field_name: str) -> list[dict]:
    _field_or_404(field_name)
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                model_id,
                COUNT(*) AS n,
                AVG(score) AS mean_score,
                SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS n_correct,
                SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS n_errors,
                AVG(latency_ms) AS mean_latency_ms,
                SUM(cost_usd) AS total_cost_usd
            FROM runs
            WHERE field_name = ?
            GROUP BY model_id
            ORDER BY mean_score DESC
            """,
            (field_name,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        n = d["n"] or 0
        d["accuracy"] = (d.pop("n_correct") / n) if n else 0.0
        result.append(d)
    return result


@app.get("/api/fields/{field_name}/llm-judge-summary")
def llm_judge_summary(field_name: str) -> list[dict]:
    """Per-model LLM-as-judge verdict summary: a posterior semantic
    true/false judgment per run (see scripts/llm_judge.py), independent of
    the automated string-matching scorer used for `models-summary.accuracy`.
    Only includes models that have at least one judged run."""
    _field_or_404(field_name)
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                r.model_id,
                COUNT(*) AS n_judged,
                SUM(CASE WHEN j.verdict = 1 THEN 1 ELSE 0 END) AS n_correct
            FROM llm_judgments j
            JOIN runs r ON r.id = j.run_id
            WHERE r.field_name = ?
            GROUP BY r.model_id
            """,
            (field_name,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        n = d["n_judged"] or 0
        d["llm_judged_accuracy"] = (d.pop("n_correct") / n) if n else 0.0
        result.append(d)
    return result



@app.get("/api/fields/{field_name}/iterations")
def iterations(field_name: str, model_id: str | None = None) -> list[dict]:
    """Optimizer iteration log for a field, optionally scoped to one model
    (each iteration is always tied to exactly one "student" model being
    optimized \u2014 see optimizer.optimize_field). Enriched with the candidate
    prompt version's number/template/notes so the frontend can render a
    per-model progress chart + prompt lineage from a single call.
    """
    _field_or_404(field_name)
    q = (
        "SELECT it.id, it.field_name, it.iteration_num, it.prompt_version_id, it.model_id, "
        "it.mean_score, it.n_records, it.feedback, it.accepted, it.created_at, "
        "pv.version AS prompt_version, pv.template AS prompt_template, pv.notes AS prompt_notes "
        "FROM iterations it JOIN prompt_versions pv ON pv.id = it.prompt_version_id "
        "WHERE it.field_name = ?"
    )
    params: list = [field_name]
    if model_id:
        q += " AND it.model_id = ?"
        params.append(model_id)
    q += " ORDER BY it.iteration_num"
    with db.get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/fields/{field_name}/runs")
def runs(field_name: str, model_id: str | None = None, limit: int = 200) -> list[dict]:
    _field_or_404(field_name)
    q = "SELECT * FROM runs WHERE field_name = ?"
    params: list = [field_name]
    if model_id:
        q += " AND model_id = ?"
        params.append(model_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with db.get_conn() as conn:
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/fields/{field_name}/confusion")
def confusion(field_name: str, model_id: str | None = None) -> dict:
    """Confusion matrix (categorical fields) or micro precision/recall/F1/F2
    (list fields), computed live from logged runs + ground truth."""
    _field_or_404(field_name)
    q = (
        "SELECT r.parsed_value_json, g.value_json FROM runs r "
        "JOIN ground_truth g ON g.record_id = r.record_id AND g.field_name = r.field_name "
        "WHERE r.field_name = ? AND r.parsed_value_json IS NOT NULL"
    )
    params: list = [field_name]
    if model_id:
        q += " AND r.model_id = ?"
        params.append(model_id)
    with db.get_conn() as conn:
        db_rows = conn.execute(q, params).fetchall()
    rows = [
        {"predicted": json.loads(r["parsed_value_json"]), "truth": json.loads(r["value_json"])}
        for r in db_rows
    ]
    return analytics.compute_confusion(field_name, rows)


@app.get("/api/fields/{field_name}/jobs")
def jobs(field_name: str) -> list[dict]:
    """Recent extraction/optimization jobs for a field, so the dashboard can
    show a "currently running" indicator. A job's `status` column is set by
    the script that owns it, but a script can die (crash, Ctrl+C, killed
    terminal) without ever calling `finish_job` \u2014 so a "running" job whose
    `updated_at` is older than `db.JOB_STALE_AFTER_SECONDS` is reported here
    with `stale: true` instead of being trusted at face value.
    """
    _field_or_404(field_name)
    with db.get_conn() as conn:
        rows = db.get_jobs_for_field(conn, field_name)
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        d = dict(r)
        stale = False
        if d["status"] == "running":
            age_s = (now - datetime.fromisoformat(d["updated_at"])).total_seconds()
            stale = age_s > db.JOB_STALE_AFTER_SECONDS
        d["stale"] = stale
        result.append(d)
    return result


@app.get("/api/config/thresholds")
def thresholds() -> dict:
    from .optimizer import IMPROVEMENT_EPSILON

    return {
        "correct_threshold": scoring.CORRECT_THRESHOLD,
        "fuzzy_match_threshold": scoring.FUZZY_MATCH_THRESHOLD,
        "improvement_epsilon": IMPROVEMENT_EPSILON,
    }
