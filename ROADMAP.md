# Prompt Lab — Roadmap

Forward-looking plans and design decisions for the DEP Prompt Lab (backend + frontend).
This is the canonical roadmap; `backend/README.md` and the agent instructions point here.
Keep entries short; move anything that becomes "current lasting state" into the README instead.

## Multi-client deployment — per-Fly-app isolation (chosen: Option 3)

Each client project (GE, HSF, StrongMinds, new DEP screening, etc.) gets its **own Fly.io app**:
own machine, own `/data` volume, own supervisor, own URL. No shared DB, no tenant isolation
headaches, complete blast radius isolation between clients.

**Architecture:**
```
dep-promptlab-api.fly.dev        ← DEP extraction (production, always-on)
dep-promptlab-ge.fly.dev         ← GE screening (when provisioned)
dep-promptlab-hsf.fly.dev        ← HSF (when provisioned)
```

**Provisioning flow (today — semi-manual, ~10 min per client):**
1. Colleague fills in the wizard (project name, type, fields/criteria, corpus, GT)
2. Wizard outputs a `project_manifest.json` (or the backend stores it)
3. Lucas runs `python -m backend.scripts.provision_project --manifest project_manifest.json`
   — this calls flyctl to create the app, set secrets, deploy the image, create the volume,
   upload corpus + GT, and launch the supervisor
4. Colleague gets a URL and password

**Provisioning flow (planned — fully automated):**
- The wizard's "Launch" button calls the Fly Machines API directly from the backend
  (`POST https://api.machines.dev/v1/apps` + `POST .../machines`) — no Lucas required
- Requires storing a `FLY_API_TOKEN` secret; the provisioner waits for health check then
  redirects the user to their new dashboard URL

**Cost model:** ~$35/month per client app (performance-2x, 4GB RAM). Idle apps can be scaled
down to `shared-cpu-1x` between review cycles (~$5/month) and scaled back up on demand.

**Status:** provisioner script `backend/scripts/provision_project.py` — **planned, not built**.
The wizard's Step 5 currently posts to the existing DEP machine (`/api/projects`). The manifest
output and Fly API integration are the next engineering tasks for this branch.

## Evaluation metrics & the quality gate

**Done (2026-07-06):** the production gate is now field-type-aware and matches the
systematic-review evaluation literature (F1 for multi-value extraction; accuracy + kappa for
categorical):

- **List fields** (`authors`, `author_affiliation`, `author_country`) gate on **element-level F1**
  (balances precision & recall).
- **Single-categorical fields** (`sector_name`, `sub_sector`) gate on **accuracy**, with **Cohen's
  κ** (chance-corrected) reported alongside.
- **LLM-judged accuracy** is kept as a reported *concordance* companion, not the gate.
- Threshold lowered **0.95 → 0.90** (the human reference standard is itself noisy — "benchmark
  bias", up to ~63% of human extractions contain ≥1 error per Mathes 2017 — so 0.95 was chasing
  label noise; the literature commonly uses ~0.70–0.90). See `scoring.GATE_THRESHOLD`,
  `analytics.gate_metrics`.

**Planned — user-selectable, evidence-supported thresholds:** let the user set the gate
threshold (and possibly the gated *metric*) **per field**, rather than a single global 0.90. The UI
should support the choice with evidence — show the literature ranges (screening favours
recall/sensitivity ≥ ~0.95; extraction commonly ~0.70–0.90 F1), the current per-model
distribution, and the precision/recall trade-off — so the choice is informed by the user's own
error-cost preference (is a *wrong* value worse than a *missing* one?). Store thresholds per
`(project, field)`; treat a change as an eval-policy decision (human-gated, see Loop B).

**Done (2026-07-06) — optimizer acceptance aligned with the gate:** the optimizer now accepts a
rewrite on the **same field-type-aware gate metric** (`analytics.gate_metrics` — F1 for lists,
accuracy for categorical) on both the val set and the cross-model holdout, so "what the optimizer
chases" == "what the gate checks." We aligned to the deterministic/reproducible/cheap metric rather
than gating on the LLM judge; judged accuracy is kept only as a reported companion.

## Data-quality control loop ("Loop B")

A second autonomous loop alongside the prompt-optimizer supervisor ("Loop A"), for keeping the
*reference data* clean:

- Scheduled, **read-only** cloud audit (`scripts/audit_ground_truth.py` + `scripts/propose_gt_fixes.py`,
  both built) runs every *X* → emails the human a diff of proposed ground-truth/taxonomy corrections.
- On a **signed one-click approval** (NOT raw email-reply parsing), scoped to a hash of that exact
  changeset, it applies the approved **data** edits to the DB / `taxonomy.json` with an old→new
  audit log (reversible). The next extraction round picks up the corrected data automatically.
