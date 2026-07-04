import type { Thresholds } from "../api";

export function Methodology({ thresholds }: { thresholds: Thresholds | null }) {
  return (
    <details className="panel methodology">
      <summary>How to read this dashboard</summary>
      <dl>
        <dt>Mean score</dt>
        <dd>
          Average 0–1 score across runs for a model on this field. Single-value fields score 1.0
          (exact match), 0.9 (fuzzy match), or 0.0 (mismatch). List fields (authors, institutions,
          countries) score the F1 of precision/recall between the predicted and ground-truth list.
        </dd>

        <dt>Accuracy</dt>
        <dd>
          Share of runs with score ≥{" "}
          <strong>{thresholds ? thresholds.correct_threshold.toFixed(2) : "…"}</strong> (the
          "correct threshold"). This is intentionally strict — a run must be a near-exact match to
          count as correct.
        </dd>

        <dt>Fuzzy match threshold</dt>
        <dd>
          String-similarity score (0–100, via rapidfuzz) required for two differently worded
          values to be treated as equivalent — e.g. minor spelling/formatting differences. Currently{" "}
          <strong>{thresholds ? thresholds.fuzzy_match_threshold : "…"}</strong>.
        </dd>

        <dt>Prompt lineage</dt>
        <dd>
          Every prompt the optimizer tries gets a permanent version row. <strong>Accepted</strong>{" "}
          versions became the new baseline (they beat the previous incumbent by more than{" "}
          {thresholds ? thresholds.improvement_epsilon : "…"}); <strong>rejected</strong> versions
          were tried and discarded, kept only for provenance/debugging.
        </dd>

        <dt>Optimizer progress</dt>
        <dd>
          Mean validation-set score of the best candidate at each optimizer iteration. A flat or
          declining line after an iteration means the optimizer stopped improving and reverted to
          the prior best prompt.
        </dd>
      </dl>
    </details>
  );
}
