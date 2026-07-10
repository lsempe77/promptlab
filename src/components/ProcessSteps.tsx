/**
 * ProcessSteps — the plain-English "how it works" for a first-time viewer.
 * Three steps, no jargon. The full technical pipeline (GEPA-lite optimizer,
 * reflector, holdout gates) stays opt-in in the Methodology panel below.
 */
const STEPS: { n: number; title: string; body: string }[] = [
  {
    n: 1,
    title: "Give the AI papers + a human answer key",
    body:
      "Each research paper is sent to the AI along with the correct value a human already recorded, so every answer can be checked.",
  },
  {
    n: 2,
    title: "Score it, then auto-improve the prompt",
    body:
      "The AI extracts the field; we score it against the answer key and automatically rewrite the prompt, keeping a change only if it clears the 90% bar and generalises.",
  },
  {
    n: 3,
    title: "A human reviews the hard cases",
    body:
      "Fields that stay stuck, low-confidence answers, and fabricated-source flags pull a person in to decide. Humans own the rules and the answer key.",
  },
];

export function ProcessSteps() {
  return (
    <section className="process-steps panel">
      <h3 className="process-steps-title">How it works</h3>
      <ol className="process-steps-grid">
        {STEPS.map((s) => (
          <li key={s.n} className="process-step">
            <span className="process-step-num">{s.n}</span>
            <div className="process-step-text">
              <span className="process-step-title">{s.title}</span>
              <span className="process-step-body muted">{s.body}</span>
            </div>
          </li>
        ))}
      </ol>
      <p className="process-steps-note muted">
        Full technical pipeline (optimizer, LLM judge, holdout gates) is in
        “How to read this dashboard” below.
      </p>
    </section>
  );
}