- **Hard rule:** Loop B only applies **data** changes (ground truth, taxonomy, eval-policy toggles).
  **Code / eval-logic changes always go through a GitHub PR → human review → `fly deploy`** — an
  agent must not be able to edit its own scoring code or answer key (reward-hacking surface).
- New infra needed: scheduled job, email-provider secret, one authenticated write endpoint (the API
  is otherwise read-only), apply+log module.
- Known GT issues already surfaced by the audit: `sector_name` taxonomy is comma-stripped vs the
  comma-using ground truth (fix the *taxonomy*, not the GT); a few `sub_sector` records list two
  values with ` | ` (now accepted as "either is correct" in scoring); author name-order variants.
- **Automated human-review queue (extension):** rather than only *surfacing* cases in the dashboard,
  route items that need a person into a queue — the model **abstained** or was **low-confidence**,
  the **judge disagreed**, or **all models agree but disagree with the ground truth** (a strong
  "answer key is wrong" signal). This closes the feedback loop so ground truth **improves over time**
  instead of staying fixed.

## Recently shipped (2026-07-12) — scoring definitions, few-shot exemplars, context narrowing

**Sector definitions (biggest single win):** Added one-line definitions per sector to
`taxonomy.json` and wired them into the prompt builder (`prompts.py`). The model now sees
what "Social protection" means instead of guessing from a bare label. sector_name accuracy
jumped from 60% → 84% (+24 pts) on the production set; Social protection confusions dropped
from 7 errors to 2 (down 71%).

**Few-shot exemplars (Phase 4):** The optimizer's reflector can now propose 2-3 hard-case
examples alongside instruction rewrites for `single_categorical` fields. Exemplars are
stored inside `prompt_versions.template` (sentinel-delimited, no schema change) and
accumulated across accepted iterations via `merge_exemplars()` (deduped, capped at 6).
Tested end-to-end: instruction-only optimization gave +4.7 pts; with exemplars the best
run gave +13.3 pts. Exemplars deliver 2-3× the improvement of instruction-only optimization.

**Reflector fallback chain + dual-family holdout:** The optimizer now tries 3 reflector
models in sequence (Claude Sonnet → GPT-4o → Gemini Pro) so a single provider outage
doesn't waste an iteration. The cross-model generalization gate now uses 2 different-
family reference models (DeepSeek + Gemini Flash) instead of 1, blocking single-family
overfits.

**Sub-sector context narrowing:** When extracting `sub_sector`, the system looks up the
previously-extracted `sector_name` for the same record+model and passes it as context.
The prompt narrows from 66 sub-sector options to ~3-8 under the known sector. Ground-truth
analysis confirmed 99.2% consistency. sub_sector accuracy: 58% → 64% (+6 pts).

**Author affiliation scoring fix:** The fuzzy matcher now uses `partial_ratio` when one
string is >1.2× longer than the other (with an 8-char minimum). This catches the
department-prefix case: "Dept of Economics, Leibniz University of Hannover" vs
"Leibniz University of Hannover" now scores 1.0 (was 0.00).

**Author affiliation instruction tightened:** Added a CRITICAL RULE with concrete examples
forcing the model to extract ONLY the parent institution and discard department names.

**Authors scoring tightened:** Raised the fuzzy match threshold from 95 to 98 for authors
only, stopping the F1 from over-crediting near-miss names ("Smith, J." no longer matches
"Smith, John"). Makes the authors F1 honest at ~68%.

**Country/institution name normalization (Phase 2):** "USA" → "United States", "MIT" →
"Massachusetts Institute of Technology", etc. Affiliation +~9 pts, country +~3 pts.

**Dashboard honesty (Phase 1):** Optimizer failure/accept rate surfaced on the dashboard;
plateaued fields show "Needs human review" instead of "still improving."

**Judge companion gate:** If the LLM judge disagrees with the scorer by >10 pts, the model
does not pass the gate even if F1 clears the bar. Catches the authors over-crediting at the
production-readiness level.

**Test results across the cheap-tier supervisor run (sector_name, 6 models, 920 runs):**
- Extraction: 6 models × 100 records, 0 errors, 74-83% accuracy
- Judging: 920 runs, 100% scorer-judge agreement
- Optimization: 3 of 6 models accepted exemplar-optimized prompts (deepseek, qwen, gpt-mini)

**Current accuracy by field (gpt-mini-latest, 100 records):**

| Field | Baseline | Current | Gate (90%) |
|-------|----------|---------|------------|
| sector_name | 60% | 84% | -6 pts |
| sub_sector | 58% | 64% | -26 pts |
| author_affiliation | 27% | 30% | -60 pts |
| author_country | ~75% | ~78% | -12 pts |
| authors | ~66% | ~68% | -22 pts |

