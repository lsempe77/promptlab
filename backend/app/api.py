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
import os
import secrets
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import analytics, config, db, scoring
from .projects import PROJECTS

app = FastAPI(title="Agentic 3ie Prompt Lab API")

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


def _project_or_404(project_slug: str):
    if project_slug not in PROJECTS:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_slug!r}")
    return PROJECTS[project_slug]


# ── Auth ─────────────────────────────────────────────────────────────────────
# Simple org-domain + shared-password gate for write operations (project
# creation). Read endpoints remain public. The shared password is stored as
# the PROMPTLAB_PASSWORD env var on Fly (never committed to source).
_ALLOWED_DOMAIN = "@3ieimpact.org"
_TOKENS: set[str] = set()  # in-memory; clears on restart (by design for a single-machine tool)


class AuthRequest(BaseModel):
    email: str
    password: str


@app.post("/api/auth/token")
def auth_token(body: AuthRequest):
    """Issue a short-lived opaque token for a verified @3ieimpact.org email + shared password."""
    if not body.email.lower().endswith(_ALLOWED_DOMAIN):
        raise HTTPException(status_code=401, detail="Email must be a @3ieimpact.org address.")
    env_password = os.environ.get("PROMPTLAB_PASSWORD", "")
    if not env_password:
        raise HTTPException(status_code=503, detail="PROMPTLAB_PASSWORD env var not set on this server.")
    if not secrets.compare_digest(body.password, env_password):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    token = secrets.token_urlsafe(32)
    _TOKENS.add(token)
    return {"token": token}


def _require_auth(request_headers) -> None:
    """Dependency helper for write endpoints: validates Bearer token."""
    auth = request_headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    token = auth.removeprefix("Bearer ").strip()
    if token not in _TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or expired token. Please sign in again.")


def _field_or_404(project_slug: str, field_name: str) -> None:
    project = _project_or_404(project_slug)
    if field_name not in project.fields:
        raise HTTPException(status_code=404, detail=f"Unknown field: {field_name!r} in project {project_slug!r}")


def _resolve_pvid(conn, project_id: int, field_name: str, requested_version: int | None) -> int | None:
    """Resolve a specific requested version to its DB id.
    Returns -1 for unknown versions so the caller yields an empty result."""
    if requested_version is None:
        return None
    row = conn.execute(
        "SELECT id FROM prompt_versions WHERE project_id = ? AND field_name = ? AND version = ?",
        (project_id, field_name, requested_version),
    ).fetchone()
    return row["id"] if row else -1


def _best_pvids_per_model(conn, project_id: int, field_name: str) -> dict[str, int]:
    """Return {model_id: best_pvid} so the dashboard can show every model at
    its own best prompt version (not all forced to one shared version).

    Best = latest accepted version that has runs; fallback = version with the
    most runs. This is the correct default for per-model prompt lineages."""
    rows = conn.execute(
        "SELECT r.model_id, r.prompt_version_id, pv.accepted, pv.version, COUNT(*) AS n_runs "
        "FROM runs r JOIN prompt_versions pv ON pv.id = r.prompt_version_id "
        "WHERE r.project_id = ? AND r.field_name = ? AND r.prompt_version_id IS NOT NULL "
        "GROUP BY r.model_id, r.prompt_version_id",
        (project_id, field_name),
    ).fetchall()
    model_candidates: dict[str, list[dict]] = {}
    for row in rows:
        model_candidates.setdefault(row["model_id"], []).append(dict(row))
    result: dict[str, int] = {}
    for mid, candidates in model_candidates.items():
        accepted = [c for c in candidates if c["accepted"]]
        best = (max(accepted, key=lambda c: c["version"]) if accepted
                else max(candidates, key=lambda c: (c["n_runs"], c["version"])))
        result[mid] = best["prompt_version_id"]
    return result


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/projects")
def list_projects() -> list[dict]:
    static = [
        {"slug": spec.slug, "name": spec.name, "description": spec.description}
        for spec in PROJECTS.values()
    ]
    # Also include any projects created via the wizard (stored in DB)
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT slug, name, description FROM projects WHERE slug NOT IN ({})".format(
                    ",".join("?" * len(PROJECTS))
                ),
                list(PROJECTS.keys()),
            ).fetchall()
        db_projects = [{"slug": r["slug"], "name": r["name"], "description": r["description"] or ""} for r in rows]
    except Exception:
        db_projects = []
    return static + db_projects


