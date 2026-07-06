# Prompt Lab — Roadmap

Forward-looking plans and design decisions for the DEP Prompt Lab (backend + frontend).
This is the canonical roadmap; `backend/README.md` and the agent instructions point here.
Keep entries short; move anything that becomes "current lasting state" into the README instead.

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

**Planned — align the optimizer's acceptance metric with the gate:** the optimizer currently
accepts a prompt rewrite on **LLM-judged accuracy** (val + cross-model holdout), while the *gate*
now uses F1/accuracy. These are correlated but not identical; consider making the optimizer accept
on the same field-type-aware gate metric so "what the optimizer chases" == "what the gate checks".

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

## Pending deploys (committed, not yet in production)

- **Carbon footprint tracking** (EcoLogits per-run gCO₂e) — committed, safe to deploy (runs no
  models); backfill fills history from stored tokens.
- **Per-model prompts** + **18-model roster** — committed; deploying starts a (carbon-costed)
  18-model benchmark, so weigh the footprint first.

## Future

- **Prompt caching** (record-major execution) — benchmark savings before refactoring
  `run_extraction.py` (see README).
- **Screening project** — a second project (title/abstract include/exclude). Screening is a binary
  classification task where **recall/sensitivity** is the dominant metric (missing an includable
  study is the costly error), reported with specificity + workload-saved (WSS); gate on high recall,
  not accuracy. Requires generalizing `prompts`/`scoring`/`taxonomy` beyond the single `fields.FIELDS`
  dict.
