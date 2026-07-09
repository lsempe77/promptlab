# PromptLab — agent instructions

This is the **promptlab** repo (github.com/lsempe77/promptlab): a React+Vite+TS **dashboard** at the
root, a Python/FastAPI **backend** in `backend/`, and an **autonomous prompt-optimization engine**
(the supervisor daemon). It extracts structured metadata from impact-evaluation studies for 3ie's
DEP, scores every model against human-curated ground truth, and improves prompts on its own.

> An **older, stale copy** of `backend/` also lives in the sibling `../DEP` folder — ignore it;
> `promptlab/backend` is the authoritative, deployed code. (Because promptlab is now the workspace
> root, `grep_search`/`file_search` work correctly here — the old "grep only sees the DEP stale
> copy" problem is gone.)

## Build / run
- **Python venv lives at `../DEP/.venv`** (NOT in promptlab). Run backend modules from the promptlab
  root as a package: `& "..\DEP\.venv\Scripts\python.exe" -m backend.scripts.<name>` (relies on
  package-relative imports; the scripts also `sys.path.insert` the repo root). Set
  `$env:DEP_DB_PATH` to point at a DB when running against one. (Or make a local `.venv`.)
- **Frontend:** `npm run dev` → http://localhost:5173/promptlab/ . Build/type-check: `npm run build`.
- **Local API:** `python -m backend.scripts.serve` → http://127.0.0.1:8000 .
- The DB + corpus are **not** in the repo (gitignored). A local production subset is built by
  `python -m backend.scripts.export_production_subset` → `backend/deploy/{promptlab.db,corpus/}`.

## Current production state (as of 2026-07-09 — Phase 2 Postgres deployed)
- **Deployed on Fly.io**: app `dep-promptlab-api`, region `iad`, machine `82547dc7995668`, volume
  `dep_data` at `/data`, `min_machines_running=1`, `memory=4096mb` (performance-2x, 2 CPUs). Env:
  `DEP_DB_PATH=/data/promptlab.db`, `DEP_MD_DIR=/data/corpus`. Secrets: `OPENROUTER_API_KEY`,
  `JWT_SECRET`, `PROMPTLAB_PASSWORD`, `DATABASE_URL` (Neon Postgres).
- **Fresh start executed 2026-07-08:** all runs/iterations/jobs wiped. 5×100 GT records + 5 v1
  shared baselines preserved. Backup: `../DEP/backups/promptlab_prod_20260708_214047.db`.
- **12-model roster** (retired: glm-4.7-flash, gemini-pro-latest, kimi-k2.5, kimi-latest,
  llama-4-scout, gpt-latest). See `backend/models.yaml`.
- **Stages: (100,)** only. `MAX_PRODUCTION_RECORDS=200`, `PRODUCTION_ROLLOUT_STAGES=(100,)`.
- **Gate: F1 ≥ 0.90 AND recall ≥ 0.85** for list fields; accuracy ≥ 0.90 for categorical.
- **Advancement**: best model passes gate → advance (not "all must pass").
- **Phase 2 Postgres (Neon):** `backend/app/db_pg.py` — all runs/iterations/judgments/jobs/tasks
  mirror to Postgres when `DATABASE_URL` is set. SQLite stays authoritative for coordinator-owned
  tables (records, ground_truth, prompt_versions, projects).
- **Running daemons** (restart after every `fly deploy`): supervisor PID 679, workers PIDs 691–694.
  Pre-wipe backup at `../DEP/backups/promptlab_prod_20260708_214047.db`.
- Dashboard + docs are merged to `main` → live on GitHub Pages.

## Architecture — two loops + governance (do not weaken)
- **Loop A (supervisor + workers, autonomous):** optimizes **prompts** within the gate.
  `scripts/supervisor.py` (coordinator daemon) decides each action and **enqueues tasks** to Postgres
  (`worker_tasks` table). `scripts/worker.py` (4 workers) poll the queue with `FOR UPDATE SKIP LOCKED`
  and execute `run_extraction` / `llm_judge` / `optimize_prompt` in parallel.
  When `DATABASE_URL` is not set, supervisor falls back to direct shell-out (Phase 1 mode).
- **Loop B (planned):** audits ground truth (`scripts/audit_ground_truth.py`,
  `scripts/propose_gt_fixes.py`, both read-only), proposes **data** fixes for **human signed
  approval**.
- **HARD RULE:** the agent moves **prompts** autonomously; **data** (ground truth / taxonomy) changes
  are human-approved; **code / eval-logic** changes go through **GitHub PR → human review → `fly
  deploy`**. Never let the agent edit its own scoring code or answer key (reward-hacking surface).
- **`ROADMAP.md` (repo root) is the canonical forward-looking doc;** `backend/README.md` and this
  file point to it. Current lasting state → READMEs. Session/task-in-progress state → repo memory.

## Deploy
- **Frontend** → GitHub Pages on push to `main` (`git push origin <branch>:main`, or push `main`).
- **Backend** → `fly deploy` from the **promptlab repo root** (Dockerfile + fly.toml are here; the
  image ships `backend/app`, `backend/scripts`, `models.yaml` — the DB + corpus stay on the `/data`
  volume, so redeploys don't re-upload data).
- **A deploy restarts the machine → kills all daemon processes** (`nohup` processes do NOT survive
  a machine restart; only `/data` persists; uvicorn auto-restarts but daemons do not). After
  deploying, **relaunch**: `fly ssh console -C "sh /data/launch_all.sh"` (starts supervisor + 4 workers);
  verify with `sh /data/list_procs2.sh`; stop with `sh /data/kill_all.sh`.

## Stop rules / guardrails (don't remove without asking the user)
- `config.MAX_PRODUCTION_RECORDS = 200`, `config.PRODUCTION_ROLLOUT_STAGES = (100,)` —
  `run_extraction.py` clamps `--n` and warns if exceeded.
- Optimizer: `no_improve_limit` default **4**, `bold_after` **2**, `val_size` **50** (clamped to
  ~35 on 100 GT records), `holdout_size` **30**, `IMPROVEMENT_EPSILON` **0.03** (list fields) /
  **0.01** (categorical). Deterministic (`seed=42`), self-terminating batch jobs.
- `scoring.GATE_THRESHOLD = 0.90` (primary); `scoring.RECALL_FLOOR = 0.85` (hard floor for list
  fields — prevents F1 gaming at the expense of recall).

## Metrics quick reference
- **Gate metric** (`app/analytics.gate_metrics`): **F1** for list fields (`authors`,
  `author_affiliation`, `author_country`), **accuracy** for categorical (`sector_name`,
  `sub_sector`), ≥ 0.90 per (field, model). Scoring is **concordance-aware** (accent/mojibake folding
  + transliteration, fuzzy match, `"A | B"` ground truth = either accepted). Cohen's **κ** reported
  for categorical.
- **LLM-judged accuracy** (`app/judging.py`, cross-family judge) is a **reported companion**, not the
  gate. The optimizer accepts a rewrite only if it raises the gate metric on the 50-paper val **and**
  holds on a cross-model holdout (blocks single-model overfits); after 2 rejects it goes **bold**
  (structural rewrites).
- The dashboard tiers metrics: Quality (gate) → precision/recall or κ → concordance → honesty →
  cost/CO₂e (EcoLogits per-run estimate).

## Gotchas
- **PowerShell shows red `NativeCommandError` for git/fly stderr even on success (exit code 1)** —
  read the actual output, don't trust the exit code for git/fly.
- **`fly ssh console -C "..."`** breaks on pipes / nested quotes / `$!` — put complex remote commands
  in a `.sh`, upload with `fly ssh sftp put <local> /data/x.sh`, run `fly ssh console -C "sh
  /data/x.sh"`. **sftp REFUSES to overwrite** an existing remote file — `rm -f` it first. The
  container has **no `ps`/`pkill`** — use the `/proc`-scan helpers on `/data`:
  `list_sup.sh` (supervisor only), `list_procs2.sh` (all Python), `kill_all.sh`, `launch_all.sh`
  (supervisor + 4 workers), `launch_supervisor.sh` (supervisor only).
- **Back up / inspect the prod DB read-only:** `fly ssh sftp get /data/promptlab.db <local>`, then
  run diagnostics against the copy. Never point write-scripts at the live volume DB casually.
- OpenRouter **`~author/family-latest`** aliases require the literal leading `~`.
- `run_extraction.py` commits each `(record, model)` result to SQLite as it completes (crash-safe)
  and **skips already-done pairs**, so an interrupted run resumes cleanly.
- `corpus.read_md()` falls back to `config.MD_DIR`/`DEP_MD_DIR` + filename if `records.md_path`
  doesn't resolve on the current machine.
- Schema migrations: always test against a **copy** of the real DB. `db.init_db()` runs
  SCHEMA → `_migrate_to_multi_project` → additive `_migrate` (adds columns like `model_id`,
  `co2e_grams`) → SCHEMA_INDEXES → `sync_projects`.

## In-progress WIP (uncommitted; not to be discarded)
- A **guided "Tour"/walkthrough** feature is half-built and uncommitted in the working tree:
  `src/components/Walkthrough.tsx` (untracked), a `useWalkthrough` hook + `id="tour-*"` anchors in
  `src/App.tsx`, `.tab-btn.tour-btn` CSS in `src/App.css`, and a new dependency in
  `package.json`/`package-lock.json`. Finish it, `npm run build`, then commit + push.
- **Phase 2 Postgres** is fully deployed. `_holdout_generalization` in `optimizer.py` still calls
  `evaluate_instruction` without `pg_conn` — holdout runs go to SQLite only (minor, non-blocking).

## Open items
- **Strategy (leadership):** publish the method vs. keep it for BD/cost advantage — parked in
  `ROADMAP.md`.
- **Roadmap highlights:** confidence-based model triage/cascade; shared base-prompt library + per-
  project overlays; automated human-review queue (Loop B); user-selectable evidence-based
  thresholds; prompt caching (record-major); a validation study for the human-on-the-loop claim.

> Note: repo *memory* (`/memories/repo/`) is workspace-scoped and does **not** follow you from the
> DEP workspace to this one — the durable facts have been captured here instead. Ask the agent to
> re-establish session/task notes in the new workspace's repo memory if you want them.
