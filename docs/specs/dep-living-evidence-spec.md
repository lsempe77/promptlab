# DEP Living Evidence — concept note

## Purpose

Evidence Gap Maps are most useful when they are current. DEP already holds a growing body of impact-evaluation studies, but keeping each EGM aligned with that evidence base still depends on periodic manual screening and coding. As a result, maps that were accurate when published can become stale even while DEP itself continues to grow.

This concept note proposes a practical way to make EGMs easier to maintain. The idea is to start with one map, use DEP studies as the source base, apply AI to screen and suggest EGM coding, and keep human reviewers in control of uncertain or publishable decisions. The pilot would measure three things that matter for scale: how many studies can be added, how accurate the AI-assisted process is against existing human coding, and how much machine and human effort it costs.

Civil Society is the first pilot map. The same approach could later be extended to the remaining DRG EGMs and, once the EPPI→DEP feed is operational, used as part of a steady update cycle.

## The Problem

An Evidence Gap Map places studies into a framework of intervention rows and outcome columns. That framework is defined for each map. It is not simply the same as DEP's general intervention and outcome taxonomy, even though DEP fields can help provide useful context.

At present, updating a map requires people to repeat two kinds of work. First, they must decide whether a DEP study belongs on the map. Second, if it does, they must place it into the map's own cells and any map-specific fields. This is manageable for a small update, but it becomes expensive and irregular across several maps.

The proposed capability does not solve evidence discovery. It does not search the literature or add studies that DEP does not contain. Its narrower and more tractable role is to keep a map aligned with the studies already in DEP, with a transparent record of what was added, what was rejected, what required review, and what the process cost.

## Proposed Approach

The pilot would treat the Civil Society EGM as a controlled test case. It would follow the same protocol and source repositories used to build the original map, first screening titles and abstracts and then moving to full text only for records that pass that first screen. For studies already represented in DEP, the same process would use DEP metadata and documents to suggest EGM cells and route cases for human review where confidence is not high enough for automatic handling.

The design principle is conservative automation. AI is used to reduce the review burden, not to remove accountability. The system can make suggestions, assemble evidence, and record provenance. Human reviewers remain responsible for confirming entries that become part of the published map, unless validation shows that a narrow class of high-confidence decisions is safe to write automatically.

The broad workflow is intentionally simple. The pilot starts from the same kinds of repositories used for the original Civil Society map, applies the map protocol at title and abstract stage, retrieves full text only for records that pass, suggests map cells, sends decisions for human review where needed, and publishes confirmed entries. The final report records accuracy, additions, cost, and reviewer effort.

The pilot has two parts. The initial build tests whether the existing static map can be updated by rerunning its original search and screening logic with AI support. The steady-state model is the later operating pattern: when the same sources are searched again, the same screening, coding, review, and publication steps run again.

## What the Pilot Must Prove

The pilot should not be judged only by whether it produces plausible labels. It needs to show that the process is useful, measurable, and governable.

First, the workflow must run end to end. A study should be able to move from DEP through eligibility screening, EGM coding, review, and publication without ad hoc file handling.

Second, accuracy must be measured against human coding. The map's existing coded studies are the gold standard. The pilot should hide their EGM coding, run the process blind, and compare the result with the original human coding. The accuracy thresholds should be benchmarked from the relevant literature on evidence screening, study classification, and inter-reviewer agreement before the run.

Third, cost must be measured from real runs. The report should include both machine cost and reviewer time, expressed per study and projected per 100 studies.

Fourth, the pilot should account for every study in scope. The candidate set means the records retrieved from the same repositories and search criteria used by the original Civil Society EGM protocol, not the final eligible set. A study in that candidate set should either appear on the map or have a recorded reason for exclusion.

The outcome should be a simple claim that can be reported to decision-makers: the pilot added a known number of studies, at a known accuracy, for a known cost.

## Inputs Needed Before Work Starts

The most important dependency is the map's existing human coding. Without it, the pilot can still produce suggestions, but it cannot make a defensible accuracy claim.

| Input | Why it matters | Current state |
|---|---|---|
| EGM framework | Defines the intervention rows, outcome columns, inclusion rules, and allowed codes. | In the DEP EGM builder |
| Existing coded studies | Provides examples for the model and the gold standard for validation. | Held on the EGM in the DEP backend |
| Titles and abstracts | Support the first screening step before full text is retrieved. | Needed from the same repositories used for the original EGM protocol |
| DEP metadata and full texts | Support coding after a record passes title and abstract screening. | Available in DEP where records and documents have already been ingested |
| Prompt and calibration machinery | Reuses existing screening, model-run, and vote-share components. | Present in `prompt_lab` |

The first practical checkpoint is therefore a data readiness check: confirm that the selected map has an exportable framework, exportable coded studies linked to DEP IDs, titles and abstracts for protocol-based screening, and full texts for records that pass the first screen.

## How Validation Would Work

Validation has two arms. The back-test arm measures accuracy. The discovery arm estimates the quality of newly added studies.

In the back-test, the system starts with studies that are already on the map. Their existing EGM coding is hidden. The pipeline then screens and codes them as if they were new. The result is compared with the human coding that was originally applied to the map.

Eligibility and cell coding should be scored separately. For eligibility, recall is especially important because a missed eligible study is the most costly error. For cell coding, the comparison should allow partial credit: many studies belong in more than one intervention or outcome cell, so the useful measure is agreement over sets of cells rather than a single all-or-nothing label. F1 and an agreement statistic such as Cohen's or Krippendorff's κ should be reported where appropriate.

The back-test can be summarized as a four-step check: start with existing map studies, hide their EGM coding, run the pipeline blind, and compare the result with the original human coding. That comparison produces two outputs: eligibility accuracy and cell-coding agreement. These become the basis for the thresholds used in later runs.

