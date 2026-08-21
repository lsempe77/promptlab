# DEP Prompt Lab — Agent Instructions

## Repo / workspace structure
- This VS Code workspace root (`DEP/`) is **not yet a git repo**. The only git repo today is the
  sibling folder `../promptlab` (React + Vite frontend, deployed to GitHub Pages).
- A monorepo merge is planned (move `backend/` into the `promptlab` repo) but has not happened
  yet — check `backend/README.md` and repo memory for current status before assuming it's done.
  Never move/rename the `backend/` directory while a production extraction run is actively
  writing to `backend/deploy/promptlab.db` (check `Get-Process python` / job status first).
- `backend/` = Python/FastAPI backend (this folder). `../promptlab/` = frontend, separate git
  history, separate deploy pipeline (GitHub Pages via `.github/workflows/deploy.yml`).

## Build / run
- Python env: `.venv` at the DEP root. Run scripts as `python -m backend.scripts.<name>` from the
  DEP root (not `python backend/scripts/x.py` — relies on package-relative imports).
- Frontend: `cd ../promptlab; npm run dev` (Vite, http://localhost:5173/promptlab/).
- Local API server: `python -m backend.scripts.serve` (http://127.0.0.1:8000).

## Production rollout stop rules (do not remove without asking the user first)
- `config.MAX_PRODUCTION_RECORDS = 100`, `config.PRODUCTION_ROLLOUT_STAGES = (50, 100)` —
  `run_extraction.py` clamps `--n` to this and warns if exceeded.
- Optimizer (`optimize_prompt.py`): `--max-iterations` default 10, `--no-improve-limit` default 3
  (the real practical brake — stops after 3 consecutive non-improving iterations),
  `IMPROVEMENT_EPSILON = 0.01`.
- Both scripts are deterministic, self-terminating batch jobs (fixed `seed=42` sampling) — there
  is deliberately **no cron/recurring schedule**; re-running without changing inputs just
  reprocesses the same records.

## Known gotchas
- `corpus.read_md()` falls back to `config.MD_DIR`/`DEP_MD_DIR` + filename if the literal
  `records.md_path` doesn't resolve on this machine (fixed — previously it read `md_path`
  literally with no fallback, so a Linux-style path baked in for a Fly volume would silently
  fail every record on Windows and vice versa).
- `run_extraction.py` now commits each `(record, model)` result to SQLite as soon as it completes
  (via `gateway.call_model_batch`'s `on_complete` callback), not just once at the very end of the
  whole batch (fixed — previously a crash/kill partway through a large batch lost every result
  already paid for, and the jobs table only ever showed 0 -> total with no real progress).
- PowerShell + inline Python (`-c "..."`) reliably mangles embedded double quotes — write a temp
  `.py` file and run it instead, then delete it.
- OpenRouter `~author/family-latest` aliases require the literal leading `~` character in the
  model id string.

## Production deployment (Fly.io)
- App `dep-promptlab-api`, region `iad`, persistent volume `dep_data` mounted at `/data`.
  Always-on (`min_machines_running = 1` in `fly.toml`) — this is a one-time data rollout, not a
  recurring redeploy.
- `backend/app/api.py` is read-only (never calls the model gateway) — **no `OPENROUTER_API_KEY`
  secret is needed on Fly**; the key only ever lives in local `backend/.env`.
- Full build -> rollout -> rewrite-paths -> upload -> deploy sequence is documented in
  `backend/README.md` under "Production deployment (Fly.io)" — follow that instead of improvising.

## Keep backend/README.md in sync
- When you fix something listed in its "Known issues / follow-ups" section, update/remove that
  entry instead of leaving it stale.
- Forward-looking plans / feature directions / architecture changes go in the repo-root
  `../promptlab/ROADMAP.md` (the canonical roadmap; `backend/README.md` points to it). When you
  plan or start a non-trivial piece of work, add/update a note there so it isn't only recorded in
  chat history or agent memory.
- Session/task-in-progress state (what's currently running, what step comes next) belongs in
  repo memory (`/memories/repo/`), not the README — the README should only describe the
  lasting, current state of the project.