@app.get("/api/projects/{project_slug}/fields")
def list_fields(project_slug: str) -> list[dict]:
    project = _project_or_404(project_slug)
    return [
        {
            "name": spec.name,
            "label": spec.label,
            "value_type": spec.value_type,
            "taxonomy_key": spec.taxonomy_key,
            "description": spec.description,
        }
        for spec in project.fields.values()
    ]


@app.get("/api/projects/{project_slug}/fields/{field_name}/prompt-versions")
def prompt_versions(project_slug: str, field_name: str) -> list[dict]:
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        rows = conn.execute(
            "SELECT id, field_name, version, template, parent_id, notes, accepted, created_at "
            "FROM prompt_versions WHERE project_id = ? AND field_name = ? ORDER BY version",
            (project_id, field_name),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_slug}/fields/{field_name}/run-versions")
def run_versions(project_slug: str, field_name: str) -> list[dict]:
    """Prompt versions that actually have logged runs for this field, newest
    first, so the dashboard's version selector only offers versions with data.
    The first entry is the default the metric endpoints use."""
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        rows = conn.execute(
            "SELECT pv.version AS version, pv.accepted AS accepted, COUNT(*) AS n_runs, "
            "COUNT(DISTINCT r.model_id) AS n_models "
            "FROM runs r JOIN prompt_versions pv ON pv.id = r.prompt_version_id "
            "WHERE r.project_id = ? AND r.field_name = ? "
            "GROUP BY pv.version, pv.accepted ORDER BY pv.version DESC",
            (project_id, field_name),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_slug}/fields/{field_name}/models-summary")
