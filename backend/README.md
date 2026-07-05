# Agentic 3ie Prompt Lab — backend

Agentic system to iteratively refine LLM prompts for 3ie's evidence-synthesis pipeline
(TAS -> FTS -> data extraction). Multi-project aware (see "Multi-project support" below): today
only one project is registered, `dep-extraction`, covering the **data extraction** step for 5
metadata fields: `authors`, `author_affiliation` (institution), `author_country`, `sector_name`,
`sub_sector`.

Stack: FastAPI + SQLite (no task queue), OpenRouter as a single unified model gateway (free ->
expensive tiers), plateau-based stopping ("stop after N iterations with no improvement") for the
prompt optimizer.

## Setup

```
cd backend
copy .env.example .env      # then edit .env and paste your OpenRouter API key
cd ..
.venv\Scripts\python.exe -m backend.scripts.build_ground_truth
.venv\Scripts\python.exe -m backend.scripts.run_extraction --field sector_name --n 5 --tiers free
```

## Architecture

- **Projects** (`app/projects.py`): a `ProjectSpec` (slug, name, description, fields) registers a
  synthesis project — today just `dep-extraction`. Adding a new project (HSF, Girl Effect,
  StrongMinds) means adding its own `FieldSpec` dict (mirroring `app/fields.py`) and registering a
  `ProjectSpec` for it in `PROJECTS` — no schema changes needed. `db.sync_projects()` upserts the
  Python registry into the `projects` table on every `init_db()` call, so a new project just needs
  a code change + restart. Every other table (`records`, `ground_truth`, `prompt_versions`,
  `runs`, `iterations`, `jobs`) is scoped by a `project_id` FK, so each project has fully
  independent corpus/ground-truth/prompt/run history while sharing the same DB file and API.
- **Ground truth**: `1770900869-ier-records.xlsx` joined against the QA'd markdown corpus
  (`..._ok_only_final`, files named `<id>.md`) by numeric `id`. 7,675 studies have both. Build/
  refresh with `python -m backend.scripts.build_ground_truth`, which writes to
  `backend/data/promptlab.db` (SQLite; gitignored).
- **Taxonomy**: sector/sub-sector/country controlled vocabularies extracted from the protocol
  workbook's `Lists` sheet into `backend/app/data/taxonomy.json`. Regenerate with
  `python -m backend.scripts.extract_taxonomy`.
- **Model gateway** (`app/gateway.py`): one `call_model(model_id, system, user, ...)` function
  against OpenRouter's OpenAI-compatible endpoint. Requires `backend/.env` with
  `OPENROUTER_API_KEY` (copy `.env.example`; never commit the real key).
- **Model roster** (`models.yaml`): where available, paid tiers use OpenRouter's
  `~author/family-latest` alias resolution (leading `~` is part of the literal model id) so the
  roster picks up each provider's newest release automatically. No alias exists yet for Grok,
  GLM, Mistral, DeepSeek, Qwen, or Meta-Llama — those stay pinned and need refreshing by hand.
- **Prompt templates** (`app/prompts.py`): v1 baseline templates per field (anchor/excerpt before
  value, typed placeholders, one null convention, `<paper>` instruction/data separation with an
  injection guard).
- **Scorer** (`app/scoring.py`): field-type aware — exact/fuzzy match for single categorical
  fields (sector, sub-sector), set-based F1 with fuzzy name matching for list fields (authors,
  institutions), exact set match for list-categorical (countries).
- **Run harness** (`scripts/run_extraction.py`): samples N ground-truthed records, runs every
  configured model (`models.yaml`) against the current baseline/accepted prompt, scores + stores
  every run in SQLite, prints a comparison table. Every script that touches the DB accepts
  `--project <slug>` (default `dep-extraction`). Example:
  ```
  python -m backend.scripts.run_extraction --field sector_name --n 20 --tiers free,cheap
  python -m backend.scripts.run_extraction --field authors --n 15 --models openai/gpt-4o-mini,anthropic/claude-3-5-haiku
  ```
