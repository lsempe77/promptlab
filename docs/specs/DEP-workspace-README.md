# DEP workspace

This folder hosts two things:

1. **`backend/`** — the Agentic 3ie Prompt Lab backend (FastAPI + SQLite, OpenRouter model
   gateway, GEPA-lite prompt optimizer). This is the actively developed project. See
   [`backend/README.md`](backend/README.md) for setup/architecture, and
   [`.github/copilot-instructions.md`](.github/copilot-instructions.md) for repo conventions,
   build/run commands, and known gotchas.
2. Reference/archived material from earlier one-off work on this dataset (see below).

## Folder structure

- `backend/` — Prompt Lab backend (active). A copy also now lives inside the `promptlab` repo
  (`../promptlab/backend/`, on `main`) as part of a monorepo merge — see repo memory / backend
  README for the current status of that migration.
- `prompt_lab/` — source protocol/prompt reference docs (`prompts-extraction.md`,
  `prompts-screening.md`, `prompts-search-synthesis-protocol.md`) that `backend/app/prompts.py`
  is written to follow. Keep in sync with the coding protocol if it changes.
- `docs/` — `DEP_LLM_Roadmap.*` — the broader LLM roadmap document for this dataset/project.
- `emails/` — generator scripts + output for status-update emails (`make_promptlab_email_docx.py`
  → `promptlab_intro_email.docx`).
- `archive/title-consistency-check/` — a completed one-off QA pass (title-vs-PDF-content
  consistency check across the ~8,925-file corpus, using `title_check_v3.py`). Not part of the
  Prompt Lab; kept for reference only. Includes the original session README
  (`README_qa-session-2026-07-04.md`) describing what that pipeline does and its results.
- `archive/graphify-out/` — generated code-mapping artifacts from an earlier Graphify run over
  this workspace (`graph.json`, `GRAPH_TREE.html`) — regenerable, kept for reference only.
- `archive/reference-data/` — `db-sample.xlsx`, not referenced by any code; kept for reference
  only (moved out of root since its purpose wasn't tied to any active script).
- `1770900869-ier-records.xlsx`, `DEP extraction protocol for IE v4- Admin
  panel.xlsx` — source reference data used to build the Prompt Lab's ground truth
  (`backend/scripts/build_ground_truth.py`, `backend/scripts/extract_taxonomy.py`). These were
  also duplicated inside `prompt_lab/` — removed there since nothing referenced that copy; the
  root copies here are the ones actual code paths point to.
- `Dockerfile`, `fly.toml`, `.dockerignore` — Fly.io deployment config for the backend API (see
  backend README's "Production deployment" section).

## Where to start

For anything related to the Prompt Lab (extraction, prompt optimization, the dashboard, Fly
deployment), start with [`backend/README.md`](backend/README.md).
