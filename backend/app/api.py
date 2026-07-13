"""FastAPI layer over promptlab.db, for the observability frontend.

Read endpoints (GET) are public so the deployed dashboard works for anonymous
visitors; write endpoints require a Bearer JWT (see the auth section below).
CORS is restricted to the dev server + deployed frontend origins.

Run with:
    uvicorn backend.app.api:app --reload --port 8000
or:
    python -m backend.scripts.serve
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import analytics, config, db, db_pg, gateway, optimization_policy, scoring
from .projects import PROJECTS

logger = logging.getLogger("promptlab.api")

app = FastAPI(title="Agentic 3ie Prompt Lab API")

# Phase 2: init Postgres schema on startup (idempotent)
@app.on_event("startup")
async def _startup():
    if db_pg.pg_enabled():
        db_pg.init_pg()

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


# ── Auth — JWT (HS256, stdlib only) ─────────────────────────────────────────
# Tokens are signed with JWT_SECRET (Fly secret). No server-side storage →
# survives machine restarts and redeploys without invalidating sessions.
# PROMPTLAB_PASSWORD is the shared write-password for @3ieimpact.org users.
_ALLOWED_DOMAIN = "@3ieimpact.org"
_JWT_TTL_HOURS = 72  # token valid for 3 days


def _jwt_secret() -> bytes:
    # Prefer a dedicated JWT_SECRET; fall back to the password itself so the
    # system still works if JWT_SECRET hasn't been set yet (tokens won't survive
    # password changes). NEVER fall back to a hardcoded constant -- a value that
    # lives in the public repo would let anyone forge a valid token. Fail closed
    # if neither is configured.
    s = os.environ.get("JWT_SECRET") or os.environ.get("PROMPTLAB_PASSWORD")
    if not s:
        raise HTTPException(
            status_code=503,
            detail="Server authentication is not configured (JWT_SECRET / PROMPTLAB_PASSWORD unset).",
        )
    return s.encode()


def _make_jwt(email: str) -> str:
    import base64, hmac as _hmac, hashlib, time as _time
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    payload_data = json.dumps({"sub": email, "exp": int(_time.time()) + _JWT_TTL_HOURS * 3600}, separators=(",", ":"))
    payload = base64.urlsafe_b64encode(payload_data.encode()).rstrip(b"=").decode()
    sig_input = f"{header}.{payload}".encode()
    sig = base64.urlsafe_b64encode(
        _hmac.new(_jwt_secret(), sig_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.{sig}"


def _verify_jwt(token: str) -> str:
    """Verify signature + expiry. Returns the email subject or raises HTTPException."""
    import base64, hmac as _hmac, hashlib, time as _time
    try:
        header, payload, sig = token.split(".")
    except ValueError:
        raise HTTPException(401, "Invalid token format.")
    # Pad base64 as needed
    def _decode(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    expected_sig = base64.urlsafe_b64encode(
        _hmac.new(_jwt_secret(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not secrets.compare_digest(sig, expected_sig):
        raise HTTPException(401, "Invalid or expired token. Please sign in again.")
    try:
        data = json.loads(_decode(payload))
    except Exception:
        raise HTTPException(401, "Malformed token payload.")
    if data.get("exp", 0) < _time.time():
        raise HTTPException(401, "Token expired. Please sign in again.")
    return data.get("sub", "")


class AuthRequest(BaseModel):
    email: str
    password: str


# ── Login throttle (in-memory; single Fly machine) ──────────────────────────
# Brute-forcing the one shared password would yield org-wide write access, so
# cap failed attempts per client IP within a rolling window.
_LOGIN_MAX_FAILURES = 10
_LOGIN_WINDOW_S = 300
_login_failures: dict[str, list[float]] = defaultdict(list)


_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file


async def _read_upload_capped(upload: UploadFile, max_bytes: int = _MAX_UPLOAD_BYTES) -> bytes:
    """Read an upload in bounded chunks and reject anything over the cap, so a
    single huge upload can't exhaust server memory."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(status_code=413, detail=f"File too large (max {max_bytes // (1024 * 1024)} MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def _client_ip(request: Request) -> str:
    # Trust ONLY the proxy-set header (Fly sets `fly-client-ip`). The
    # client-supplied `x-forwarded-for` is deliberately NOT trusted for
    # rate-limiting: honoring it lets an attacker rotate the header to get a
    # fresh throttle bucket per request and bypass the login attempt cap.
    fly = request.headers.get("fly-client-ip", "").split(",")[0].strip()
    if fly:
        return fly
    return request.client.host if request.client else "unknown"