The discovery arm then runs the process over records found through the same Civil Society search sources but not already on the map. These are the potential additions. Because there is no gold standard for them, 10% of AI-suggested additions should be double-coded by a human reviewer to estimate precision. The final report should be clear about what this measures: the ability to keep the EGM current through a repeatable search, screen, code, and review process.

## Human Review and Publication

The review model should be explicit from the start. High-confidence results may eventually be eligible for automatic writing, but only after the back-test justifies that choice. Medium-confidence results should be presented as suggestions for a reviewer to accept or edit. Low-confidence or malformed results should go directly to human coding.

Suggested entries should remain provisional until reviewed. If the public map can show a provisional or AI-suggested state, those entries can be visible with the right label. If not, they should be withheld from confirmed map outputs until a reviewer signs them off.

Each published value should carry an audit trail: the model and prompt version, the source evidence, the confidence signal, the reviewer action, and the confirmation state. This is what makes the result inspectable rather than just automated.

## Cost and Effort

The pilot should report cost in the same practical units that a programme team can use for planning.

Machine cost is the sum of model tokens multiplied by the agreed model prices. The first model candidate should include `z-ai/glm-5.2`, with at least one cheaper model run as a comparator so the report can show the cost-quality trade-off rather than assume it. Human effort is reviewer time for eligibility checks, suggested cells, out-of-vocabulary cases, and double-coding. The useful outputs are dollars per study, human-minutes per study, and projected human-days per 100 studies.

Those figures should be compared with the manual baseline. The roadmap's current DEX figure is 20-90 minutes per study, and any recorded effort from the original map build should be used as an additional benchmark.

## Delivery Path

The work should proceed in four phases.

| Phase | Main work | Exit condition |
|---|---|---|
| 1. Prepare the first map | Confirm the map, export the framework and gold set, restate the original protocol search criteria, and check title/abstract and full-text availability. | The gold set is usable. |
| 2. Build and validate | Implement screening and EGM coding on the selected map, then run the blind back-test. | Accuracy clears the agreed threshold. |
| 3. Publish an update | Run the discovery arm, complete human review, and publish confirmed additions. | The map reflects in-scope DEP studies, with cost and accuracy recorded. |
| 4. Generalise and sustain | Repeat for other DRG maps and connect to the EPPI→DEP feed. | Updates can run without a manual restart each time. |

The critical path runs through the first phase. If the framework or existing coded studies cannot be exported cleanly, the pilot should pause rather than proceed without a benchmark.

## Governance and Risk

The pilot uses full texts, metadata, prompts, model outputs, and reviewer decisions. That creates governance questions that should be resolved before production use.

The first question is provider approval. Stages that send titles, abstracts, or full texts to an AI model need clear rules about which providers may receive study text, whether zero-retention endpoints are required, and whether any content must stay on premise.

The second is copyright and retention. Full texts may be copyrighted, and prompts or outputs may contain derived material. The project should define what can be stored, for how long, and who may access it.

The third is accountability. AI-suggested values should not become anonymous machine outputs. Each confirmed entry should have a named reviewer or approval record.

The main delivery risks are practical rather than abstract. The pilot may not be able to reproduce the original search sources cleanly. The gold set may not have enough title, abstract, and full-text coverage. The map codebook may be too fine-grained for reliable classification. Eligibility rules may be loosened in pursuit of a larger additions number, reducing accuracy. And the initial build should not be presented as a continuously living map until the scheduled or feed-triggered update cycle is actually in place.

## Relationship to Existing Work

This proposal builds on work 3ie already has rather than starting from scratch. The screening prompts and consensus machinery from `sr-platform` can support eligibility screening. The structured-classification pattern from the integration spec can be retargeted from DEP taxonomy coding to EGM cell coding. DEP provides metadata and full texts where records have already been ingested, while the original EGM protocol defines the search sources for new screening. The EPPI→DEP integration is the route to steady-state updates once the first map has been proven.

## Settled Assumptions and Remaining Choices

Several earlier questions are now settled enough for the concept note. Civil Society is the pilot map. Validation thresholds should be benchmarked from the literature rather than invented for this exercise. The candidate set should follow the same search sources and eligibility protocol used to build the original Civil Society EGM, beginning with title and abstract screening and moving to full text only after a positive first-stage screen. For cost testing, `z-ai/glm-5.2` should be included as the main quality candidate and compared with a cheaper model so the pilot can report the trade-off.

The remaining choices are implementation details that should be resolved during phase 1.

1. Restate the original Civil Society EGM search protocol in a form the pilot can rerun: which repositories, dates, search terms, and title/abstract fields are used.
2. Pull the Civil Society map structure and existing human coding from the DEP EGM builder/backend, including DEP study IDs and any custom map fields.
3. Check that records have titles and abstracts for first-stage screening, and that full texts can be retrieved for records that pass that screen.
4. Double-code 10% of AI-suggested additions and assign the reviewers before the discovery run starts.
5. Decide whether provisional or AI-suggested entries can be displayed before reviewer confirmation.
6. Use an approved provider route for study text, preferably zero-retention where available; store prompts, outputs, reviewer actions, model versions, and publication records as the audit trail, while relying on DEP as the system of record for full texts.

## References

This note draws on the [DEP LLM Roadmap](docs/DEP_LLM_Roadmap.md), [DEP extraction protocol IE v4](DEP%20extraction%20protocol%20for%20IE%20v4-%20Admin%20panel.xlsx), the existing [screening](prompt_lab/prompts-screening.md) and [search/synthesis](prompt_lab/prompts-search-synthesis-protocol.md) prompt registry files, and [EPPI↔DEP integration spec v2](eppi-dep-integration-spec-v2.md).