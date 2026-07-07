import { Fragment } from "react";
import type { Thresholds } from "../api";
import { MermaidDiagram } from "./MermaidDiagram";

const PIPELINE_CHART = `flowchart TD
    GT["Ground truth<br/>(human-curated)"] --> EX
    P["Per-model prompt<br/>(v1 baseline → optimized)"] --> EX["Extract field across<br/>the model roster"]
    EX --> SC["Score vs truth — concordance-aware:<br/>accents/mojibake folded · 'A or B' = either accepted"]
    SC --> LOG["Log per run:<br/>outcome · honesty · cost · CO₂e"]
    SC --> JUDGE["Cross-family LLM judge<br/>(concordance)"]
    LOG --> GATE{"Per-model gate:<br/>F1 (lists) / accuracy (categorical) ≥ 90%?"}
    JUDGE -. corroborates .-> GATE
    GATE -- "below gate" --> REFLECT
    GATE -- "passes" --> STAGE{"Sample size<br/>reached this stage?"}
    STAGE -- "100 → grow" --> G200["Extract to 200 refs"]
    STAGE -- "200 → grow" --> G300["Extract to 300 refs"]
    STAGE -- "300 (capped)" --> DONE(["Production-ready<br/>(field, model) pairs"])
    G200 --> EX
    G300 --> EX
    subgraph OPT["Optimizer (autonomous, per-model)"]
      direction TB
      REFLECT["Reflector proposes revision<br/>(bold structural rewrite after 2 rejects)"] --> RETEST["Re-test on 50-paper held-out val<br/>+ cross-model holdout"]
      RETEST --> ACC{"Improves AND generalizes?"}
      ACC -- "yes" --> ACCEPT["Accept → new per-model prompt version"]
      ACC -- "no" --> REJECT["Reject<br/>(stop after 4 no-improve / 10 iters)"]
    end
    ACCEPT --> P
    DASH["Dashboard: leaderboard · cost/quality ·<br/>confusion · calibration · lineage · CO₂e"] -.reads.- SC`;

// One glossary row: [metric, plain meaning, better direction].
type Row = [string, string, string];

const TIERS: { tier: string; rows: Row[] }[] = [
  {
    tier: "Decision — the production gate",
    rows: [
      ["Quality (gate)", "F1 for list fields (authors/institutions/countries); accuracy for categorical fields (sector/sub-sector). A (field, model) is production-ready at ≥ 90%.", "higher"],
    ],
  },
  {
    tier: "Explains the gate",
    rows: [
      ["Precision", "Of the values the model reported, the share that were correct — penalises wrong extras (list fields).", "higher"],
      ["Recall (sensitivity)", "Of the true values, the share the model found — penalises misses (list fields).", "higher"],
      ["Cohen's κ", "Category accuracy discounted for chance agreement (categorical fields).", "higher"],
      ["95% CI", "Uncertainty band on a rate; narrows ~1/√n as the reference sample grows.", "narrower"],
    ],
  },
  {
    tier: "Corroboration",
    rows: [
      ["Concordance (LLM judge)", "A cross-family model decides whether the answer means the same as the truth — independent of string matching.", "higher"],
      ["Calibration / Brier", "Does the model's stated confidence match how often it's actually right? Diagnostic only, never folded into scores.", "lower Brier"],
    ],
  },
  {
    tier: "Honesty (this is what steers the optimizer)",
    rows: [
      ["Outcome mix", "Each answer is correct / abstained (honest miss) / wrong / hallucination (invented). A modest abstention rate is healthy.", "fewer wrong & hallucinated"],
      ["Honesty-adjusted score", "Correct = 1.0, honest abstention = 0.5, wrong/hallucination = 0.0. Only this steers the optimizer, so models learn to say \"I don't know\" rather than bluff.", "higher"],
      ["Excerpt verified", "Share of answers whose quoted source line was actually found in the paper — an anti-fabrication check.", "higher"],
    ],
  },
  {
    tier: "Diagnostics (context, not the gate)",
    rows: [
      ["Fuzzy-match rate", "Heuristic string-match score (≥ threshold on a 0–100 scale → treated as correct). Superseded by Quality; shown for reference.", "higher"],
      ["Exact-match accuracy", "Word-for-word identical only — the strictest, so usually lower than the others.", "higher"],
      ["Specificity / F2", "Specificity = correctly-omitted share (closed-vocab only; n/a for free text). F2 = F1 weighting recall higher.", "higher"],
      ["Agreement / self-consistency", "How often models agree on a reference, and how often one model repeats its own answer under resampling.", "higher"],
      ["Confusion matrix · prompt lineage · optimizer progress", "Where errors cluster; the accepted/rejected prompt history; the validation score per optimizer iteration.", "diagnostic"],
    ],
  },
];