def models_summary(project_slug: str, field_name: str, prompt_version: int | None = None) -> list[dict]:
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)

        if prompt_version is None:
            # Per-model best version: each model shown at its own latest accepted
            # (or most-run fallback) prompt version, so all models are visible.
            model_pvids = _best_pvids_per_model(conn, project_id, field_name)
            if not model_pvids:
                return []
            or_clauses = " OR ".join(
                "(runs.model_id = ? AND runs.prompt_version_id = ?)" for _ in model_pvids
            )
            or_params = [p for mid, pvid in model_pvids.items() for p in (mid, pvid)]
            version_filter = f"AND ({or_clauses})"
            query_params = (project_id, field_name) + tuple(or_params)
        else:
            pvid = _resolve_pvid(conn, project_id, field_name, prompt_version)
            version_filter = "AND runs.prompt_version_id = ?"
            query_params = (project_id, field_name, pvid)

        rows = conn.execute(
            f"""
            SELECT
                runs.model_id AS model_id,
                COUNT(*) AS n,
                AVG(score) AS mean_score,
                AVG(honesty_score) AS mean_honesty_score,
                AVG(logprob_confidence) AS mean_logprob_confidence,
                SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS n_correct,
                SUM(CASE WHEN outcome = 'abstain_miss' THEN 1 ELSE 0 END) AS n_abstain,
                SUM(CASE WHEN outcome = 'hallucination' THEN 1 ELSE 0 END) AS n_hallucination,
                SUM(CASE WHEN outcome = 'wrong' THEN 1 ELSE 0 END) AS n_wrong,
                SUM(CASE WHEN excerpt_verified = 1 THEN 1 ELSE 0 END) AS n_excerpt_verified,
                SUM(CASE WHEN excerpt_verified IS NOT NULL THEN 1 ELSE 0 END) AS n_excerpt_cited,
                SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS n_errors,
                AVG(latency_ms) AS mean_latency_ms,
                SUM(cost_usd) AS total_cost_usd,
                SUM(co2e_grams) AS total_co2e_grams,
                MAX(pv.version) AS prompt_version
            FROM runs
            LEFT JOIN prompt_versions pv ON pv.id = runs.prompt_version_id
            WHERE runs.project_id = ? AND runs.field_name = ? {version_filter}
            GROUP BY runs.model_id
            ORDER BY mean_score DESC
            """,
            query_params,
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        n = d["n"] or 0
        d["accuracy"] = (d.pop("n_correct") / n) if n else 0.0
        # Rates are over all runs for the model (same denominator as accuracy);
        # error rows have a NULL outcome and so fall into none of these buckets.
        d["abstention_rate"] = (d.pop("n_abstain") / n) if n else 0.0
        d["hallucination_rate"] = (d.pop("n_hallucination") / n) if n else 0.0
        d["wrong_rate"] = (d.pop("n_wrong") / n) if n else 0.0
        # Of the answers that cited an excerpt, the share whose excerpt was
        # actually found in the source text (None if none cited one -- e.g. an
        # extraction run done before excerpt-verification existed).
        n_cited = d.pop("n_excerpt_cited") or 0
        d["excerpt_verified_rate"] = (d.pop("n_excerpt_verified") / n_cited) if n_cited else None
        result.append(d)
    return result


@app.get("/api/projects/{project_slug}/fields/{field_name}/llm-judge-summary")
def llm_judge_summary(project_slug: str, field_name: str, prompt_version: int | None = None) -> list[dict]:
    """Per-model LLM-as-judge verdict summary: a posterior semantic
    true/false judgment per run (see scripts/llm_judge.py), independent of
    the automated string-matching scorer used for `models-summary.accuracy`.
    Only includes models that have at least one judged run."""
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        pvid = _resolve_pvid(conn, project_id, field_name, prompt_version)
        rows = conn.execute(
            """
            SELECT
                r.model_id,
                COUNT(*) AS n_judged,
                SUM(CASE WHEN j.verdict = 1 THEN 1 ELSE 0 END) AS n_correct
            FROM llm_judgments j
            JOIN runs r ON r.id = j.run_id
            WHERE r.project_id = ? AND r.field_name = ? AND r.prompt_version_id = ?
            GROUP BY r.model_id
            """,
            (project_id, field_name, pvid),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        n = d["n_judged"] or 0
        d["llm_judged_accuracy"] = (d.pop("n_correct") / n) if n else 0.0
        result.append(d)
    return result


@app.get("/api/projects/{project_slug}/fields/{field_name}/cross-model-agreement")
def cross_model_agreement(project_slug: str, field_name: str, prompt_version: int | None = None) -> list[dict]:
    """Per-model cross-model agreement: for each record, how often this model's
    extracted value agrees with the OTHER models that answered the same record
    (agreement = scorer score >= CORRECT_THRESHOLD, so semantically-equal values
    count as agreeing). A model whose answers are usually backed by the pack is a
    higher-confidence signal than a lone dissenter. Computed from existing runs —
    no extra API calls. Uses the latest run per (record, model)."""
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        pvid = _resolve_pvid(conn, project_id, field_name, prompt_version)
        rows = conn.execute(
            "SELECT record_id, model_id, parsed_value_json, created_at FROM runs "
            "WHERE project_id = ? AND field_name = ? AND prompt_version_id = ? "
            "AND parsed_value_json IS NOT NULL AND error IS NULL "
            "ORDER BY created_at",
            (project_id, field_name, pvid),
        ).fetchall()

    # latest parsed value per (record, model)
    latest: dict[tuple[int, str], object] = {}
    for r in rows:
        latest[(r["record_id"], r["model_id"])] = json.loads(r["parsed_value_json"])

    by_record: dict[int, dict[str, object]] = defaultdict(dict)
    for (rid, mid), val in latest.items():
        by_record[rid][mid] = val

    agree_sum: dict[str, float] = defaultdict(float)
    agree_n: dict[str, int] = defaultdict(int)
    for model_vals in by_record.values():
        models = list(model_vals.keys())
        if len(models) < 2:
            continue  # need at least one peer to agree/disagree with
        for m in models:
            others = [o for o in models if o != m]
            agreed = sum(
                1 for o in others
                if scoring.score_field(field_name, model_vals[m], model_vals[o]).score
                >= scoring.CORRECT_THRESHOLD
            )
            agree_sum[m] += agreed / len(others)
            agree_n[m] += 1

    result = [
        {"model_id": m, "n_records": agree_n[m], "agreement_rate": agree_sum[m] / agree_n[m]}
        for m in agree_n
    ]
    result.sort(key=lambda d: d["agreement_rate"], reverse=True)
    return result


@app.get("/api/projects/{project_slug}/fields/{field_name}/self-consistency")
def self_consistency_summary(project_slug: str, field_name: str) -> list[dict]:
    """Per-model self-consistency: mean agreement across the repeat-sampling
    validation study (see scripts/self_consistency.py). Empty if the study
    hasn't been run for this field."""
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        try:
            project_id = db.get_project_id(conn, project_slug)
            rows = conn.execute(
                "SELECT model_id, COUNT(*) AS n_records, AVG(agreement) AS mean_agreement, "
                "AVG(n_samples) AS mean_samples FROM self_consistency "
                "WHERE project_id = ? AND field_name = ? GROUP BY model_id",
                (project_id, field_name),
            ).fetchall()
        except db.sqlite3.OperationalError:
            return []  # table not present on this (older) DB yet
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_slug}/fields/{field_name}/calibration")
def calibration(project_slug: str, field_name: str, prompt_version: int | None = None) -> list[dict]:
    """Per-model calibration of the model's *verbalized* confidence (the 0-1
    self-reported probability it attached to each answer) against whether the
    answer was actually correct (`is_correct`). Reports the Brier score
    (mean squared error between stated confidence and correctness -- lower is
    better, a proper scoring rule that rewards honest probabilities) plus
    reliability-diagram bins (stated confidence vs. observed accuracy per
    confidence band). This is a posterior diagnostic only -- confidence is NOT
    folded into the per-run score."""
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        pvid = _resolve_pvid(conn, project_id, field_name, prompt_version)
        rows = conn.execute(
            "SELECT model_id, confidence, is_correct FROM runs "
            "WHERE project_id = ? AND field_name = ? AND prompt_version_id = ? "
            "AND confidence IS NOT NULL AND is_correct IS NOT NULL "
            "AND error IS NULL",
            (project_id, field_name, pvid),
        ).fetchall()

    n_bins = 5
    by_model: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for r in rows:
        by_model[r["model_id"]].append((float(r["confidence"]), int(r["is_correct"])))

    result = []
    for mid, pairs in by_model.items():
        n = len(pairs)
        if n == 0:
            continue
        brier = sum((c - y) ** 2 for c, y in pairs) / n
        bins = []
        for b in range(n_bins):
            lo, hi = b / n_bins, (b + 1) / n_bins
            members = [
                (c, y) for c, y in pairs
                if c >= lo and (c < hi or (b == n_bins - 1 and c <= hi))
            ]
            if members:
                bins.append({
                    "lo": lo, "hi": hi, "n": len(members),
                    "mean_confidence": sum(c for c, _ in members) / len(members),
                    "accuracy": sum(y for _, y in members) / len(members),
                })
            else:
                bins.append({"lo": lo, "hi": hi, "n": 0, "mean_confidence": None, "accuracy": None})
        result.append({
            "model_id": mid,
            "n_scored": n,
            "brier": brier,
            "mean_confidence": sum(c for c, _ in pairs) / n,
            "accuracy": sum(y for _, y in pairs) / n,
            "bins": bins,
        })
    result.sort(key=lambda d: d["brier"])  # lower Brier = better calibrated
    return result


@app.get("/api/projects/{project_slug}/fields/{field_name}/stage-status")
def stage_status(project_slug: str, field_name: str, prompt_version: int | None = None) -> dict:
    """Derived staged-rollout status for a field (no manual state): how many
    references it has reached (= current stage), and the quality gate evaluated
    PER MODEL within the field (each model's own LLM-judged accuracy vs. the
    gate threshold), plus how many prompt versions have been tried. The gate is
    per (field, model) -- a field is not uniformly 'passed'/'gated'; individual
    models pass or fail it -- so the dashboard summarises as 'N/M models pass'.
    When prompt_version is given, gate metrics are computed only over runs from
    that version so the frontend can show per-version F1/accuracy progression."""
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        pvid = _resolve_pvid(conn, project_id, field_name, prompt_version)
        row = conn.execute(
            "SELECT COUNT(DISTINCT record_id) AS recs FROM runs "
            "WHERE project_id = ? AND field_name = ? AND error IS NULL",
            (project_id, field_name),
        ).fetchone()
        references = (row["recs"] if row else 0) or 0
        # Optional version filter appended to queries that touch runs.
        v_clause = "AND r.prompt_version_id = ?" if pvid is not None else ""
        v_params: tuple = (pvid,) if pvid is not None else ()
        jrows = conn.execute(
            "SELECT r.model_id, AVG(CASE WHEN j.verdict = 1 THEN 1.0 ELSE 0.0 END) AS acc, "
            f"COUNT(*) AS n FROM llm_judgments j JOIN runs r ON r.id = j.run_id "
            f"WHERE r.project_id = ? AND r.field_name = ? {v_clause} GROUP BY r.model_id",
            (project_id, field_name) + v_params,
        ).fetchall()
        rrows = conn.execute(
            "SELECT r.model_id, r.parsed_value_json, g.value_json FROM runs r "
            "JOIN ground_truth g ON g.project_id = r.project_id AND g.record_id = r.record_id "
            f"AND g.field_name = r.field_name "
            f"WHERE r.project_id = ? AND r.field_name = ? AND r.parsed_value_json IS NOT NULL {v_clause}",
            (project_id, field_name) + v_params,
        ).fetchall()
        pv = conn.execute(
            "SELECT COUNT(*) AS total, SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS accepted "
            "FROM prompt_versions WHERE project_id = ? AND field_name = ?",
            (project_id, field_name),
        ).fetchone()

    # Judged accuracy is kept as a reported *concordance* companion; the GATE
    # itself is now the field-type-aware quality metric (F1 for list fields,
    # accuracy for categorical) computed from runs -- see analytics.gate_metrics.
    judged_by_model = {
        jr["model_id"]: (jr["acc"], jr["n"] or 0)
        for jr in jrows if (jr["n"] or 0) > 0 and jr["acc"] is not None
    }
    rows_by_model: dict[str, list[dict]] = {}
    for rr in rrows:
        rows_by_model.setdefault(rr["model_id"], []).append(
            {"predicted": json.loads(rr["parsed_value_json"]), "truth": json.loads(rr["value_json"])}
        )

    models = []
    for model_id, mrows in rows_by_model.items():
        gm = analytics.gate_metrics(field_name, mrows)
        judged = judged_by_model.get(model_id)
        models.append(
            {
                "model_id": model_id,
                "gate_metric_name": gm["metric_name"],
                "gate_metric": gm["metric"],
                "precision": gm["precision"],
                "recall": gm["recall"],
                "f1": gm["f1"],
                "accuracy": gm["accuracy"],
                "kappa": gm["kappa"],
                "n": gm["n"],
                "llm_judged_accuracy": judged[0] if judged else None,
                "n_judged": judged[1] if judged else 0,
                "gate_passed": gm["metric"] >= scoring.GATE_THRESHOLD,
            }
        )
    models.sort(key=lambda m: m["gate_metric"], reverse=True)

    stages = list(config.PRODUCTION_ROLLOUT_STAGES)
    next_target = next((s for s in stages if s > references), None)  # None = final stage reached
    return {
        "references": references,
        "stages": stages,
        "stage_target": next_target,
        "final_stage": stages[-1] if stages else references,
        "gate_threshold": scoring.GATE_THRESHOLD,
        "models": models,
        "n_models_evaluated": len(models),
        "n_models_judged": sum(1 for m in models if m["llm_judged_accuracy"] is not None),
        "n_models_passing": sum(1 for m in models if m["gate_passed"]),
        "n_judged": sum(m["n_judged"] for m in models),
        "prompt_versions": (pv["total"] if pv else 0) or 0,
        "prompt_versions_accepted": (pv["accepted"] if pv else 0) or 0,
    }


@app.get("/api/projects/{project_slug}/fields/{field_name}/iterations")
def iterations(project_slug: str, field_name: str, model_id: str | None = None) -> list[dict]:
    """Optimizer iteration log for a field, optionally scoped to one model
    (each iteration is always tied to exactly one "student" model being
    optimized — see optimizer.optimize_field). Enriched with the candidate
    prompt version's number/template/notes so the frontend can render a
    per-model progress chart + prompt lineage from a single call.
    """
    _field_or_404(project_slug, field_name)
    q = (
        "SELECT it.id, it.field_name, it.iteration_num, it.prompt_version_id, it.model_id, "
        "it.mean_score, it.n_records, it.feedback, it.accepted, it.created_at, "
        "pv.version AS prompt_version, pv.template AS prompt_template, pv.notes AS prompt_notes "
        "FROM iterations it JOIN prompt_versions pv ON pv.id = it.prompt_version_id "
        "WHERE it.project_id = ? AND it.field_name = ?"
    )
    params: list = []
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        params = [project_id, field_name]
        if model_id:
            q += " AND it.model_id = ?"
            params.append(model_id)
        q += " ORDER BY it.iteration_num"
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_slug}/fields/{field_name}/runs")
def runs(project_slug: str, field_name: str, model_id: str | None = None, limit: int = 200) -> list[dict]:
    _field_or_404(project_slug, field_name)
    q = "SELECT * FROM runs WHERE project_id = ? AND field_name = ?"
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        params: list = [project_id, field_name]
        if model_id:
            q += " AND model_id = ?"
            params.append(model_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/projects/{project_slug}/fields/{field_name}/confusion")
def confusion(project_slug: str, field_name: str, model_id: str | None = None, prompt_version: int | None = None) -> dict:
    """Confusion matrix (categorical fields) or micro precision/recall/F1/F2
    (list fields), computed live from logged runs + ground truth."""
    _field_or_404(project_slug, field_name)
    q = (
        "SELECT r.parsed_value_json, g.value_json FROM runs r "
        "JOIN ground_truth g ON g.project_id = r.project_id AND g.record_id = r.record_id "
        "AND g.field_name = r.field_name "
        "WHERE r.project_id = ? AND r.field_name = ? AND r.parsed_value_json IS NOT NULL"
    )
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        params: list = [project_id, field_name]
        pvid = _resolve_pvid(conn, project_id, field_name, prompt_version)
        q += " AND r.prompt_version_id = ?"
        params.append(pvid)
        if model_id:
            q += " AND r.model_id = ?"
            params.append(model_id)
        db_rows = conn.execute(q, params).fetchall()
    rows = [
        {"predicted": json.loads(r["parsed_value_json"]), "truth": json.loads(r["value_json"])}
        for r in db_rows
    ]
    return analytics.compute_confusion(field_name, rows)


@app.get("/api/projects/{project_slug}/fields/{field_name}/jobs")
def jobs(project_slug: str, field_name: str) -> list[dict]:
    """Recent extraction/optimization jobs for a field, so the dashboard can
    show a "currently running" indicator. A job's `status` column is set by
    the script that owns it, but a script can die (crash, Ctrl+C, killed
    terminal) without ever calling `finish_job` — so a "running" job whose
    `updated_at` is older than `db.JOB_STALE_AFTER_SECONDS` is reported here
    with `stale: true` instead of being trusted at face value.
    """
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        rows = db.get_jobs_for_field(conn, project_id, field_name)
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


# ── Project creation & corpus management ─────────────────────────────────────

def _field_type_to_value_type(wizard_type: str) -> str:
    """Map wizard field type → FieldSpec.value_type."""
    return {"list": "list_text", "categorical": "single_categorical"}.get(wizard_type, "list_text")


def _load_db_project(slug: str) -> "ProjectSpec | None":
    """Load a dynamically-created project from the DB. Returns None if not found."""
    from .projects import ProjectSpec
    from .fields import FieldSpec
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT name, description, project_type, config_json FROM projects WHERE slug = ?",
                (slug,),
            ).fetchone()
        if not row:
            return None
        cfg = json.loads(row["config_json"] or "{}")
        fields_cfg = cfg.get("fields", [])
        field_specs: dict[str, FieldSpec] = {}
        for f in fields_cfg:
            vtype = _field_type_to_value_type(f.get("type", "text"))
            # Categorical fields may have a taxonomy stored in the config
            spec = FieldSpec(
                name=f["name"],
                label=f.get("label", f["name"]),
                value_type=vtype,
                taxonomy_key=None,  # user taxonomy stored separately, not keyed into taxonomy.json
                description=f.get("description", ""),
            )
            field_specs[f["name"]] = spec
        return ProjectSpec(
            slug=slug,
            name=row["name"],
            description=row["description"] or "",
            fields=field_specs,
        )
    except Exception:
        return None


