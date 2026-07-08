import type { WizardState } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
  onLaunch: () => void;
  submitting: boolean;
}

/** DEP-validated model roster. Excludes glm-4.7-flash (broken json_mode),
 *  gemini-pro (0.075 F1 on authors), kimi-* (poor list-field performance).
 *  Metrics are production gate values from the DEP 2026-07 run. */
const CURATED_MODELS = [
  {
    id: "~anthropic/claude-sonnet-latest",
    label: "Claude Sonnet",
    provider: "Anthropic",
    tier: "expensive" as const,
    costHint: "$$$",
    depF1: 0.895,
    depCountry: 0.920,
    note: "Best overall across all fields",
    recommended: true,
  },
  {
    id: "~openai/gpt-mini-latest",
    label: "GPT-4o mini",
    provider: "OpenAI",
    tier: "cheap" as const,
    costHint: "$",
    depF1: 0.893,
    depCountry: 0.895,
    note: "Best cost-performance ratio",
    recommended: true,
  },
  {
    id: "~google/gemini-flash-latest",
    label: "Gemini Flash",
    provider: "Google",
    tier: "cheap" as const,
    costHint: "$",
    depF1: 0.878,
    depCountry: 0.914,
    note: "Fast, good on structured fields",
    recommended: true,
  },
  {
    id: "deepseek/deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    provider: "DeepSeek",
    tier: "cheap" as const,
    costHint: "$",
    depF1: 0.840,
    depCountry: 0.912,
    note: "Very cheap; strong on country/sector",
    recommended: false,
  },
  {
    id: "~anthropic/claude-haiku-latest",
    label: "Claude Haiku",
    provider: "Anthropic",
    tier: "cheap" as const,
    costHint: "$",
    depF1: 0.883,
    depCountry: 0.882,
    note: "Fast Anthropic model",
    recommended: false,
  },
  {
    id: "~openai/gpt-latest",
    label: "GPT-4o",
    provider: "OpenAI",
    tier: "expensive" as const,
    costHint: "$$$",
    depF1: 0.866,
    depCountry: 0.915,
    note: "OpenAI flagship",
    recommended: false,
  },
  {
    id: "mistralai/mistral-medium-3-5",
    label: "Mistral Medium 3.5",
    provider: "Mistral",
    tier: "mid" as const,
    costHint: "$$",
    depF1: 0.879,
    depCountry: 0.907,
    note: "EU-hosted option",
    recommended: false,
  },
  {
    id: "qwen/qwen3-235b-a22b-2507",
    label: "Qwen3 235B",
    provider: "Alibaba",
    tier: "mid" as const,
    costHint: "$$",
    depF1: 0.892,
    depCountry: 0.885,
    note: "Strong MoE, near Claude quality",
    recommended: false,
  },
] as const;

const MAYBE_LABELS: Record<WizardState["maybeStrategy"], string> = {
  cross_model: "Cross-model disagreement",
  excerpt_verify: "Excerpt verification",
  self_consistency: "Self-consistency re-sampling",
};

const TIER_COLOR: Record<string, string> = {
  cheap: "#16a34a",
  mid: "#d97706",
  expensive: "#7c3aed",
};

export default function Step5Launch({ state, update, onBack, onLaunch, submitting }: Props) {
  const isExtraction = state.projectType === "extraction";
  const typeLabel =
    state.projectType === "extraction"
      ? "Data Extraction"
      : state.projectType === "screening_ta"
      ? "TA Screening"
      : "Full-Text Screening";

  const fieldCount = isExtraction ? state.fields.length : state.exclusionCriteria.length;
  const fieldLabel = isExtraction ? "field" : "exclusion criterion";
  const selected = state.selectedModels;

  const toggle = (id: string) => {
    update({
      selectedModels: selected.includes(id)
        ? selected.filter((m) => m !== id)
        : [...selected, id],
    });
  };

  const estimatedCost = selected.reduce((acc, id) => {
    const m = CURATED_MODELS.find((m) => m.id === id);
    const perDoc = m?.tier === "expensive" ? 0.40 : m?.tier === "mid" ? 0.12 : 0.03;
    return acc + perDoc;
  }, 0);

  return (
    <div className="wizard-step">
      <h3 className="step-title">Review &amp; launch</h3>
      <p className="step-subtitle">
        Choose which models to run, review the summary, then launch. Models shown are
        DEP-validated — performance metrics are from the 2026 production run.
      </p>

      {/* Model picker */}
      <div className="model-picker-section">
        <div className="model-picker-header">
          <h4 className="picker-title">Models to run</h4>
          <span className="picker-hint">
            {selected.length} selected · est. ~${estimatedCost.toFixed(2)}/doc
          </span>
        </div>
        <div className="model-grid">
          {CURATED_MODELS.map((m) => {
            const isSelected = selected.includes(m.id);
            return (
              <button
                key={m.id}
                className={`model-card ${isSelected ? "selected" : ""} ${m.recommended ? "recommended" : ""}`}
                onClick={() => toggle(m.id)}
                type="button"
              >
                {m.recommended && <span className="model-badge">⭐ Recommended</span>}
                <div className="model-card-top">
                  <span className="model-name">{m.label}</span>
                  <span
                    className="model-tier-chip"
                    style={{ background: TIER_COLOR[m.tier] + "22", color: TIER_COLOR[m.tier] }}
                  >
                    {m.costHint}
                  </span>
                </div>
                <span className="model-provider">{m.provider}</span>
                <div className="model-metrics">
                  <span>Authors <strong>{(m.depF1 * 100).toFixed(0)}%</strong></span>
                  <span>Country <strong>{(m.depCountry * 100).toFixed(0)}%</strong></span>
                </div>
                <span className="model-note">{m.note}</span>
              </button>
            );
          })}
        </div>
      </div>

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
            {isExtraction && state.fields.length > 0 && (
              <span className="summary-detail">
                {" "}({state.fields.map((f) => f.label || f.name).join(", ")})
              </span>
            )}
          </span>
        </div>
        {!isExtraction && (
          <div className="summary-row">
            <span className="summary-label">MAYBE</span>
            <span className="summary-value">{MAYBE_LABELS[state.maybeStrategy]}</span>
          </div>
        )}
        <div className="summary-row">
          <span className="summary-label">Corpus</span>
          <span className="summary-value">{state.corpusFiles.length} file{state.corpusFiles.length !== 1 ? "s" : ""}</span>
        </div>
        <div className="summary-row">
          <span className="summary-label">Ground truth</span>
          <span className="summary-value">{state.groundTruthFile?.name ?? "—"}</span>
        </div>
        <div className="summary-row">
          <span className="summary-label">Models</span>
          <span className="summary-value">
            {selected.length > 0
              ? selected.map((id) => CURATED_MODELS.find((m) => m.id === id)?.label ?? id).join(", ")
              : <em>None selected</em>}
          </span>
        </div>
      </div>

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack} disabled={submitting}>← Back</button>
        <button className="btn-launch" onClick={onLaunch} disabled={submitting || selected.length === 0}>
          {submitting ? "Creating project…" : "🚀 Launch"}
        </button>
      </div>
    </div>
  );
}
