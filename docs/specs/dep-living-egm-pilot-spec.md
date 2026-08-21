# DEP living-EGM pilot — spec & method

**Pilot target:** one DRG Evidence Gap Map. Civil Society is the recommended choice (swap via ⟦P1⟧).
**Occasion:** EES 2026 session alongside DEval (around October 2026). A one-off, measured update of an existing EGM from studies already in DEP, run as a test of whether any EGM can be kept current at low cost.
**Basis:** email thread (EES/DEval session; DEval interest in a living Civil Society EGM) · [DEP LLM Roadmap](docs/DEP_LLM_Roadmap.md) · `sr-platform` + `living-evidence` LLM prompt registry ([screening](prompt_lab/prompts-screening.md), [search/synthesis](prompt_lab/prompts-search-synthesis-protocol.md)) · [DEP extraction protocol IE v4](DEP%20extraction%20protocol%20for%20IE%20v4-%20Admin%20panel.xlsx) · [EPPI↔DEP integration spec v2](eppi-dep-integration-spec-v2.md) (the phase-2 feed, not on this pilot's critical path)
**Convention:** every unknown is a `⟦token⟧`; each appears once in §10 as a question. Once the tokens are resolved, the pilot is executable.

---

## 0. Framing: what this pilot is

The pilot is a **DEP → EGM** pipeline. It takes studies already in DEP, decides which are eligible for the chosen EGM, codes them into that EGM's intervention × outcome framework, and publishes them, while measuring cost (human-days plus AI spend) and accuracy (agreement with the original human coding). Those two measures are the conference deliverable.

---

## 1. Objective & success criteria

**The conference claim template** (fill the blanks by running the pilot):

> The 6 DRG EGMs were completed 2020–2022 with **1,867** included studies. Through a one-off AI-assisted update we added **⟦N-added⟧** studies to the *Civil Society* map in about 3 months, using **⟦H⟧ human-days** and **$⟦C⟧** of AI compute, at **⟦A⟧% agreement** with the original human coding on a blind back-test.

**Success = all four:**

1. **Runs end-to-end.** A study flows from DEP through eligibility, custom-field coding, and into a published EGM cell without manual glue.
2. **Defensible accuracy.** Blind back-test agreement clears the bar agreed in advance ⟦P2: threshold, e.g. κ ≥ 0.6 on cell coding⟧.
3. **Measured cost.** Dollars per study and human-minutes per study are computed, not estimated.
4. **A real number added.** Net-new eligible DEP studies are placed on the map (`N-added`).

Non-goal for October: a production "always-on" living map. That is the phase-2 feed (spec v2) plus Roadmap Phase 3 "Dynamic EGMs".

---

## 2. Scope

| In scope | Out of scope |
|---|---|
| One EGM — Civil Society ⟦P1⟧ | The other 5 DRG EGMs (generalise after the pilot) |
| Source = DEP studies as of now | EPPI→DEP ingestion (spec v2, phase 2) |
| Candidate filter over DEP metadata ⟦P3: which DEP sector/DAC codes bound the map's scope⟧ | New literature searches beyond DEP |
| Eligibility + EGM custom-field coding | The EGM's public rendering/redesign (reuse existing map UI ⟦P4⟧) |
| Cost + accuracy instrumentation | Full DEX re-extraction (DEP fields are already coded; the pilot consumes them) |

---

## 3. Inputs and their current state

| # | Input | Role | State |
|---|---|---|---|
| I1 | Civil Society EGM **codebook** — intervention framework (rows), outcome framework (columns), inclusion/exclusion criteria, definitions | The classification target and the eligibility rubric | **Not in repo** ⟦P5: location + format (xlsx/Word/EPPI coding tree?)⟧ |
| I2 | The map's **existing coded studies** (subset of the 1,867) with their cell assignments | Two uses: RAG exemplars (Stage 3) and gold standard (§5) | **Not in repo** ⟦P6: how many for this map? are their DEP study IDs available? held where?⟧ |
| I3 | DEP **full-text corpus** — `backend/deploy/corpus/*.md` | Read source for eligibility and coding | **Present** (sample in repo) ⟦P7: does it cover the gold-set + candidate studies, or only a subset?⟧ |
| I4 | DEP **structured metadata** (sector, method, intervention/outcome, country…) | Candidate filter and priors for coding | Available via DEP export ⟦P8: export vs API for the pilot⟧ |
| I5 | `sr-platform` prompt registry + calibration machinery (`runs`, `consensus_models`, vote-share) | Reused pipeline components | **Present** (documented in prompt_lab) |
| I6 | Meeting notes (Mark/Lucas initial discussion, attached to the email) | Decisions already taken, to avoid re-litigating | **Not in repo** ⟦P9: share so this spec reconciles with them⟧ |

> I2 is the most time-sensitive input. Without the map's existing coding as a gold standard there is no accuracy figure to report, so confirm it is available before anything else starts.

---

## 4. Pipeline (DEP → EGM)

Five stages. Each names the existing component it reuses, its mode (AUTO = auto-write; S+C = suggest-and-confirm; HITL = human), and what provenance and cost it records. Modes start conservative and move to AUTO only where the back-test (§5) earns it, following the same discipline as spec v2 §5.

### Stage 0 — Candidate filter *(mode: AUTO; negligible cost)*

Query DEP metadata down to the map's scope ⟦P3⟧, for example DEP studies in the governance/public-admin sector slice, to produce the candidate set. This is a metadata filter (SQL or export) with no LLM involved. **Records:** candidate count, the denominator for every later rate.

### Stage 1 — EGM eligibility screening *(mode: S+C, moving to AUTO once validated; reuses `screening.score` + `screening.critic` with consensus vote-share)*

Judge each candidate against the map's inclusion/exclusion criteria (I1). Reuse the five-level ordinal verdict and the reasoning-before-verdict contract already in [prompts-screening.md](prompt_lab/prompts-screening.md), taking the ranking signal from vote share over `runs` 3–5 rather than a single verbalised number. Route `unclear` to a human (`uncertainty_policy: abstain`). **Records:** verdict, criterion codes, evidence anchors (provenance), and tokens/dollars (cost).
> Because the pilot covers one EGM, eligibility and assignment collapse into a single question: does this study belong on this map? The step that attributes a study to one of the six maps is deferred to the generalisation (see §8).

### Stage 2 — Document assembly *(mode: AUTO; reuses corpus + DEP metadata)*

For each eligible study, assemble the coding context: sectioned full text (I3), DEP structured fields (I4), and retrieved exemplars from a k-NN search over the map's already-coded studies (I2). This mirrors the retrieval step in [integration spec Appendix A](eppi-dep-integration-spec-v2.md). **Records:** which exemplars were retrieved (provenance).

### Stage 3 — Custom-field coding *(mode: S+C; the one new classifier in the pilot)*

Place the study in the EGM framework: which **intervention row(s)** and **outcome column(s)** it populates, plus any map-specific fields ⟦P10: does the Civil Society EGM carry custom fields beyond the intervention×outcome grid, e.g. population, region, study-design filter?⟧. Structured output is validated against the codebook vocabulary (I1), with a single repair-retry on out-of-vocabulary codes and a human queue after that. It uses the same runtime as integration spec Appendix A, retargeted from the DEP taxonomy to the EGM framework. A confidence gate routes each field to AUTO, S+C, or HITL. **Records:** per-cell code, description, page-anchored spans, confidence, and model/version (the full provenance record from spec v2 §5), plus tokens/dollars.
> Spec v2 deferred the EGM custom fields to a separate document. This spec is that document.

### Stage 4 — Human confirmation & publish *(mode: HITL on S+C fields; reuses Nova/review surface ⟦P4⟧)*

A reviewer accepts or edits the suggested cells, and confirmed entries publish to the map. Confirmation happens after commit and does not block the pipeline (spec v2 design (d)), so review lag never holds up the count. **Records:** reviewer_action per field and human minutes (cost).

Instrumentation runs through every stage rather than being a stage of its own: machine cost (tokens × price ⟦P11: model + pricing tier⟧) and human effort (reviewer minutes, reusing the `Minutes`-per-tab convention and its S2 semantics from spec v2 §3). Everything §6 needs is captured while the pipeline runs, not reconstructed afterwards.

---

## 5. Validation method

The 6 EGMs were human-coded 2020–2022. That coding is a held-out gold standard, already in-house, and the pilot's accuracy claim rests on it. The method has two arms.

### 5.1 Back-test arm (accuracy)
Take the map's known-included studies (I2), strip their EGM coding, run Stages 1–3 blind, and compare against the original human coding:

| Measure | Compares | Metric |
|---|---|---|
| Eligibility agreement | Stage 1 verdict vs original include set | recall and precision (recall matters most, since a missed eligible study is the costly error) |
| Cell-coding agreement | Stage 3 intervention×outcome cells vs human cells | per-dimension F1 plus Cohen's/Krippendorff's κ ⟦P2⟧ |

This produces the `A`% agreement figure and calibrates the per-field confidence thresholds that decide AUTO vs S+C, the same recalibration loop as spec v2 §5.

### 5.2 Discovery arm (the headline number)
Run the full pipeline over candidate DEP studies that are not in the original map, whether added to DEP since 2020–2022 or eligible but missed at the time. The eligible ones make up `N-added`, the "we added XXXX" number. There is no gold standard for this set, so a random sample should be double-keyed by a human to estimate the precision of the additions ⟦P12: sample size + who codes the second key⟧. That sample is what lets the count be reported with an error bar rather than as a bare figure.

> A caution for the pitch: `N-added` counts studies DEP already holds that were not on the map, so it shows low-cost coding rather than new evidence discovery. The two should not be conflated in the presentation, since the difference is something DEval is likely to question.

---

## 6. Cost & effort model (the conference numbers)

Reported per study and extrapolated to a cost of making any EGM living:

- **Machine cost** = the sum over stages of tokens × unit price ⟦P11⟧. Stage 3 dominates; Stage 0 is negligible.
- **Human effort** = reviewer minutes on S+C fields, plus triage on `unclear` and out-of-vocabulary cases, plus double-keying. Captured live in Stage 4.
- **Outputs:** dollars per study, human-minutes per study, and a projection of human-days per 100 studies, which is the figure behind the "any EGM, low cost" argument.
- **Baseline for contrast:** the roadmap's manual DEX figure (20–90 min/study) and the original EGM's coding effort ⟦P13: is the original per-study effort recorded anywhere?⟧, to give the before-and-after comparison.

---

## 7. Timeline to EES (today 2026-07-19 → target early October, about 11–12 weeks)

| Weeks (from w/c 21 Jul) | Milestone | Gate |
|---|---|---|
| 1–2 | Secure I1 (codebook), I2 (gold set), I6 (notes); run Stage 0 candidate count; confirm ⟦P1/P3⟧ | Go/no-go: is the gold set usable? (§3 note) |
| 3–5 | Build Stages 1–3 on `sr-platform`; back-test (§5.1) on gold set; calibrate thresholds | Accuracy clears ⟦P2⟧ |
| 6–8 | Discovery arm (§5.2) over candidates; human double-key sample | `N-added` with error bar |
| 9–10 | Stage 4 review and publish to the map; compute §6 numbers | Map updated; cost/accuracy locked |
| 11–12 | Conference narrative, slides, buffer | Pitch ready |

The critical path runs through weeks 1–2, because every downstream number depends on I1 and I2 arriving on time.

---

## 8. Risks & decisions

- **Gold set not in the full-text corpus (I2 vs I3).** If the map's original studies lack full text in `corpus/`, the back-test falls back to abstract-level coding, which is weaker but still reportable. Check coverage in week 1 ⟦P7⟧.
- **Codebook granularity vs LLM reliability.** Cells may be too fine for reliable classification. The back-test will show which dimensions hold up and which stay HITL.
- **"Living" is one-off here.** The pilot proves the update mechanism; a continuously living map needs the phase-2 feed (spec v2). The presentation should be clear on this and not imply the pipeline already runs continuously.
- **Single-EGM assignment shortcut.** Collapsing eligibility and assignment (the Stage 1 note) is valid for one map but has to be reopened for the six-map generalisation.
- **Copyright / PII.** Full-text handling and author PII carry the same constraints as spec v2 §8, and the org rule against PII in external uploads applies to any shared artifact or slide.
- **Scope creep from the number.** Loosening eligibility to raise `N-added` trades away the accuracy figure. Fix eligibility strictness from the back-test, then report whatever `N-added` results.

---

## 9. Relationship to existing 3ie work

| Asset | Role in the pilot |
|---|---|
| `sr-platform` screening prompts (score/critic/advocate/synthesise, consensus, vote-share) | Stage 1, largely as-is |
| Integration spec Appendix A (taxonomy classifier runtime, RAG + confidence gate + repair) | Template for Stage 3, retargeted to the EGM framework |
| Integration spec v2 (EPPI→DEP feed, provenance, security) | Phase-2: keeps the map living after the pilot |
| DEP corpus + metadata | Source material |
| Roadmap Phase 3 "Dynamic EGMs" | The production version this pilot is a step toward |

---

## 10. Questionnaire (resolve the tokens)

**Pilot design (P1–P4)**
1. P1: confirm Civil Society as the pilot EGM (vs another DRG map).
2. P2: agreed accuracy bar for "success" (e.g. κ on cell coding; recall floor on eligibility).
3. P3: which DEP sector/DAC/intervention codes bound this map's candidate set?
4. P4: what is the review surface (Nova?) and the map's publish/render target for pilot output?

**Inputs (P5–P9)**
5. P5: Civil Society EGM codebook — location + format (intervention/outcome frameworks, inclusion criteria).
6. P6: the map's existing coded studies — count, DEP study IDs, where held.
7. P7: does the full-text corpus cover the gold set + candidates, or only a subset?
8. P8: DEP access for the pilot — bulk export or API?
9. P9: the meeting notes attached to the triggering email.

**Method & cost (P10–P13)**
10. P10: does the Civil Society EGM carry custom fields beyond the intervention×outcome grid?
11. P11: which model(s) + pricing tier for the cost figure (Claude / GPT / mixed)?
12. P12: discovery-arm double-keying — sample size and who provides the second (human) key?
13. P13: is the original EGM's per-study coding effort recorded anywhere, for the before/after contrast?
