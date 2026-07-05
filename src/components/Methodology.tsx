import type { Thresholds } from "../api";

export function Methodology({ thresholds }: { thresholds: Thresholds | null }) {
  return (
    <details className="panel methodology">
      <summary>How to read this dashboard</summary>

      <p className="muted">
        There are <strong>three different ways</strong> this dashboard checks whether a model's
        answer was "correct" for a given reference (paper). They can disagree with each other —
        that's expected, not a bug — because they're answering slightly different questions.
      </p>

      <dl>
        <dt>1. Threshold accuracy — correct references ÷ total references (higher is better)</dt>
        <dd>
          The simplest of the three: for every reference, the model's answer is marked correct or
          not, then we divide by how many references there were. A reference counts as correct if
          its score is ≥{" "}
          <strong>{thresholds ? thresholds.correct_threshold.toFixed(2) : "…"}</strong> on a 0–1
          scale (the "correct threshold") — generous enough to allow near-matches (see "fuzzy match
          threshold" below — a <strong>different</strong> number, on a <strong>different</strong>{" "}
          0–100 scale, used one step earlier in the process). E.g. the model saying <em>"WHO"</em>{" "}
          when the correct answer is <em>"World Health Organization"</em> still counts as correct,
          since they mean the same thing even though the text differs.
        </dd>

        <dt>2. Exact-match accuracy (shown in each model's confusion matrix) — higher is better</dt>
        <dd>
          Also correct ÷ total, but much stricter: only a word-for-word identical answer counts.
          Using the same example, <em>"WHO"</em> vs <em>"World Health Organization"</em> would count
          as <strong>wrong</strong> here, even though a person would say they mean the same thing.
          This is why exact-match accuracy is usually lower than threshold accuracy for the same
          model — it's not measuring a different model, just being stricter about what "matching"
          means.
        </dd>

        <dt>3. LLM-judged accuracy — higher is better (the most trustworthy of the three, when available)</dt>
        <dd>
          Instead of comparing text at all, a separate AI model reads the model's answer and the
          correct answer side by side and decides, the way a person would, whether they mean the
          same thing — catching cases like <em>"WHO"</em> = <em>"World Health Organization"</em>{" "}
          that exact-match would wrongly mark wrong, without the risk of the more lenient
          threshold-accuracy rule accepting two answers that only look similar as text but actually
          mean different things. This is only available for the subset of references someone has
          explicitly had this second AI review — the number in parentheses next to the percentage
          is how many references that covers.
        </dd>

        <dt>Fuzzy match threshold (a setting, not a "higher is better" metric)</dt>
        <dd>
          <strong>
            This is a different number, on a different scale, from the "correct threshold" above —
            they are not meant to be the same value.
          </strong>{" "}
          Scoring happens in two steps: <em>first</em>, rapidfuzz compares the predicted and
          correct text and gives a 0–100 similarity score — if that similarity is ≥{" "}
          <strong>{thresholds ? thresholds.fuzzy_match_threshold : "…"}</strong> out of 100 (e.g.
          minor spelling differences, abbreviations, or reordered words), the pair is treated as a
          "fuzzy match" and given a score of exactly 0.9 (out of the 0–1 scale used everywhere
          else). <em>Second</em>, that 0–1 score is compared against the "correct threshold" above
          to decide if it counts as correct. Raising this 0–100 number makes fuzzy-matching
          stricter (fewer near-matches qualify); lowering it makes it more lenient. There's no
          universally "correct" value — <code>llm_judge.py</code> exists to empirically
          sanity-check it.
        </dd>

        <dt>Sensitivity (recall) — higher is better</dt>
        <dd>
          Of everything that should have been extracted, the share the model actually found (true
          positives / (true positives + false negatives)). Low sensitivity means the model is
          <strong> under-reporting</strong> — missing values it should be reporting, e.g. only
          catching the first author's country when a paper has several.
        </dd>

        <dt>Specificity — higher is better</dt>
        <dd>
          Of everything that should <em>not</em> be reported for a record, the share the model
          correctly left out (true negatives / (true negatives + false positives)). Low specificity
          means the model is <strong>over-reporting</strong> — guessing values it shouldn't. For
          single-value fields (sector, sub-sector) and closed-vocabulary list fields (country, from
          a fixed taxonomy), this is well-defined and shown. For open-vocabulary list fields
          (authors, institutions — free text, no fixed list of possible values) there's no fixed
          set of "negatives" to measure against, so it shows as <strong>n/a</strong>.
        </dd>

        <dt>F2 score — higher is better</dt>
        <dd>
          Like F1, but weights recall higher than precision (missing a value is penalized more
          than an extra/wrong one) — useful here since under-reporting (e.g. missing a co-author's
          country) is usually the more costly mistake for this database.
        </dd>

        <dt>Confusion matrix (no single "better" direction — a diagnostic, not a score)</dt>
        <dd>
          For single-value fields (sector, sub-sector): rows = ground truth, columns = predicted,
          diagonal = correct. For list fields (authors, institutions, countries), a literal matrix
          isn't meaningful (open-set, multi-label), so matched/extra/missing item counts are shown
          instead. Use it to see <em>where</em> a model goes wrong (which categories get confused
          with which), not just how often.
        </dd>

        <dt>Prompt lineage (no "better" direction — a history log)</dt>
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
