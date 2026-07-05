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

        <dt>Confidence interval (the ± band under threshold accuracy) — narrower means more certain</dt>
        <dd>
          Every accuracy here is measured on a <em>sample</em> of references, so it's an estimate,
          not the exact truth. The small bar under "threshold accuracy" is the{" "}
          <strong>95% confidence interval</strong> (Wilson score): the range the true accuracy is
          very likely in. This is the <strong>central limit theorem</strong> at work — as the
          rollout grows the sample (30 → 60 → 100 references), the interval shrinks by roughly{" "}
          <em>1 ÷ √(sample size)</em>, so the estimate gets sharper and more trustworthy. A wide
          band on a model with few references means "not enough data to be sure yet" (the fix is
          more references) rather than necessarily an unreliable model — and you can literally watch
          the band tighten as each rollout stage completes.
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

        <dt>
          Honesty: abstention, hallucination &amp; wrong rates (a breakdown, not a single score)
        </dt>
        <dd>
          The three accuracy numbers above only ask "was the answer right?" — they treat an honest{" "}
          <em>"I don't know"</em> exactly like a confident wrong answer (both score 0). For a
          systematic-review database that's misleading: a wrong value silently corrupts the data and
          looks trustworthy, whereas a blank flags "a human needs to check this" and is safely
          recoverable. So every answer is also sorted into one of four outcomes:
          <ul>
            <li>
              <strong>Correct</strong> — matched the expected value (or correctly reported nothing
              when there was nothing to report).
            </li>
            <li>
              <strong>Abstained</strong> — returned nothing (or, for list fields like authors,
              reported only some values without inventing wrong ones) when a value did exist. An
              honest miss.
            </li>
            <li>
              <strong>Wrong</strong> — gave a confident value that didn't match.
            </li>
            <li>
              <strong>Hallucination</strong> — invented a value when there was nothing to report.
              The worst case for data quality.
            </li>
          </ul>
          The <strong>abstention / hallucination / wrong rates</strong> on each model card are just
          how often it landed in each bucket. Lower hallucination and wrong rates are better; a
          modest abstention rate is <em>healthy</em> — it means the model declines rather than
          guesses when the paper is unclear.
        </dd>

        <dt>Honesty-adjusted score — higher is better</dt>
        <dd>
          A single 0–1 number that rewards calibrated honesty. A correct answer scores 1.0 and a
          confident wrong answer or hallucination scores 0.0, exactly as before — but an honest
          abstention scores <strong>0.5</strong> instead of 0.0. <strong>Why 0.5?</strong> Because
          an abstention sits deliberately <em>between</em> right and wrong: it's clearly worse than
          getting the answer (someone still has to fill the field in by hand), but clearly better
          than a wrong value (which looks trustworthy and quietly pollutes the database). Half
          credit puts it exactly in the middle — "worth half a correct answer, and never worse than
          a confident mistake." It's a deliberate policy choice, not a measured constant: raise it
          if a wrong value is very costly or hard to catch downstream, lower it if a missing value
          is nearly as costly as a wrong one (the knob is <code>ABSTENTION_CREDIT</code> in the
          backend). Only this honesty-adjusted score is used to <em>steer the prompt optimizer</em>,
          so it learns to make models say "I don't know" rather than bluff; the plain accuracy
          numbers above are left untouched for comparability.
        </dd>

        <dt>Confidence signals: token confidence, cross-model agreement, self-consistency</dt>
        <dd>
          Three complementary hints at <em>how sure</em> an answer is — separate from whether it's
          actually correct, and none of them need the ground truth:
          <ul>
            <li>
              <strong>Avg token confidence</strong> — the model's own average per-token probability
              for its answer (from "logprobs", when the provider exposes them). Higher means the
              model was less "surprised" by its own words. Only captured on runs done with the{" "}
              <code>--logprobs</code> option, and blank for models that don't return it.
            </li>
            <li>
              <strong>Cross-model agreement</strong> — how often this model's answer matches the
              other models on the same reference. A value the whole panel converges on is more
              trustworthy than a lone outlier. Free — computed from runs we already have.
            </li>
            <li>
              <strong>Self-consistency</strong> — ask the same model the same question several times
              with a little randomness; the share of times it returns the same answer. Low means
              it's essentially guessing on that reference. This costs several calls per reference, so
              it's run only as an occasional validation study on a small sample (blank until then).
            </li>
          </ul>
          These are <em>calibration</em> signals — a well-behaved model should be right more often
          when these are high — and unlike a model simply asserting "I'm 90% sure", they can't be
          faked by an overconfident model.
        </dd>

        <dt>Excerpt verified — higher is better (an anti-fabrication check)</dt>
        <dd>
          The prompt asks each model to quote the exact line from the paper that its answer came
          from. "Excerpt verified" is the share of a model's answers whose quoted line was actually
          found in the source text. A low number means the model is <strong>fabricating its
          evidence</strong> — citing quotes that aren't in the paper — which is a red flag even when
          the answer happens to be right. Because a made-up quote is a form of dishonesty, an answer
          with an unverifiable excerpt also has its <em>honesty-adjusted score</em> docked (the
          plain accuracy numbers are left alone), which pushes the optimizer toward prompts that
          make models quote real text.
        </dd>

        <dt>Confidence calibration &amp; Brier score — lower Brier is better</dt>
        <dd>
          We also ask each model to <em>state</em> how confident it is (0–1) in every answer. That
          number is meaningless on its own — an overconfident model can just say "1.0" — so we judge
          it by <strong>calibration</strong>: across many answers, when a model says it's 80% sure,
          is it actually right about 80% of the time? The <strong>Brier score</strong> is the
          average squared gap between the stated confidence and whether the answer was right (0 =
          perfect; lower is better). The <strong>reliability diagram</strong> plots stated confidence
          (x) against actual accuracy (y): points on the dashed line are perfectly calibrated, points
          below it mean the model was overconfident, above it underconfident. This is only a
          diagnostic — stated confidence is never folded into the accuracy or honesty scores.
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
