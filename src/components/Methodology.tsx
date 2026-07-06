import { Fragment } from "react";
import type { Thresholds } from "../api";
import { MermaidDiagram } from "./MermaidDiagram";

const PIPELINE_CHART = `flowchart TD
    GT["Ground-truth reference set<br/>(human-curated)"] --> EX
    P["Current prompt<br/>(baseline or optimized)"] --> EX["Extraction:<br/>run field across all models"]
    EX --> SC["Score vs ground truth<br/>(concordance-aware match)"]
    SC --> GATE{"Per-model gate:<br/>F1 (lists) / accuracy (categorical) &ge; 90%?"}
    GATE -- "no (gated)" --> REFLECT["Reflector model:<br/>diagnose failures,<br/>propose revised prompt"]
    GATE -- "yes" --> STAGE{"Sample size<br/>reached this stage?"}
    STAGE -- "100 -> grow" --> G200["Extract to 200 refs"]
    STAGE -- "200 -> grow" --> G300["Extract to 300 refs"]
    STAGE -- "300 (capped)" --> DONE(["Production-ready<br/>(field, model) pairs"])
    G200 --> EX
    G300 --> EX
    REFLECT --> RETEST["Re-test candidate on a 50-paper<br/>held-out set + cross-model holdout"]
    RETEST --> BETTER{"Improves & generalizes?"}
    BETTER -- "yes" --> ACCEPT["Accept -> new prompt version"]
    BETTER -- "no" --> REJECT["Reject (bold rewrite after 2,<br/>stop after 4 no-improve)"]
    ACCEPT --> P`;

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
    </details>
  );
}
