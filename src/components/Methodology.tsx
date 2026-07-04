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

        <dt>Sensitivity (recall)</dt>
        <dd>
          Of everything that should have been extracted, the share the model actually found (true
          positives / (true positives + false negatives)). Low sensitivity means the model is
          missing values it should be reporting — e.g. only catching the first author's country
          when a paper has several.
        </dd>

        <dt>Specificity</dt>
        <dd>
          Of everything that should <em>not</em> be reported for a record, the share the model
          correctly left out (true negatives / (true negatives + false positives)). For
          single-value fields (sector, sub-sector) and closed-vocabulary list fields (country, from
          a fixed taxonomy), this is well-defined and shown. For open-vocabulary list fields
          (authors, institutions — free text, no fixed list of possible values) there's no fixed
          set of "negatives" to measure against, so it shows as <strong>n/a</strong>.
        </dd>

        <dt>F2 score</dt>
        <dd>
          Like F1, but weights recall higher than precision (missing a value is penalized more
          than an extra/wrong one) — useful here since under-reporting (e.g. missing a co-author's
          country) is usually the more costly mistake for this database.
        </dd>

        <dt>Confusion matrix</dt>
        <dd>
          For single-value fields (sector, sub-sector): rows = ground truth, columns = predicted,
          diagonal = correct. For list fields (authors, institutions, countries), a literal matrix
          isn't meaningful (open-set, multi-label), so matched/extra/missing item counts are shown
          instead. Computed per model — see each model's own card.
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
          Validation-set score (y-axis, 0–1) of the best candidate prompt at each optimizer
          iteration (x-axis) for that model. A flat or declining line after an iteration means the
          optimizer stopped improving and reverted to the prior best prompt.
        </dd>
      </dl>
    </details>
  );
}