# Monkey-patch _project_or_404 to include DB lookup
_orig_project_or_404 = _project_or_404


def _project_or_404(project_slug: str):  # type: ignore[redefinition]
    if project_slug in PROJECTS:
        return PROJECTS[project_slug]
    p = _load_db_project(project_slug)
    if p:
        return p
    raise HTTPException(status_code=404, detail=f"Unknown project: {project_slug!r}")


class CreateProjectBody(BaseModel):
    name: str
    slug: str
    description: str = ""
    project_type: str = "extraction"  # extraction | screening_ta | screening_ft
    password: str = ""
    config: dict = {}


@app.post("/api/projects")
async def create_project(request: Request, body: CreateProjectBody):
    """Create a new project from the onboarding wizard."""
    _require_auth(dict(request.headers))

    # Basic slug validation
    import re
    if not re.match(r'^[a-z0-9][a-z0-9\-]{1,60}$', body.slug):
        raise HTTPException(400, "Slug must be lowercase alphanumeric + hyphens, 2-61 chars.")

    # Optional password hash
    pw_hash: str | None = None
    if body.password:
        import hashlib
        pw_hash = hashlib.sha256(body.password.encode()).hexdigest()

    with db.get_conn() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE slug = ?", (body.slug,)).fetchone()
        if existing:
            raise HTTPException(409, f"Project slug '{body.slug}' already exists.")
        conn.execute(
            "INSERT INTO projects (slug, name, description, project_type, config_json, password_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body.slug, body.name, body.description, body.project_type,
             json.dumps(body.config), pw_hash, db.now()),
        )
        project_id = conn.execute("SELECT id FROM projects WHERE slug = ?", (body.slug,)).fetchone()["id"]

        # Create baseline prompt version for each field
        fields_cfg = body.config.get("fields", [])
        for f in fields_cfg:
            existing_pv = conn.execute(
                "SELECT id FROM prompt_versions WHERE project_id = ? AND field_name = ? AND model_id IS NULL",
                (project_id, f["name"]),
            ).fetchone()
            if not existing_pv:
                conn.execute(
                    "INSERT INTO prompt_versions (project_id, field_name, model_id, version, template, "
                    "parent_id, notes, accepted, created_at) VALUES (?, ?, NULL, 1, ?, NULL, 'baseline v1', 1, ?)",
                    (project_id, f["name"], f.get("description", ""), db.now()),
                )

    # Create corpus directory
    corpus_dir = config.PROJECTS_DATA_DIR / body.slug / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    return {"slug": body.slug, "project_id": project_id, "status": "created"}


