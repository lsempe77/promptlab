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
- **Automated human-review queue (extension):** rather than only *surfacing* cases in the dashboard,
  route items that need a person into a queue — the model **abstained** or was **low-confidence**,
  the **judge disagreed**, or **all models agree but disagree with the ground truth** (a strong
  "answer key is wrong" signal). This closes the feedback loop so ground truth **improves over time**
  instead of staying fixed.

## Recently shipped (2026-07-06)

- **Deployed to production (Fly.io) + fresh start:** carbon tracking (EcoLogits per-run gCO₂e),
  per-model prompts, and the 18-model roster are live; the DB was wiped to a clean slate (reference
  data kept, backup archived) and the autonomous supervisor is rebuilding v1 baselines across 18
  models × 5 fields under the new F1/accuracy gate. The frontend metric-clarity overhaul
  (Quality-led comparison table, leaderboard + cost/quality plots, slim glossary) is committed on
  `feature/eval-hardening`, pending merge to `main` (GitHub Pages).

## Future

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