## Recently shipped (2026-07-08) — fresh start, metric overhaul, roster pruning

**Fresh start:** Full DB wipe after a complete first production run (50,535 runs archived to
`../DEP/backups/promptlab_prod_20260708_214047.db`). GT + v1 shared baselines preserved.

**Roster pruned to 13 models:** Retired glm-4.7-flash (96-100% error rate), gemini-pro-latest
(54% errors, 0.075 F1 on authors), kimi-k2.5 (25-36% errors), kimi-latest (40-59% errors),
llama-4-scout (marginal value). Both kimi variants removed. `backend/models.yaml` updated.

**Stage ceiling 200:** `PRODUCTION_ROLLOUT_STAGES=(100, 200)`, `MAX_PRODUCTION_RECORDS=200`.
Stage 300 retired — 200 records gives reliable metrics at lower cost.

**Advancement rule: best model passes (not all):** Field advances when the best model crosses
the gate; weaker models keep being optimized at the next stage level.

**Recall floor:** Gate for list fields is now **F1 ≥ 0.90 AND recall ≥ 0.85** (`RECALL_FLOOR`
in `scoring.py`). Enforced in supervisor advancement, supervisor logging, and optimizer candidate
acceptance. Rationale: missing values are invisible in QA; extras are visible and fixable.

**Previously shipped (2026-07-07–08):**
- Bold-mode null content fixed (`json_mode=False` + `max_tokens=4000` for Claude Sonnet).
- Per-field `IMPROVEMENT_EPSILON` (0.03 list, 0.01 categorical).
- `max_tokens` 1024→2048 (fixed glm-4.7-flash JSON truncation; model now retired anyway).
- `from pathlib import Path` added to `api.py` (was causing 500 on all upload endpoints).
- JWT auth (HS256 stdlib) replacing in-memory token set — survives machine restarts.
- New-project wizard (5-step extraction + 3-step screening with EPPI upload + LLM-assisted
  question generation).

## Model reliability — known broken models (historical; all retired)
- **`z-ai/glm-4.7-flash`**: `content=null` for ~95% of calls under `json_object` mode. Retired.
- **`~google/gemini-pro-latest`**: 54% error rate on authors, 0.075 F1. Retired.
- **`~moonshotai/kimi-latest`** and **`moonshotai/kimi-k2.5`**: 25-59% errors. Retired.
- **`meta-llama/llama-4-scout`**: Consistently 0.05-0.10 below top models. Retired.

## Recently shipped (2026-07-08) — analysis documents

- `analysis.qmd`: deep analysis covering gate metric validity, recall vs F1, optimizer impact,
  error pattern analysis, model roster consequences, GT reliability risks, and recommendations.
- `examine_wrong_labels.py` + `wrong_labels_report.txt`: systematic analysis of "wrong" labels
  across fields/models. Key finding: most apparent "artefacts" are already handled by
  `token_set_ratio` in the scorer; the net bias is small (±0.01-0.02 F1).

## Recently shipped (2026-07-06)

## Recently shipped (2026-07-06)

- **Deployed to production (Fly.io) + fresh start:** carbon tracking (EcoLogits per-run gCO₂e),
  per-model prompts, and the 18-model roster are live; the DB was wiped to a clean slate (reference
  data kept, backup archived) and the autonomous supervisor is rebuilding v1 baselines across 18
  models × 5 fields under the new F1/accuracy gate. The frontend metric-clarity overhaul
  (Quality-led comparison table, leaderboard + cost/quality plots, slim glossary) is committed on
  `feature/eval-hardening`, pending merge to `main` (GitHub Pages).

## Future

- **[Phase 1 — shipped on `feature/parallel-workers`] Intra-machine parallelism** — supervisor
  `--parallelism N` flag spawns N concurrent subprocesses for extraction and optimization. Each
  subprocess handles one model; SQLite WAL handles concurrent writes safely. No infra changes
  needed. Activate on Fly by updating `launch_supervisor.sh` to add `--parallelism 4` and scaling
  the machine to 2+ CPUs (`fly scale vm performance-2x`). Expected: **N× throughput** on
  extraction and optimization cycles (judging is already one call; harder to split without a
  `llm_judge` per-model flag).