- **Optimizer loop** (`app/optimizer.py`): GEPA-lite — evaluate on a minibatch, reflect on
  wrong/low-score runs (avoiding previously-tried dead ends), propose up to N candidate revisions
  per iteration (best-of-N), validate each on a held-out set, accept only the winner if it beats
  the incumbent by more than `IMPROVEMENT_EPSILON`, stop after N iterations with no improvement.
  Run via `scripts/optimize_prompt.py` (single field+model+reflector at a time) or
  `scripts/optimize_all.py` (sweeps every field x model combination in one run, picking a
  cross-family reflector automatically — Anthropic models are reflected on by `~openai/gpt-latest`,
  everything else by `~anthropic/claude-opus-latest`, so a model is never self-critiqued by a
  same-family model; one failing pair doesn't stop the sweep).
- **FastAPI app** (`app/api.py`, read-only): every field-scoped route is nested under a project
  slug: `/api/projects`, `/api/projects/{p}/fields`, `/api/projects/{p}/fields/{f}/prompt-versions`,
  `/api/projects/{p}/fields/{f}/models-summary`, `/api/projects/{p}/fields/{f}/runs`,
  `/api/projects/{p}/fields/{f}/iterations`, `/api/projects/{p}/fields/{f}/confusion`,
  `/api/projects/{p}/fields/{f}/jobs`, plus the project-agnostic `/api/config/thresholds`. Run
  with `python -m backend.scripts.serve` (http://127.0.0.1:8000).
- **Job tracking** (`db.start_job`/`update_job_progress`/`finish_job`, `jobs` table): both
  `run_extraction.py` and `optimize_prompt.py` record a `jobs` row (per field+model) when they
  start and mark it completed/failed when they stop, so the dashboard can show a "currently
  running" banner + per-model badge while a batch is in flight. `run_extraction.py` fires every
  (record, model) call in one big concurrent batch, so its progress only ever jumps 0 -> total (no
  granular per-record updates); `optimize_prompt.py` updates progress once per iteration. A
  "running" job whose `updated_at` is older than `db.JOB_STALE_AFTER_SECONDS` (5 min) is reported
  by the API as `stale: true` instead of trusted at face value, in case the script crashed/was
  killed without ever calling `finish_job`.
- **Observability frontend**: separate repo/deploy (`promptlab`, React + Vite), fetches from the
  FastAPI app above (either the local dev server, or the always-on Fly.io deployment — see
  "Production deployment" below). CORS is opened for both the Vite dev origin and the deployed
  GitHub Pages origin. Polls `/api/fields/{f}/jobs` every 6s while a field is selected to drive
  the running-job banner/badges.

## Data model (SQLite)

`projects(id, slug, name, description, created_at)` ·
`records(project_id, id, title, md_path, PRIMARY KEY(project_id, id))` ·
`ground_truth(project_id, record_id, field_name, value_json)` ·
`prompt_versions(id, project_id, field_name, version, template, parent_id, notes, accepted,
created_at)` · `runs(id, project_id, prompt_version_id, model_id, record_id, field_name,
raw_response, parsed_value_json, score, is_correct, latency_ms, prompt_tokens, completion_tokens,
cost_usd, error, batch_id, created_at)` · `iterations(id, project_id, field_name, iteration_num,
prompt_version_id, model_id, mean_score, n_records, feedback, accepted, created_at)` ·
`llm_judgments(id, run_id, judge_model, verdict, reasoning, created_at)` · `jobs(id, project_id,
field_name, model_id, kind, status, total, completed, started_at, updated_at, finished_at,
error)`.

An older single-project DB (no `project_id` columns) is migrated automatically the first time
`db.init_db()` runs against it — see `db._migrate_to_multi_project`: adds the `projects` table,
backfills every existing row into a `dep-extraction` project (id=1), and rebuilds
`records`/`ground_truth`/`prompt_versions`/`runs`/`iterations`/`jobs` with the new project-scoped
keys. No data is lost (verified: row counts before/after match exactly); it's a no-op once
already migrated.

## Production deployment (Fly.io)

The dashboard is meant to keep working even when the developer's laptop is off, so the API is
deployed as an always-on Fly.io app serving a **fixed, one-time production dataset** — not the
full 7,675-record local corpus, and not a recurring/scheduled extraction job. Both
`run_extraction.py` and `optimize_prompt.py` are deterministic, self-terminating batch scripts
(fixed `seed=42` sampling, early-stop via `no_improve_limit`); re-running them on a schedule
without changing inputs just re-processes the same records, so there's no cron job here — the
production dataset is built once, by hand, then served read-only forever after.

Files: `Dockerfile`, `fly.toml`, `.dockerignore` at the DEP root (repo root for `fly deploy`'s
build context). The image contains only `backend/app`, `backend/scripts`, `backend/models.yaml`
— **not** the database or corpus, which live on a persistent Fly volume mounted at `/data`, kept
separate so redeploying code never requires re-uploading ~50MB of data.

`backend/app/api.py` never calls the model gateway (it's read-only), so **no `OPENROUTER_API_KEY`
secret is needed on Fly** — the OpenRouter key only ever lives in the developer's local
`backend/.env`, used to build the dataset before it's uploaded.

Building the production dataset (run locally):
```
python -m backend.scripts.export_production_subset
# regenerates backend/deploy/{promptlab.db,corpus/} -- 300 complete-case records
# (config.MAX_PRODUCTION_RECORDS), md_path pointed at the local corpus/ folder so you
# can immediately run a real rollout against it:
$env:DEP_DB_PATH = "<repo>\backend\deploy\promptlab.db"
python -m backend.scripts.run_extraction --field <field> --n 300 --tiers free,cheap,mid,expensive
# repeat per field: authors, author_affiliation, author_country, sector_name, sub_sector
```

Once the local rollout is done and you're ready to actually ship it, rewrite the paths to match
the Fly volume mount before uploading:
```
python -m backend.scripts.rewrite_corpus_path_for_deploy --db backend/deploy/promptlab.db --target-dir /data/corpus
```

One-time Fly setup (from the DEP root):
```
fly auth login
fly launch --no-deploy --copy-config --name <app-name>
fly volumes create dep_data --size 1 --region <region matching fly.toml primary_region>
fly deploy
fly ssh sftp shell   # or `fly ssh console` + scp, to upload promptlab.db + corpus/ into /data
```
Later code-only changes: `fly deploy`. Later data refreshes: repeat the export -> rollout ->
rewrite-paths -> upload sequence above and redeploy (or just re-upload to the existing volume).

Running extraction jobs directly on the Fly machine (so they survive closing your laptop): upload
`OPENROUTER_API_KEY`/`OPENROUTER_BASE_URL` as Fly secrets (`Get-Content backend\.env | fly secrets
import --app <app-name>` — pipes the key in without ever printing it), then kick off
`python -m backend.scripts.run_extraction ...` via `fly ssh console -C "sh /data/<script>.sh"`
(upload a small `.sh` file via `fly ssh sftp shell` first — inline quoting through `fly ssh console
-C` reliably breaks on nested quotes). Check progress later with a
`SELECT COUNT(*) FROM runs WHERE field_name=?` query against `/data/promptlab.db`, run the same
way. **Memory**: the default `shared-cpu-1x`/512MB machine OOM-restarted (silently killing the
background job, no error, just gone) the moment an extraction job ran concurrently with the API
server — bumped to 1024mb in `fly.toml` to fix. A plain `nohup ... &` background job does **not**
survive a Fly machine restart (restarts wipe the whole container; only `/data` persists) — unlike
a local crash, there's no automatic resume, so just re-run the command if a restart happens again.

## Roadmap

- **Multi-project support (backend done, frontend not started)**: on `feature/multi-project`.
  Backend is fully project-scoped now (see "Projects" in Architecture above and the Data model
  section) — schema migration, `app/projects.py` registry, `/api/projects/...`-nested API, and
  `--project` CLI flag on every data-touching script are all done and tested locally (migration
  verified to preserve all existing rows exactly; API smoke-tested end-to-end). Still only one
  project is registered (`dep-extraction`). Remaining work: (1) frontend project switcher +
  `api.ts` updated to call the new nested URLs (currently still calls the old un-nested
  `/api/fields/...` routes, so the deployed frontend will break against this branch's API until
  that's done), (2) `app/prompts.py`/`scoring.py`/`taxonomy.py` are still hardcoded to the single
  `fields.FIELDS` dict rather than being project-aware — needs generalizing when the second real
  project (HSF/Girl Effect/StrongMinds screening) is actually added, (3) merge to `main` and
  deploy once the current production rollout finishes (don't touch the live Fly DB schema while
  a rollout is actively writing to it).
- **Prompt caching (planned, not started)**: the `<paper>` document block is already the stable
  prefix in every prompt (see `prompts.build_prompt`), and most providers OpenRouter proxies to
  (OpenAI, Gemini 2.5, DeepSeek, Grok) cache a shared prefix automatically, with Anthropic/Qwen
  needing an explicit `cache_control: {"type": "ephemeral"}` marker on that block — cached reads
  cost 10-50% of normal input price depending on provider. The catch: cache TTL is only ~5 min
  (up to 1h for Anthropic), but `run_extraction.py` currently runs *field-major* (all 300 records
  for one field, then the next field), so the same record+model's 5 field calls are hours apart
  and never hit a warm cache. To benefit, execution would need to go *record-major* (loop each
  record, call all 5 fields back-to-back per model) instead. **Before committing to this
  refactor, benchmark it against the current single-request-per-field baseline** — run a small
  side-by-side comparison (same sample of records/models) measuring actual cost via
  `usage.prompt_tokens_details`/`cache_discount` and wall-clock time, record-major+caching vs.
  today's field-major approach, to confirm the savings are worth the iteration-order rewrite
  before changing `run_extraction.py`.
- **LLM-judged accuracy surfaced in the dashboard, but needs a real sweep run (in progress)**:
  added a third accuracy metric (`GET /api/fields/{field}/llm-judge-summary`, a new stat card in
  `ModelCard.tsx`) sourced from `scripts/llm_judge.py`'s posterior semantic true/false verdicts —
  meant to be the most trustworthy of the three accuracy numbers shown (vs. threshold accuracy /
  exact-match accuracy, which are both just string-matching heuristics). This needs `fly deploy`
  to actually go live (deferred until the current production rollout finishes, since deploying
  restarts the machine and kills in-progress unattended jobs). It also currently only has data for
  `sector_name`/`authors` (40 judged references each, judge model `~openai/gpt-latest`) — TODO:
  once the rollout + optimizer sweep finish, run `llm_judge.py` for `author_affiliation`/
  `author_country`/`sub_sector` too, and consider a bigger sample size than 40 for all 5 fields so
  the metric is meaningful across more of the production data, not just a small judged subset.

## Known issues / follow-ups

- **Free-tier upstream rate-limiting**: some free-tier models (seen with `meta-llama/llama-3.3-70b-instruct:free`, `qwen/qwen3-coder:free`, occasionally `google/gemma-4-26b-a4b-it:free`) can get 100% 429-rate-limited by their upstream provider for a period, independent of anything in this codebase. `gateway.call_model()` already retries 3x with backoff on 429, but once a call fails all 3 retries it's logged as a permanent error row for that batch — there's no automatic later re-attempt. Use `python -m backend.scripts.retry_failed_runs --field <field> [--models a,b]` afterwards to re-run just the `(record, model)` pairs that have no successful run yet, once the outage clears.
- **Scoring thresholds not empirically derived**: `scoring.CORRECT_THRESHOLD` (0.9) and
  `scoring.FUZZY_MATCH_THRESHOLD` (95) are hand-picked. `scripts/llm_judge.py` runs a posterior
  LLM-as-judge pass over already-logged runs and reports, for a sweep of candidate thresholds, how
  well the automated scorer's correctness agrees with the LLM judge's verdicts — TODO: run this
  across all 5 fields with a larger sample and use the sweep to pick better-justified values.
  Example: `python -m backend.scripts.llm_judge --field sector_name --n 40 --judge-model openai/gpt-4o`
- **Model alias resolution isn't logged**: `gateway.call_model()` logs runs under whatever
  `model_id` was requested (e.g. `~openai/gpt-mini-latest`), not the concrete model OpenRouter
  actually resolved it to (`response["model"]`) — so the dashboard can't show *which* underlying
  model served an aliased run, or alert on version rollovers. TODO: capture `response["model"]`
  alongside `runs.model_id` if that visibility becomes important.
- **Corpus truncation fix doesn't retroactively apply**: the corpus is Tika-extracted from PDFs,
  so every `.md` file used to start with a large metadata block that could push real content past
  `prompts.MAX_CHARS`, silently starving `authors`/`author_affiliation`/`author_country` on
  multi-author papers (61% of a 150-record sample had the real content beyond the old 6000-char
  cutoff). Fixed by having `corpus.read_md()` skip straight to `<body>` and raising `MAX_CHARS` to
  10000 (validated: `author_country` accuracy went 44%->72% for gpt-4o-mini, 48%->88% for
  claude-opus-4.1 on a 25-record before/after check). Runs logged *before* the fix used the
  truncated text and are still averaged together with post-fix runs in the dashboard (no
  corpus-version marker to filter by) — TODO: either re-run full production batches for all 5
  fields so fresh runs dominate the aggregates, or add a corpus/prompt "generation" marker.