export function Methodology({ thresholds }: { thresholds: Thresholds | null }) {
  return (
    <details className="panel methodology">
      <summary>How to read this dashboard</summary>

      <p className="muted">
        <strong>What to look at first:</strong> the <strong>Quality</strong> column (and the
        leaderboard) is the production gate — <strong>F1</strong> for list fields and{" "}
        <strong>accuracy</strong> for categorical fields; green passes the 90% gate. Precision &amp;
        recall (lists) or Cohen's κ (categorical) explain <em>why</em>; <em>Concordance</em> (an
        independent LLM judge) corroborates it; cost &amp; CO₂e show efficiency. Everything else is
        diagnostic.
      </p>

      <details className="method-group">
        <summary>Threshold rationale &amp; references</summary>
        <p className="muted">
          <strong>List fields (authors, affiliations, countries) — F1 ≥ 90%.</strong>{" "}
          Ground truth is verifiable (a name either appears in the paper or it doesn't), so a high
          bar is justified. Element-level F1 balances precision and recall symmetrically, penalising
          both missed names and hallucinated ones.
        </p>
        <p className="muted">
          <strong>Categorical fields (sector, sub-sector) — accuracy ≥ 90%.</strong>{" "}
          This threshold is aspirational and serves as a direction-of-travel target. Published
          benchmarks for structurally comparable tasks (11–28 imbalanced classes, specialised domain,
          zero-shot LLMs) show state-of-the-art performance of 40–58% macro-F1 and 60–72% accuracy:{" "}
          OSDG trained classifier on 17 SDG categories reaches ~70% F1 (Pukelis et al. 2022,{" "}
          <em>arXiv:2211.11252</em>); zero-shot GPT-4 on SDG detection reaches macro-F1 45–58%
          (Cadeddu et al. 2025, <em>arXiv:2509.19833</em>); ChatGPT on rare NER classes drops to
          F1 4–27% (Qin et al. 2023, <em>arXiv:2302.06476</em>). The 90% gate therefore functions
          as a ceiling that the optimizer works toward; it is not expected to be cleared by baseline
          zero-shot prompts alone. A field stuck at 60–70% accuracy signals that prompt engineering
          has reached its ceiling and a ground-truth or taxonomy review is needed (Loop B).
        </p>
        <p className="muted">
          Cohen's κ is reported alongside accuracy as a diagnostic: it penalises majority-class
          exploitation (e.g. always guessing "Health" when 44% of papers are health papers) that raw
          accuracy rewards. κ ≥ 0.60 corresponds to "substantial agreement" (Landis &amp; Koch 1977)
          and is the threshold used in inter-annotator agreement studies in the same domain.
        </p>
      </details>

      <details className="method-group" open>
        <summary>The pipeline at a glance</summary>
        <MermaidDiagram
          chart={PIPELINE_CHART}
          caption="Extract → score → gate → (reflect/rewrite & re-test | advance). Gate 90% (F1 for lists, accuracy for categorical); the optimizer accepts a rewrite only if it improves on a held-out set and generalizes across a second model."
        />
      </details>

      <details className="method-group">
        <summary>Metric glossary</summary>
        <table className="glossary">
          <thead>
            <tr>
              <th>Metric</th>
              <th>What it means</th>
              <th>Better</th>
            </tr>
          </thead>
          <tbody>
            {TIERS.map((t) => (
              <Fragment key={t.tier}>
                <tr className="glossary-tier">
                  <td colSpan={3}>{t.tier}</td>
                </tr>
                {t.rows.map(([metric, meaning, better]) => (
                  <tr key={metric}>
                    <td className="glossary-metric">{metric}</td>
                    <td>{meaning}</td>
                    <td className="glossary-dir">{better}</td>
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
        {thresholds && (
          <p className="muted glossary-note">
            Live thresholds: correct ≥ {thresholds.correct_threshold.toFixed(2)} (0–1), fuzzy-match ≥{" "}
            {thresholds.fuzzy_match_threshold}/100, optimizer accept margin ≥ {thresholds.improvement_epsilon}.
          </p>
        )}
      </details>

      <details className="method-group">
        <summary>When a field is "done" &amp; where humans decide</summary>
        <p className="muted">
          <strong>Good enough:</strong> a (field, model) clears the gate (F1/accuracy ≥ 90%) with a
          tight 95% CI. <strong>Long enough:</strong> the rollout is capped at 300 references, the
          optimizer stops after 4 non-improving iterations, and a field is "converged" once every
          model passes the gate or the optimizer is exhausted. A field stuck below the gate is often
          a signal to fix the <em>ground truth</em>, not the prompt.
        </p>
        <p className="muted">
          This automates the loop but keeps <strong>humans on the loop</strong>: the model's own
          uncertainty (an honest abstention) is the first tripwire that pulls a person in — plus
          stuck fields, ground-truth corrections, fabricated-excerpt flags, and all
          policy/threshold and code changes. Humans own the rules and the answer key; the machine
          does the repetitive work and surfaces what needs a decision.
        </p>
      </details>
    </details>
  );
}