- **[Phase 2] True multi-machine parallelism — IMPLEMENTATION PLAN (decided 2026-07-09)**

  Full Postgres migration (all tables). Single-machine parallel fields first, then multi-worker.

  **Why SQLite can't parallelize:** `db.get_conn()` holds an implicit write transaction for
  the entire optimization loop (10–40 min), blocking every other writer. WAL allows concurrent
  reads but not concurrent writes.

  **Fly Postgres created:** `dep-promptlab-pg` (shared-cpu-1x, 10GB, iad).

  **Step sequence:**

  Step 1 — Infrastructure (done: Postgres created; next: `fly postgres attach dep-promptlab-pg`)
  Step 2 — `db_pg.py`: Postgres-equivalent of `db.py` + `worker_tasks` table:
  ```sql
  CREATE TABLE worker_tasks (
    id SERIAL PRIMARY KEY, project_id INT, field_name TEXT, model_id TEXT,
    kind TEXT NOT NULL, args_json JSONB, status TEXT DEFAULT 'pending',
    worker_id TEXT, claimed_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, error TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
  );
  -- Atomic claim: UPDATE ... WHERE status='pending' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED
  ```
  Step 3 — Migrate write paths: `add_run()`, `add_iteration()`, `add_llm_judgment()` → Postgres.
             SQLite stays for: `projects`, `records`, `ground_truth`, `prompt_versions` (coordinator-owned).
  Step 4 — `worker.py`: poll `worker_tasks`, claim atomically, execute, mark done. Stateless.
  Step 5 — `supervisor.py`: INSERT tasks instead of blocking shell-out. Short poll cycle.
  Step 6 — `fly machine clone` to add workers (no volume, just Postgres + OpenRouter).

  **Corpus access for workers (decide before Step 6):**
  Option A: `GET /corpus/{record_id}` from coordinator API (~10ms/record, simplest).
  Option B: Fly Object Storage / Cloudflare R2 (proper object storage, more setup).
  Recommendation: A for DEP, B for new client projects.

  **Tables to Postgres:** `runs`, `llm_judgments`, `iterations`, `jobs`, `worker_tasks`, `self_consistency_runs`.
  **Tables staying SQLite:** `projects`, `records`, `ground_truth`, `prompt_versions`.



- **[Phase 3] Elastic workers via Fly Machines API** — coordinator spawns an ephemeral Fly machine
  per task batch using the Fly Machines REST API, runs the task, machine exits. Fully elastic:
  zero idle cost, burst to many machines for a big extraction wave. Requires Phase 2's shared DB.
- **Confidence-based model triage / cascade** — cut cost/carbon by not sending everything to the
  priciest model. Two tiers: (a) *simple* — pick the **cheapest gate-passing model per field** (the
  cost-vs-quality frontier already identifies it); (b) *cascade* — a cheap first pass, **escalate to
  a reasoning model (or a human) only when confidence is low or the judge disagrees**. The needed
  signals already exist (logprob confidence, self-consistency, cross-model agreement, verbalized
  confidence + calibration).
- **Shared base-prompt library + per-project overlays** — DEP-learned prompts are **only partly
  portable** to HSF/GE/SM (fields, taxonomy, and document types differ; prompts overfit even across
  models within DEP, which is why the cross-model holdout gate exists). Rather than each workspace
  rediscovering from scratch, factor prompts into a **shared base** (general extraction discipline:
  excerpt-first, null convention, injection guard, "don't guess") **+ project/field overlays** (the
  taxonomy, field definition, examples), and optimize mainly the overlay. `prompts.build_prompt`
  already does this at the field level within one project; extend it across projects.
- **Validation study (evidence for the human-on-the-loop value)** — to *show* the tool reduces
  effort without loss of quality: agreement vs. independent human dual-extraction, workload-saved
  (WSS), and a downstream-impact analysis (do extraction errors change synthesis conclusions?).
- **Prompt caching** (record-major execution) — **raised priority** given the cost/carbon focus and
  the fresh 18-model run: the `<paper>` block is the stable prefix, but extraction runs *field-major*
  so the cache goes cold; benefiting needs a *record-major* rewrite. Benchmark the savings before
  refactoring `run_extraction.py` (see README).
- **Screening project** — a second project (title/abstract include/exclude). Screening is a binary
  classification task where **recall/sensitivity** is the dominant metric (missing an includable
  study is the costly error), reported with specificity + workload-saved (WSS); gate on high recall,
  not accuracy. Requires generalizing `prompts`/`scoring`/`taxonomy` beyond the single `fields.FIELDS`
  dict.

## Open questions (strategy — for leadership, not eng tasks)

- **Publish the method vs. keep it for BD / cost advantage.** The techniques (GEPA-lite
  optimization, LLM-as-judge, holdout gates, honesty scoring) are largely public in the literature,
  so the algorithmic moat is thin; the durable assets are the **curated ground truth, the domain
  taxonomy, and the operational pipeline**. Publishing the *method* costs little competitively and
  builds credibility/adoption (aligns with 3ie's open-evidence mission) while the *data +
  operations* stay proprietary. Decision owner: leadership.