@app.post("/api/projects/{project_slug}/corpus")
async def upload_corpus(request: Request, project_slug: str, files: list[UploadFile]):
    """Upload corpus files (PDF or markdown). PDFs are converted to markdown."""
    _require_auth(dict(request.headers))
    _project_or_404(project_slug)

    corpus_dir = config.PROJECTS_DATA_DIR / project_slug / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)

    saved: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        stem = Path(upload.filename or "unnamed").stem
        raw = await upload.read()

        if (upload.filename or "").lower().endswith(".pdf"):
            try:
                import tempfile, pymupdf4llm  # type: ignore
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = tmp.name
                md_text = pymupdf4llm.to_markdown(tmp_path)
                Path(tmp_path).unlink(missing_ok=True)
            except Exception as exc:
                errors.append({"file": upload.filename, "error": f"PDF conversion failed: {exc}"})
                continue
        else:
            md_text = raw.decode("utf-8", errors="replace")

        out_path = corpus_dir / f"{stem}.md"
        out_path.write_text(md_text, encoding="utf-8")

        # Register record in DB (use stem as record_id placeholder)
        with db.get_conn() as conn:
            # Use a hash of the filename as a stable integer record_id
            record_id = abs(hash(stem)) % (2**31)
            conn.execute(
                "INSERT OR IGNORE INTO records (project_id, id, title, md_path) VALUES (?, ?, ?, ?)",
                (project_id, record_id, stem, str(out_path)),
            )
        saved.append({"file": upload.filename, "record_id": record_id, "md_path": str(out_path)})

    return {"saved": len(saved), "errors": errors, "records": saved}


