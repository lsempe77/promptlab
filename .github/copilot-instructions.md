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

## Current production state (as of 2026-07-10)
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
- **Phase 2 Postgres (Neon):** `backend/app/db_pg.py` — runs/iterations/judgments/jobs/tasks
  mirror to Postgres when `DATABASE_URL` is set. SQLite stays authoritative for coordinator-owned
  tables (records, ground_truth, prompt_versions, projects).
- **Running daemons**: `entrypoint.sh` (the container CMD) auto-starts **one supervisor + one
  worker** (plus the uvicorn API) on every machine boot — so they are NOT started manually and must
  NOT be relaunched by hand after a normal deploy (a second supervisor double-enqueues work). The
  supervisor runs `--max-cycles 12 --interval 60` and self-terminates when it converges.
  Pre-wipe backup at `../DEP/backups/promptlab_prod_20260708_214047.db`.
- Dashboard + docs are merged to `main` → live on GitHub Pages.
- **`auto_stop_machines = 'off'`** in fly.toml — the machine never auto-stops (background daemons run 24/7).

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
- **A deploy restarts the machine, but `entrypoint.sh` re-starts the API + supervisor + worker
  automatically** (only `/data` persists; the processes are respawned by the container CMD, not by
  `nohup`). So a normal `fly deploy` needs **NO** manual relaunch.
  - **Do NOT** run `launch_all_new.sh` / `Launch-Daemons` after a deploy — the entrypoint has already
    started a supervisor, and a second one **double-enqueues work (wasted API spend)**. (This is the
    old, now-stale step; it predates the entrypoint auto-start.)
  - Verify: `fly ssh console -C "sh /data/list_procs2.sh"` — expect exactly **one** supervisor + one worker.
  - Relaunch manually **only** if the entrypoint set is gone (e.g. the supervisor exited after its
    `--max-cycles 12`). **Kill any existing set first** (`fly ssh console -C "kill <pids>"` or
    `/data/kill_all.sh`) so you never run two supervisors, then `fly ssh console -C "sh /data/launch_all_new.sh"`.

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
- **Work Saved at X% accuracy** (extraction analog of WSS@95%): fraction of extractions
  auto-acceptable at a given accuracy target, computed from `logprob_confidence` calibration bins.
  Displayed in the dashboard as the `WorkSavedChart` component.

## Gotchas
- **`fly ssh console -C "..."` on Windows always exits with code 1** (PowerShell `NativeCommandError` alert). This is a `flyctl` Windows SSH cleanup bug — the remote command DID succeed. To silence the alert, always append `; $LASTEXITCODE = 0` or wrap in `try { ... } catch {}`:
  ```powershell
  fly ssh console --app dep-promptlab-api -C "sh /data/launch_all_new.sh"; $LASTEXITCODE = 0
  ```
  The actual result is visible in the output (e.g. "LAUNCHED supervisor pid 690"). Ignore "Error: The handle is invalid." and exit code 1 — they are always noise.
- **Use `fly_helpers.ps1`** (in the repo root) for clean SSH commands: `. .\fly_helpers.ps1` then
  `Launch-Daemons`, `Show-Procs`, `Show-SupervisorLog`, `Kill-Daemons` — all suppress the noise automatically.
- **`fly deploy` / `git push` also show red `NativeCommandError`** for the same reason — read the actual output to determine success/failure, not the exit code.
- **`fly ssh console -C "..."`** breaks on pipes / nested quotes / `$!` — put complex remote commands
  in a `.sh`, upload with `fly ssh sftp put <local> /data/x.sh`, run `fly ssh console -C "sh
  /data/x.sh"`. **sftp REFUSES to overwrite** an existing remote file — `rm -f` it first. The
  container has **no `ps`/`pkill`** — use the `/proc`-scan helpers on `/data`:
  `list_sup.sh` (supervisor only), `list_procs2.sh` (all Python), `kill_all.sh`, `launch_all_new.sh`
  (supervisor + 4 workers, interval=300s).
- **Back up / inspect the prod DB read-only:** `fly ssh sftp get /data/promptlab.db <local>`, then
  run diagnostics against the copy. Never point write-scripts at the live volume DB casually.
- OpenRouter **`~author/family-latest`** aliases require the literal leading `~`.
- `run_extraction.py` commits each `(record, model)` result to SQLite as it completes (crash-safe)
  and **skips already-done non-errored pairs** (error IS NULL), so an interrupted run resumes cleanly
  and previously-errored records are retried.
- `corpus.read_md()` falls back to `config.MD_DIR`/`DEP_MD_DIR` + filename if `records.md_path`
  doesn't resolve on the current machine.
- Schema migrations: always test against a **copy** of the real DB. `db.init_db()` runs
  SCHEMA → `_migrate_to_multi_project` → additive `_migrate` (adds columns like `model_id`,
  `co2e_grams`) → SCHEMA_INDEXES → `sync_projects`.

## Current work / WIP
- **Paper draft in progress**: `paper/paper.qmd` (Quarto) — intro/background/methods/discussion/conclusion
  written; §4 results pending final optimization runs. Target: JDE special issue, deadline 30 Sep 2026.
  References in `paper/references.bib` (all CrossRef-verified).
- **Frontend rehaul (2026-07-10)**: `FieldOverview.tsx` (cross-field progress bars), `WorkSavedChart.tsx`
  (Work Saved curve), `LiveActivity.tsx` (queue + log tail), collapsed ModelCards, charts before table.
- **Phase 2 Postgres** is fully deployed. `_holdout_generalization` in `optimizer.py` still calls
  `evaluate_instruction` without `pg_conn` — holdout runs go to SQLite only (minor, non-blocking).
- **Tour/walkthrough** (`src/components/Walkthrough.tsx`): committed and working.

## Open items
- **Strategy (leadership):** publish the method vs. keep it for BD/cost advantage — parked in
  `ROADMAP.md`.
- **Roadmap highlights:** confidence-based model triage/cascade; shared base-prompt library + per-
  project overlays; automated human-review queue (Loop B); user-selectable evidence-based
  thresholds; prompt caching (record-major); a validation study for the human-on-the-loop claim.

> Note: repo *memory* (`/memories/repo/`) is workspace-scoped and does **not** follow you from the
> DEP workspace to this one — the durable facts have been captured here instead. Ask the agent to
> re-establish session/task notes in the new workspace's repo memory if you want them.
