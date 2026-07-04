export function About() {
  return (
    <div className="about">
      <section className="panel">
        <h2>What this is</h2>
        <p>
          3ie runs an evidence-synthesis pipeline over impact-evaluation studies: title/abstract
          screening (TAS), full-text screening (FTS), then <strong>data extraction</strong> — pulling
          structured metadata out of each paper (authors, institutions, countries, sector, sub-sector,
          and more downstream). This prompt lab is where extraction prompts for LLMs are built, tested
          against a human-curated ground truth, and iteratively improved.
        </p>
      </section>

      <section className="panel">
        <h2>The pipeline, end to end</h2>
        <ol className="about-steps">
          <li>
            <strong>Ground truth.</strong> A human-curated spreadsheet of ~7,700 studies is joined
            against the QA'd paper corpus (markdown text per study), giving every field (authors,
            affiliation, country, sector, sub-sector) a known-correct value per study. This
            deployment runs against a fixed, deliberately-sampled 300-study subset (studies with
            complete ground truth across all 5 fields) rather than the full ~7,700 — kept small on
            purpose so the production dataset stays cheap to host and reproduce.
          </li>
          <li>
            <strong>Extraction run.</strong> For a chosen field, model, and prompt version, every
            sampled study's markdown is sent to an LLM (via OpenRouter, one unified gateway across
            ~10–20 models) with the current prompt. The model's JSON response is parsed into the
            field's expected shape (a single value, or a list).
          </li>
          <li>
            <strong>Scoring.</strong> Each prediction is compared to ground truth: exact/fuzzy string
            match for single-value fields (sector, sub-sector), set-based precision/recall/F1 for
            list fields (authors, institutions, countries). A run counts as "correct" if its score
            clears the correct-threshold (see the Methodology panel on the Dashboard tab for the
            current value).
          </li>
          <li>
            <strong>Optimizer (GEPA-lite).</strong> Starting from a baseline prompt, each iteration:
            evaluates the current best prompt on a small minibatch, has a "reflector" LLM look at the
            failures and propose up to N revised prompt candidates (avoiding instructions already
            tried and rejected), evaluates every candidate on a held-out validation set, and keeps
            the best one only if it beats the incumbent by a meaningful margin. Otherwise the
            optimizer stops after a few iterations with no improvement. Every candidate — accepted or
            rejected — is kept permanently for provenance.
          </li>
          <li>
            <strong>This dashboard.</strong> A read-only FastAPI layer over the same SQLite database
            exposes fields, prompt-version lineage, per-model comparisons, confusion
            matrices/F-scores, and optimizer iteration history, which this React app renders.
          </li>
        </ol>
      </section>

      <section className="panel">
        <h2>Why an LLM judge exists too</h2>
        <p>
          The automated scorer is deliberately simple string matching — fast and free, but not a
          semantic judgment. A separate <code>llm_judge.py</code> tool asks an LLM to independently
          confirm true/false for a sample of logged runs, and reports how well different threshold
          values would agree with that judgment. This is used to sanity-check and tune the
          correct-threshold empirically, rather than by guesswork alone.
        </p>
      </section>

      <section className="panel">
        <h2>Architecture notes</h2>
        <ul className="about-list">
          <li>Backend (FastAPI + SQLite, no ORM/task queue) is deployed on Fly.io as an always-on
            read-only API, serving the fixed 300-study production dataset described above.</li>
          <li>This frontend is deployed to GitHub Pages (static files only) and fetches from that
            always-on backend, so the dashboard shows real data regardless of whether anyone's
            laptop is on.</li>
          <li>Every prompt version, run, and optimizer iteration is preserved for full provenance/debugging.</li>
        </ul>
      </section>
    </div>
  );
}