@app.post("/api/projects/{project_slug}/ground-truth")
async def upload_ground_truth(request: Request, project_slug: str, file: UploadFile):
    """Upload a CSV of ground-truth labels into the ground_truth table."""
    _require_auth(dict(request.headers))
    _project_or_404(project_slug)

    import csv, io
    text = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []

    is_screening = "decision" in headers
    if is_screening:
        required = {"record_id", "decision"}
    else:
        required = {"record_id", "field_name", "value"}

    missing = required - set(headers)
    if missing:
        raise HTTPException(422, f"CSV missing required columns: {sorted(missing)}")

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        inserted = skipped = 0
        for row in reader:
            rid_raw = row.get("record_id", "").strip()
            if not rid_raw:
                continue
            try:
                record_id = int(rid_raw)
            except ValueError:
                record_id = abs(hash(rid_raw)) % (2**31)

            if is_screening:
                field_name = "screening_decision"
                value = json.dumps({
                    "decision": row.get("decision", "").strip().upper(),
                    "exclusion_tag": row.get("exclusion_tag", "").strip(),
                })
            else:
                field_name = row.get("field_name", "").strip()
                raw_val = row.get("value", "").strip()
                # Pipe-separated → list
                value = json.dumps([v.strip() for v in raw_val.split("|")] if "|" in raw_val else raw_val)

            try:
                conn.execute(
                    "INSERT OR REPLACE INTO ground_truth (project_id, record_id, field_name, value_json) "
                    "VALUES (?, ?, ?, ?)",
                    (project_id, record_id, field_name, value),
                )
                inserted += 1
            except Exception:
                skipped += 1

    return {"inserted": inserted, "skipped": skipped}


@app.post("/api/projects/{project_slug}/launch")
async def launch_extraction(request: Request, project_slug: str):
    """Kick off the first extraction run for a newly created project."""
    _require_auth(dict(request.headers))
    project = _project_or_404(project_slug)

    import subprocess, sys
    cfg = {}
    with db.get_conn() as conn:
        row = conn.execute("SELECT config_json FROM projects WHERE slug = ?", (project_slug,)).fetchone()
        if row and row["config_json"]:
            cfg = json.loads(row["config_json"])

    selected_models = cfg.get("selected_models", [])
    if not selected_models:
        raise HTTPException(400, "No models selected. Add selected_models to project config.")

    jobs = []
    for field_name in project.fields:
        for model_id in selected_models:
            cmd = [
                sys.executable, "-m", "backend.scripts.run_extraction",
                "--project", project_slug,
                "--field", field_name,
                "--models", model_id,
                "--n", "100",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            jobs.append({"field": field_name, "model": model_id, "pid": proc.pid})

    return {"launched": len(jobs), "jobs": jobs}