def _stable_id(value) -> int:
    """Deterministic integer ``record_id`` from an arbitrary string key.

    Numeric keys (e.g. EPPI IDs like the corpus filename ``"10154"``) map to
    their integer value so corpus records and ground-truth rows keyed on the
    same ID join correctly. Non-numeric keys hash via SHA-256, which is stable
    across processes — unlike the builtin ``hash()`` (salted per-process by
    PYTHONHASHSEED), whose result changed on every restart/worker and both broke
    INSERT-OR-IGNORE dedup and silently prevented corpus↔ground-truth joins.
    """
    s = str(value).strip()
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return int(float(s))  # tolerate "10154.0" from pandas/CSV
    except (ValueError, OverflowError):
        return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16)


def _hash_password(password: str) -> str:
    """Salted PBKDF2-SHA256 (stdlib, no deps) for the optional project write
    password. Format: pbkdf2_sha256$iterations$salt_hex$hash_hex."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return secrets.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _check_login_rate(ip: str) -> None:
    now = time.time()
    recent = [t for t in _login_failures[ip] if now - t < _LOGIN_WINDOW_S]
    _login_failures[ip] = recent
    if len(recent) >= _LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")


@app.post("/api/auth/token")
def auth_token(request: Request, body: AuthRequest):
    """Issue a signed JWT for a verified @3ieimpact.org email + shared password.
    Token is valid for 72 hours and survives server restarts."""
    ip = _client_ip(request)
    _check_login_rate(ip)
    if not body.email.lower().endswith(_ALLOWED_DOMAIN):
        _login_failures[ip].append(time.time())
        raise HTTPException(status_code=401, detail="Email must be a @3ieimpact.org address.")
    env_password = os.environ.get("PROMPTLAB_PASSWORD", "")
    if not env_password:
        raise HTTPException(status_code=503, detail="PROMPTLAB_PASSWORD env var not set on this server.")
    if not secrets.compare_digest(body.password, env_password):
        _login_failures[ip].append(time.time())
        raise HTTPException(status_code=401, detail="Incorrect password.")
    _login_failures.pop(ip, None)  # reset on success
    return {"token": _make_jwt(body.email)}


def _require_auth(request_headers) -> None:
    """Validate Bearer JWT on write endpoints."""
    auth = request_headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required.")
    _verify_jwt(auth.removeprefix("Bearer ").strip())


def _is_authed(request_headers) -> bool:
    """Non-raising auth check for read endpoints that expose more detail to
    signed-in users (e.g. server log tails) while staying public otherwise."""
    auth = request_headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return False
    try:
        _verify_jwt(auth.removeprefix("Bearer ").strip())
        return True
    except HTTPException:
        return False


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


@app.get("/api/activity")
def activity(request: Request, log_lines: int = 30) -> dict:
    """Live activity feed: worker task queue state + recent supervisor log lines.
    Designed for polling every 5s from the dashboard's Live panel. The raw
    supervisor log tail can leak internal paths/exception detail, so it is only
    returned to authenticated callers; the queue state stays public."""
    log_lines = max(1, min(log_lines, 200))  # bound the file tail read
    tasks: list[dict] = []
    queue_summary: dict = {"pending": 0, "running": 0, "total_active": 0}
    # Optimizer health over the last 24h — so a crash-loop or stall is visible,
    # never silent (this is exactly the signal that was missing before).
    optimizer_health: dict = {}

    if db_pg.pg_enabled():
        try:
            with db_pg.get_pg_conn() as pg:
                with pg.cursor() as cur:
                    cur.execute(
                        "SELECT field_name, model_id, kind, status, claimed_at, created_at, error "
                        "FROM worker_tasks WHERE status IN ('pending','running') "
                        "ORDER BY priority DESC, id ASC LIMIT 50"
                    )
                    rows = cur.fetchall()
                    tasks = [dict(r) for r in rows]
                    queue_summary["pending"] = sum(1 for t in tasks if t["status"] == "pending")
                    queue_summary["running"] = sum(1 for t in tasks if t["status"] == "running")
                    queue_summary["total_active"] = len(tasks)
                    # Also fetch last 5 recently-finished tasks for context
                    cur.execute(
                        "SELECT field_name, model_id, kind, status, finished_at, error "
                        "FROM worker_tasks WHERE status IN ('done','failed') "
                        "ORDER BY finished_at DESC NULLS LAST LIMIT 5"
                    )
                    recent_done = [dict(r) for r in cur.fetchall()]
                    cur.execute(
                        "SELECT status, COUNT(*) AS n FROM worker_tasks "
                        "WHERE kind = 'optimization' AND finished_at > now() - interval '24 hours' "
                        "GROUP BY status"
                    )
                    oc = {r["status"]: r["n"] for r in cur.fetchall()}
                    done_24h, failed_24h = oc.get("done", 0), oc.get("failed", 0)
                    total_24h = done_24h + failed_24h
                    # Accept-rate: of the optimization candidates logged in the
                    # last 24h, what fraction were *accepted* as improvements.
                    cur.execute(
                        "SELECT COUNT(*) AS n, "
                        "SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS acc "
                        "FROM iterations WHERE created_at > now() - interval '24 hours'"
                    )
                    irow = cur.fetchone()
                    i_total = irow["n"] or 0
                    i_acc = irow["acc"] or 0
                    optimizer_health = {
                        "runs_24h": total_24h,
                        "failed_24h": failed_24h,
                        "failure_rate": (failed_24h / total_24h) if total_24h else 0.0,
                        "candidates_24h": i_total,
                        "accepted_24h": i_acc,
                        "accept_rate": (i_acc / i_total) if i_total else 0.0,
                    }
        except Exception as exc:
            tasks = []
            recent_done = []
            logger.warning("activity: worker-task query failed: %s", exc)
            queue_summary["error"] = "queue unavailable"
    else:
        recent_done = []

    # Supervisor log tail — only for authenticated callers (raw log content can
    # contain internal paths / exception detail we don't expose anonymously).
    log_tail: list[str] = []
    if _is_authed(request.headers):
        log_path = os.environ.get("SUPERVISOR_LOG", "/data/supervisor.log")
        try:
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                log_tail = [l.rstrip() for l in all_lines[-log_lines:] if l.strip()]
        except Exception:
            pass

    return {
        "queue": queue_summary,
        "active_tasks": tasks,
        "recently_done": recent_done,
        "log_tail": log_tail,
        "optimizer_health": optimizer_health,
    }


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

        # When no specific version is requested, show each model at its OWN
        # best accepted prompt version (same as models-summary does) rather
        # than mixing runs from all versions, which would produce a misleading
        # aggregate metric.
        if pvid is None:
            model_pvids = _best_pvids_per_model(conn, project_id, field_name)
            if model_pvids:
                or_clauses = " OR ".join(
                    "(r.model_id = ? AND r.prompt_version_id = ?)" for _ in model_pvids
                )
                or_params = tuple(p for mid, mpvid in model_pvids.items() for p in (mid, mpvid))
                v_clause = f"AND ({or_clauses})"
                v_params: tuple = or_params
            else:
                v_clause = ""
                v_params = ()
            # Build {pvid → version number} for the response
            pvid_to_version: dict[int, int] = {}
            if model_pvids:
                pvids = list(set(model_pvids.values()))
                rows_pv = conn.execute(
                    "SELECT id, version FROM prompt_versions WHERE id IN ({})".format(
                        ",".join("?" * len(pvids))
                    ),
                    pvids,
                ).fetchall()
                pvid_to_version = {r["id"]: r["version"] for r in rows_pv}
            model_version_map = {mid: pvid_to_version.get(mpvid) for mid, mpvid in (model_pvids or {}).items()}
        else:
            v_clause = "AND r.prompt_version_id = ?"
            v_params = (pvid,)
            model_version_map = {}
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

        # Per-model optimizer stats for the cost-benefit status (same policy the
        # supervisor uses): total candidates, accepted, and how many tried since
        # the last accepted gain.
        opt_stats: dict[str, tuple[int, int, int]] = {}
        for orow in conn.execute(
            "SELECT model_id, COUNT(*) AS n, "
            "SUM(CASE WHEN accepted = 1 THEN 1 ELSE 0 END) AS acc, "
            "MAX(CASE WHEN accepted = 1 THEN created_at END) AS last_acc "
            "FROM iterations WHERE project_id = ? AND field_name = ? AND model_id IS NOT NULL "
            "GROUP BY model_id",
            (project_id, field_name),
        ).fetchall():
            mid, n, acc, last_acc = orow["model_id"], orow["n"], orow["acc"] or 0, orow["last_acc"]
            if last_acc is None:
                since = n
            else:
                since = conn.execute(
                    "SELECT COUNT(*) AS c FROM iterations WHERE project_id = ? AND field_name = ? "
                    "AND model_id = ? AND created_at > ?",
                    (project_id, field_name, mid, last_acc),
                ).fetchone()["c"]
            opt_stats[mid] = (n, since, acc)

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
        gate_passed = gm["metric"] >= scoring.GATE_THRESHOLD
        # List fields must ALSO clear the recall floor: element-level F1 can pass
        # by over-predicting while still missing >15% of true values (invisible in
        # QA), so a model with F1>=0.90 but recall<0.85 is NOT production-ready.
        # Categorical fields have recall=None, so the floor doesn't apply to them.
        # This mirrors optimizer.py / supervisor.py so the dashboard's gate matches
        # the gate the optimizer actually enforces (previously it did not).
        if gate_passed and gm.get("recall") is not None and gm["recall"] < scoring.RECALL_FLOOR:
            gate_passed = False
        # Judge companion gate (authors over-crediting fix): the element-level
        # F1 is too lenient on near-miss author lists — it accepts partial /
        # reordered / dropped-co-author matches a human would reject. When an
        # LLM judge is available and disagrees by more than the tolerance band,
        # the model does NOT truly pass the gate even if F1 clears the bar.
        # This is the exact gap the judge-vs-scorer analysis found: F1 said 77%
        # but the judge said 66% for authors.
        judge_disagreement = False
        judge_accuracy = judged[0] if judged else None
        if judge_accuracy is not None and gate_passed:
            JUDGE_TOLERANCE = 0.10  # 10 pts: scorer flatters by more than this -> not truly passing
            if gm["metric"] - judge_accuracy > JUDGE_TOLERANCE:
                gate_passed = False
                judge_disagreement = True
        n_cand, since_accept, n_acc = opt_stats.get(model_id, (0, 0, 0))
        # Optimizer cost-benefit status for this (field, model): 'passed' if it
        # clears the gate, else the policy verdict (optimize / plateaued /
        # task_limited / budget). Lets the UI show what's still worth chasing.
        if gate_passed:
            opt_status, opt_reason = "passed", ""
        else:
            _ok, opt_status, opt_reason = optimization_policy.decide(
                n_cand, since_accept, gm["metric"], scoring.GATE_THRESHOLD
            )
        models.append(
            {
                "model_id": model_id,
                "gate_metric_name": gm["metric_name"],
                "gate_metric": gm["metric"],
                "precision": gm["precision"],
                "recall": gm["recall"],
                "sensitivity": gm.get("sensitivity"),  # categorical macro-sensitivity (not a gate input)
                "f1": gm["f1"],
                "accuracy": gm["accuracy"],
                "kappa": gm["kappa"],
                "n": gm["n"],
                "llm_judged_accuracy": judge_accuracy,
                "n_judged": judged[1] if judged else 0,
                "gate_passed": gate_passed,
                "judge_disagreement": judge_disagreement,
                "prompt_version": model_version_map.get(model_id),
                "opt_status": opt_status,
                "opt_reason": opt_reason,
                "n_candidates": n_cand,
                "n_accepted": n_acc,
            }
        )
    models.sort(key=lambda m: m["gate_metric"], reverse=True)
    n_needs_review = sum(1 for m in models if m["opt_status"] in optimization_policy.STOP_STATUSES)

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
        "n_needs_review": n_needs_review,
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
    limit = max(1, min(limit, 2000))  # cap: this is a public, unauthenticated read
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
    _field_or_404(project_slug, field_name)
    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        # Phase 2: read jobs from Postgres when available; fall back to SQLite
        if db_pg.pg_enabled():
            with db_pg.get_pg_conn() as pg:
                rows = db_pg.get_jobs_pg(pg, project_id, field_name)
        else:
            rows = db.get_jobs_for_field(conn, project_id, field_name)
    now = datetime.now(timezone.utc)
    result = []
    for r in rows:
        d = dict(r)
        stale = False
        if d.get("status") == "running":
            try:
                updated = d.get("updated_at")
                if updated:
                    ts = updated if hasattr(updated, "total_seconds") else datetime.fromisoformat(str(updated))
                    age_s = (now - ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else now - ts).total_seconds()
                    stale = age_s > db.JOB_STALE_AFTER_SECONDS
            except Exception:
                pass
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

    # Optional password hash (salted PBKDF2, not bare SHA-256)
    pw_hash: str | None = None
    if body.password:
        pw_hash = _hash_password(body.password)

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

        # Create baseline prompt version for each field/criterion.
        # Extraction: fields list  →  each field description becomes the v1 template.
        # Screening: exclusion_criteria list  →  each criterion's yes/no question becomes
        #   the v1 template for a field named after the tag (e.g. "on_topic_interest").
        fields_cfg = body.config.get("fields", [])
        criteria_cfg = body.config.get("exclusion_criteria", [])

        def _upsert_pv(project_id: int, field_name: str, template: str, notes: str) -> None:
            existing = conn.execute(
                "SELECT id FROM prompt_versions WHERE project_id = ? AND field_name = ? AND model_id IS NULL",
                (project_id, field_name),
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO prompt_versions (project_id, field_name, model_id, version, template, "
                    "parent_id, notes, accepted, created_at) VALUES (?, ?, NULL, 1, ?, NULL, ?, 1, ?)",
                    (project_id, field_name, template, notes, db.now()),
                )

        for f in fields_cfg:
            _upsert_pv(project_id, f["name"], f.get("description", ""), "baseline v1")

        for c in criteria_cfg:
            # Use the tag as the field_name; the yes/no question is the template.
            tag = c.get("tag", "").strip()
            question = c.get("question", "").strip()
            label = c.get("label", tag)
            order = c.get("order", 0)
            if tag and question:
                _upsert_pv(
                    project_id, tag, question,
                    f"baseline v1 (screening criterion {order}: {label})"
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
        raw = await _read_upload_capped(upload)

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
            # Stable integer record_id derived from the filename stem
            record_id = _stable_id(stem)
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
    text = (await _read_upload_capped(file)).decode("utf-8", errors="replace")
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
            record_id = _stable_id(rid_raw)

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


@app.post("/api/screening/suggest-question")
async def suggest_screening_question(request: Request):
    """Generate a detailed, rule-based screening instruction for one exclusion criterion.
    Uses Claude Sonnet and the review scope to produce extraction-quality instructions."""
    _require_auth(dict(request.headers))
    body = await request.json()
    label = str(body.get("label", "")).strip()
    review_scope = str(body.get("review_scope", "")).strip()
    if not label:
        raise HTTPException(400, "label is required")

    scope_block = (
        f"\nThe review's inclusion scope: {review_scope}"
        if review_scope
        else "\n(No review scope provided — write general rules the user should refine.)"
    )

    system = (
        "You are an expert systematic review methodologist writing LLM-based title/abstract screening instructions. "
        "You write the INSTRUCTION that tells another LLM how to apply a specific exclusion criterion to a paper. "
        "Your instruction will be used verbatim — write it as a directive to the screening LLM.\n\n"
        "REQUIRED structure (prose, no bullet points, 3-6 sentences):\n"
        "1. State clearly what triggers EXCLUSION (be specific — what must be true about the paper).\n"
        "2. State what does NOT trigger exclusion (common false positives to avoid).\n"
        "3. Add at least one disambiguation rule for edge cases.\n"
        "4. Instruct the model to lean INCLUDE when genuinely uncertain (conservative screening).\n\n"
        "Match the precision of this extraction example:\n"
        "'List EVERY author of the paper, one entry per author, in the order they appear in the "
        "title/author block — check for co-authors named after the first author and in footnotes, "
        "do not stop at the first name you find. Format each as Last name, First name Middle name. "
        "If the paper gives only initials for the first/middle name, keep the initials exactly as printed.'\n\n"
        "Do NOT start with 'I' or 'You'. Do NOT write a yes/no question — write a screening rule."
    )
    user = (
        f"Exclusion criterion: \"{label}\""
        f"{scope_block}\n\n"
        "Write the screening instruction for this criterion."
    )

    try:
        resp = gateway.call_model(
            "~anthropic/claude-sonnet-latest", system, user,
            temperature=0.3, max_tokens=400, json_mode=False,
        )
        instruction = (resp.content or "").strip().strip('"').strip("'")
        return {"question": instruction}
    except gateway.GatewayError as exc:
        raise HTTPException(503, f"LLM call failed: {exc}")


@app.post("/api/screening/parse-eppi")
async def parse_eppi(request: Request, file: UploadFile):
    """Parse an EPPI-Reviewer Excel export."""
    _require_auth(dict(request.headers))
    import tempfile, re
    import pandas as pd

    try:
        raw = await _read_upload_capped(file)
        suffix = ".xlsx" if (file.filename or "").lower().endswith((".xlsx", ".xls")) else ".csv"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        try:
            df = pd.read_excel(tmp_path, engine="calamine") if suffix != ".csv" else pd.read_csv(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        col_map = {c.lower().strip(): c for c in df.columns}

        # --- Decision column detection (robust, column-name-independent) ---
        # 1. Try common EPPI export names (case-insensitive)
        _KNOWN_DECISION_NAMES = [
            "ta_decision", "ft_decision", "decision", "screening_decision",
            "include_exclude", "status", "screen_decision",
        ]
        decision_col: str | None = None
        for name in _KNOWN_DECISION_NAMES:
            if name in col_map:
                decision_col = col_map[name]
                break

        # 2. Fallback: scan every column for one whose values are mostly INCLUDE/EXCLUDE
        if decision_col is None:
            best_col, best_score = None, 0.4  # require >40% match to avoid false positives
            for col in df.columns:
                try:
                    vals = df[col].dropna().astype(str).str.upper()
                    if len(vals) < 2:
                        continue
                    score = (vals.str.startswith("INCLUDE") | vals.str.startswith("EXCLUDE")).sum() / len(vals)
                    if score > best_score:
                        best_score, best_col = score, col
                except Exception:
                    continue
            decision_col = best_col

        if decision_col is None:
            all_cols = list(df.columns)
            raise HTTPException(
                422,
                f"Could not detect the screening decision column. "
                f"Available columns: {all_cols}. "
                f"Rename your decision column to 'ta_decision' (TA) or 'ft_decision' (FT)."
            )
        # ----------------------------------------------------------------

        id_col = col_map.get("u1") or col_map.get("record_id") or col_map.get("id") or col_map.get("eppi_id")
        decisions = df[decision_col].dropna().astype(str).str.strip()
        include_count = int(decisions.str.upper().str.startswith("INCLUDE").sum())
        exclude_count = int(decisions.str.upper().str.startswith("EXCLUDE").sum())
        total = int(len(df))

        tag_counts: dict[str, dict] = {}
        for val in decisions:
            if val.upper().startswith("EXCLUDE"):
                rest = val[7:].strip()
                tag = re.sub(r"[^a-z0-9]+", "_", rest.lower()).strip("_")
                if tag:
                    if tag not in tag_counts:
                        tag_counts[tag] = {"tag": tag, "label": rest, "count": 0}
                    tag_counts[tag]["count"] += 1

        tags = sorted(tag_counts.values(), key=lambda x: -x["count"])
        return {
            "total": total,
            "include_count": include_count,
            "exclude_count": exclude_count,
            "tags": tags,
            "decision_col": decision_col,   # tell the UI which column was detected
            "id_col": id_col,
            "has_abstract": "ab" in col_map or "abstract" in col_map,
            "has_title": "t1" in col_map or "title" in col_map,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("parse-eppi failed")
        raise HTTPException(500, "Failed to parse the EPPI export.")


@app.post("/api/projects/{project_slug}/process-eppi")
async def process_eppi(request: Request, project_slug: str, file: UploadFile):
    """Process an EPPI-Reviewer Excel export for a screening project:
    saves T1+AB as markdown corpus files, stores records in DB, and
    parses ta_decision → ground_truth table."""
    _require_auth(dict(request.headers))
    _project_or_404(project_slug)

    import tempfile, re
    import pandas as pd

    raw = await _read_upload_capped(file)
    suffix = ".xlsx" if (file.filename or "").lower().endswith((".xlsx", ".xls")) else ".csv"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        df = pd.read_excel(tmp_path, engine="calamine") if suffix != ".csv" else pd.read_csv(tmp_path)
    except Exception as exc:
        raise HTTPException(422, f"Could not read file: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    col = {c.lower().strip(): c for c in df.columns}
    title_col    = col.get("t1") or col.get("title")
    abstract_col = col.get("ab") or col.get("abstract")
    id_col       = col.get("u1") or col.get("record_id") or col.get("id")
    decision_col = col.get("ta_decision") or col.get("decision")
    author_col   = col.get("a1") or col.get("author")
    year_col     = col.get("py") or col.get("year")

    if decision_col is None:
        raise HTTPException(422, "ta_decision column not found.")

    corpus_dir = config.PROJECTS_DATA_DIR / project_slug / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        stored_records = stored_gt = skipped = 0

        for idx, row in df.iterrows():
            raw_id = str(row[id_col]).strip() if id_col and pd.notna(row.get(id_col)) else None
            if raw_id and raw_id not in ("nan", ""):
                record_id = _stable_id(raw_id)
            else:
                # No usable ID column → derive from the row index. Prefix so a
                # bare index can never collide with a real numeric record_id.
                record_id = _stable_id(f"row-{idx}")

            title    = str(row[title_col]).strip()    if title_col    and pd.notna(row.get(title_col))    else ""
            abstract = str(row[abstract_col]).strip() if abstract_col and pd.notna(row.get(abstract_col)) else ""
            author   = str(row[author_col]).strip()   if author_col   and pd.notna(row.get(author_col))   else ""
            year     = str(row[year_col]).strip()     if year_col     and pd.notna(row.get(year_col))     else ""

            if not title and not abstract:
                skipped += 1
                continue

            # TA screening corpus = title + abstract only (no full PDF needed)
            md_text = f"# {title}\n\n"
            if author: md_text += f"**Authors:** {author}  \n"
            if year:   md_text += f"**Year:** {year}  \n\n"
            if abstract: md_text += f"## Abstract\n\n{abstract}\n"

            stem = re.sub(r"[^a-z0-9]+", "_", title[:50].lower()).strip("_") or str(record_id)
            out_path = corpus_dir / f"{record_id}_{stem}.md"
            out_path.write_text(md_text, encoding="utf-8")

            conn.execute(
                "INSERT OR REPLACE INTO records (project_id, id, title, md_path) VALUES (?, ?, ?, ?)",
                (project_id, record_id, title[:255], str(out_path)),
            )
            stored_records += 1

            decision_raw = str(row[decision_col]).strip() if pd.notna(row.get(decision_col)) else ""
            if not decision_raw or decision_raw.lower() == "nan":
                continue
            if decision_raw.upper().startswith("INCLUDE"):
                gt_value = json.dumps({"decision": "INCLUDE", "tag": ""})
            elif decision_raw.upper().startswith("EXCLUDE"):
                tag_label = decision_raw[7:].strip()
                tag = re.sub(r"[^a-z0-9]+", "_", tag_label.lower()).strip("_")
                gt_value = json.dumps({"decision": "EXCLUDE", "tag": tag, "tag_label": tag_label})
            else:
                gt_value = json.dumps({"decision": "MAYBE", "tag": "", "raw": decision_raw})

            conn.execute(
                "INSERT OR REPLACE INTO ground_truth "
                "(project_id, record_id, field_name, value_json) VALUES (?, ?, 'screening_decision', ?)",
                (project_id, record_id, gt_value),
            )
            stored_gt += 1

    return {"records_stored": stored_records, "ground_truth_stored": stored_gt, "skipped": skipped}


@app.post("/api/projects/{project_slug}/launch")
async def launch_extraction(request: Request, project_slug: str):
    """Kick off the first extraction/screening run for a newly created project."""
    _require_auth(dict(request.headers))
    _project_or_404(project_slug)

    import subprocess, sys as _sys
    cfg = {}
    project_type = "extraction"
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT config_json, project_type FROM projects WHERE slug = ?", (project_slug,)
        ).fetchone()
        if row and row["config_json"]:
            cfg = json.loads(row["config_json"])
            project_type = row["project_type"] or "extraction"

    selected_models = cfg.get("selected_models", [])
    if not selected_models:
        raise HTTPException(400, "No models selected. Add selected_models to project config.")

    jobs = []
    is_screening = project_type in ("screening_ta", "screening_ft")

    if is_screening:
        # One run_screening subprocess per model (it handles all criteria internally)
        models_arg = ",".join(selected_models)
        cmd = [
            _sys.executable, "-m", "backend.scripts.run_screening",
            "--project", project_slug,
            "--models", models_arg,
            "--n", "100",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        jobs.append({"type": "screening", "models": selected_models, "pid": proc.pid})
    else:
        project = _project_or_404(project_slug)
        for field_name in project.fields:
            for model_id in selected_models:
                cmd = [
                    _sys.executable, "-m", "backend.scripts.run_extraction",
                    "--project", project_slug,
                    "--field", field_name,
                    "--models", model_id,
                    "--n", "100",
                ]
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                jobs.append({"field": field_name, "model": model_id, "pid": proc.pid})

    return {"launched": len(jobs), "jobs": jobs}
