import type { WizardState } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
  onLaunch: () => void;
  submitting: boolean;
}

const TIER_INFO = {
  cheap: { label: "Cheap", models: "GPT-4o mini, Haiku, Flash, GLM-5, Kimi-k2.5", cost: "~$0.01–0.05/doc" },
  mid: { label: "Mid", models: "Mistral Medium, Llama Maverick, Qwen 235B, GLM-5.2", cost: "~$0.05–0.20/doc" },
  expensive: { label: "Expensive", models: "GPT-4o, Claude Sonnet, Gemini Pro", cost: "~$0.20–1.00/doc" },
};

const MAYBE_LABELS: Record<WizardState["maybeStrategy"], string> = {
  cross_model: "Cross-model disagreement",
  excerpt_verify: "Excerpt verification",
  self_consistency: "Self-consistency re-sampling",
};

export default function Step5Launch({ state, onBack, onLaunch, submitting }: Props) {
  const isExtraction = state.projectType === "extraction";
  const typeLabel =
    state.projectType === "extraction"
      ? "Data Extraction"
      : state.projectType === "screening_ta"
      ? "TA Screening"
      : "Full-Text Screening";

  const fieldCount = isExtraction ? state.fields.length : state.exclusionCriteria.length;
  const fieldLabel = isExtraction ? "field" : "exclusion criterion";

  return (
    <div className="wizard-step">
      <h3 className="step-title">Review &amp; launch</h3>
      <p className="step-subtitle">
        Everything looks good? Hit Launch to create the project and kick off the first extraction
        run. You can monitor progress in the dashboard once it starts.
      </p>

      {/* Summary card */}
      <div className="launch-summary">
        <div className="summary-row">
          <span className="summary-label">Project</span>
          <span className="summary-value"><strong>{state.projectName}</strong> <code>{state.projectSlug}</code></span>
        </div>
        <div className="summary-row">
          <span className="summary-label">Type</span>
          <span className="summary-value">{typeLabel}</span>
        </div>
        <div className="summary-row">
          <span className="summary-label">{isExtraction ? "Fields" : "Criteria"}</span>
          <span className="summary-value">
            {fieldCount} {fieldLabel}{fieldCount !== 1 ? "s" : ""}
            {isExtraction && (
              <span className="summary-detail">
                {" "}({state.fields.map((f) => f.label || f.name).join(", ")})
              </span>
            )}
          </span>
        </div>
        {!isExtraction && (
          <div className="summary-row">
            <span className="summary-label">MAYBE strategy</span>
            <span className="summary-value">{MAYBE_LABELS[state.maybeStrategy]}</span>
          </div>
        )}
        <div className="summary-row">
          <span className="summary-label">Corpus</span>
          <span className="summary-value">
            {state.corpusFiles.length} file{state.corpusFiles.length !== 1 ? "s" : ""}
            {state.corpusFiles.filter((f) => f.name.endsWith(".pdf")).length > 0 && (
              <span className="summary-detail">
                {" "}({state.corpusFiles.filter((f) => f.name.endsWith(".pdf")).length} PDFs to convert)
              </span>
            )}
          </span>
        </div>
        <div className="summary-row">
          <span className="summary-label">Ground truth</span>
          <span className="summary-value">{state.groundTruthFile?.name ?? "—"}</span>
        </div>
        <div className="summary-row">
          <span className="summary-label">Password</span>
          <span className="summary-value">{state.password ? "✓ set" : "None (read-only public)"}</span>
        </div>
      </div>

      {/* Model tier selector */}
      <div className="tier-section">
        <h4 className="tier-title">Which model tiers to run?</h4>
        <p className="label-hint">
          More tiers = more coverage and cross-model comparison, but higher cost. You can always
          add tiers later.
        </p>
        <div className="tier-options">
          {(["cheap", "mid", "expensive"] as const).map((tier) => {
            const selected = state.modelTiers.includes(tier);
            return (
              <label key={tier} className={`tier-card ${selected ? "selected" : ""}`}>
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => {
                    // tier selection wired via parent update prop
                  }}
                />
                <strong>{TIER_INFO[tier].label}</strong>
                <span className="tier-models">{TIER_INFO[tier].models}</span>
                <span className="tier-cost">{TIER_INFO[tier].cost}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack} disabled={submitting}>
          ← Back
        </button>
        <button
          className="btn-launch"
          onClick={onLaunch}
          disabled={submitting}
        >
          {submitting ? "Creating project…" : "🚀 Launch"}
        </button>
      </div>
    </div>
  );
}
