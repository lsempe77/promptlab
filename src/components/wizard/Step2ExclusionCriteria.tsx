import { useState } from "react";
import type { WizardState, ExclusionCriterion } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
}

function newCriterion(order: number): ExclusionCriterion {
  return {
    id: crypto.randomUUID(),
    tag: "",
    label: "",
    question: "",
    order,
  };
}

function CriterionRow({
  criterion,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
}: {
  criterion: ExclusionCriterion;
  onChange: (c: ExclusionCriterion) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  return (
    <div className="criterion-row">
      <div className="criterion-order">
        <button onClick={onMoveUp} disabled={isFirst} title="Move up">▲</button>
        <span>{criterion.order}</span>
        <button onClick={onMoveDown} disabled={isLast} title="Move down">▼</button>
      </div>
      <div className="criterion-fields">
        <div className="field-row-2">
          <input
            className="wizard-input"
            placeholder="EPPI tag (verbatim) e.g. wrong_population"
            value={criterion.tag}
            onChange={(e) => onChange({ ...criterion, tag: e.target.value })}
          />
          <input
            className="wizard-input"
            placeholder="Display label e.g. Wrong population"
            value={criterion.label}
            onChange={(e) => onChange({ ...criterion, label: e.target.value })}
          />
        </div>
        <input
          className="wizard-input"
          placeholder="Yes/no question the model answers — e.g. 'Does this study involve exclusively non-human subjects?'"
          value={criterion.question}
          onChange={(e) => onChange({ ...criterion, question: e.target.value })}
        />
      </div>
      <button className="criterion-remove" onClick={onRemove} title="Remove">✕</button>
    </div>
  );
}

const MAYBE_OPTIONS: { value: WizardState["maybeStrategy"]; label: string; detail: string }[] = [
  {
    value: "cross_model",
    label: "Cross-model disagreement",
    detail:
      "Run 2–3 diverse models. If they all agree → take the decision. If they disagree → MAYBE. No extra cost: reuses the multi-model runs already planned.",
  },
  {
    value: "excerpt_verify",
    label: "Excerpt verification (recommended)",
    detail:
      "Model must cite verbatim text justifying each EXCLUDE. If no specific excerpt can be found in the document → MAYBE. Confidence comes from evidence, not from the model's self-reported score.",
  },
  {
    value: "self_consistency",
    label: "Self-consistency re-sampling",
    detail:
      "Run the same model 3× with slight temperature variation. 3/3 agree → confident. 2/3 or 1/3 → MAYBE. Adds ~3× cost for screened documents.",
  },
];

export default function Step2ExclusionCriteria({ state, update, onNext, onBack }: Props) {
  const criteria = state.exclusionCriteria;
  const setCriteria = (c: ExclusionCriterion[]) =>
    update({ exclusionCriteria: c.map((x, i) => ({ ...x, order: i + 1 })) });

  const addCriterion = () =>
    setCriteria([...criteria, newCriterion(criteria.length + 1)]);

  const removeCriterion = (id: string) =>
    setCriteria(criteria.filter((c) => c.id !== id));

  const changeCriterion = (id: string, c: ExclusionCriterion) =>
    setCriteria(criteria.map((x) => (x.id === id ? c : x)));

  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...criteria];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    setCriteria(next);
  };

  const moveDown = (idx: number) => {
    if (idx === criteria.length - 1) return;
    const next = [...criteria];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    setCriteria(next);
  };

  // Bulk-paste helper: EPPI-style "tag: label" lines
  const [bulkText, setBulkText] = useState("");
  const applyBulkPaste = () => {
    const lines = bulkText
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);
    const parsed: ExclusionCriterion[] = lines.map((line, i) => {
      const [tag, ...rest] = line.split(/[:\-–]\s*/);
      return {
        id: crypto.randomUUID(),
        tag: tag.trim().toLowerCase().replace(/\s+/g, "_"),
        label: rest.join(": ").trim() || tag.trim(),
        question: "",
        order: criteria.length + i + 1,
      };
    });
    setCriteria([...criteria, ...parsed]);
    setBulkText("");
  };

  const canContinue =
    criteria.length > 0 &&
    criteria.every((c) => c.tag && c.question);

  const screeningLabel =
    state.projectType === "screening_ta" ? "Title & Abstract" : "Full-Text";

  return (
    <div className="wizard-step">
      <h3 className="step-title">Exclusion criteria — {screeningLabel} Screening</h3>
      <p className="step-subtitle">
        List your eligibility criteria in hierarchical order (most diagnostic first). The model
        checks them in order and stops at the first that matches — exactly as EPPI-Reviewer works.
        Tags must match your EPPI taxonomy <strong>verbatim</strong>.
      </p>

      {/* Bulk paste */}
      <details className="bulk-paste-section">
        <summary>Paste criteria from EPPI / spreadsheet</summary>
        <p className="label-hint">One criterion per line in format: <code>tag: Label</code> or just <code>Label</code></p>
        <textarea
          className="wizard-input monospace"
          rows={5}
          placeholder={"wrong_population: Wrong population\nwrong_design: Wrong study design\nwrong_outcome: Wrong outcome"}
          value={bulkText}
          onChange={(e) => setBulkText(e.target.value)}
        />
        <button className="btn-secondary" onClick={applyBulkPaste} disabled={!bulkText.trim()}>
          Import
        </button>
      </details>

      {/* Criteria list */}
      <div className="criteria-list">
        {criteria.length === 0 && (
          <p className="empty-hint">No criteria yet — add one below or paste from EPPI above.</p>
        )}
        {criteria.map((c, i) => (
          <CriterionRow
            key={c.id}
            criterion={c}
            onChange={(nc) => changeCriterion(c.id, nc)}
            onRemove={() => removeCriterion(c.id)}
            onMoveUp={() => moveUp(i)}
            onMoveDown={() => moveDown(i)}
            isFirst={i === 0}
            isLast={i === criteria.length - 1}
          />
        ))}
      </div>

      <button className="btn-add-field" onClick={addCriterion}>+ Add criterion</button>

      {/* MAYBE strategy */}
      <div className="maybe-section">
        <h4 className="maybe-title">
          How should MAYBE / uncertain decisions be handled?
        </h4>
        <p className="maybe-subtitle">
          The model never self-reports a probability — that's uncalibrated. Instead, choose a
          structural signal:
        </p>
        <div className="maybe-options">
          {MAYBE_OPTIONS.map((opt) => (
            <label
              key={opt.value}
              className={`maybe-option ${state.maybeStrategy === opt.value ? "selected" : ""}`}
            >
              <input
                type="radio"
                name="maybe-strategy"
                value={opt.value}
                checked={state.maybeStrategy === opt.value}
                onChange={() => update({ maybeStrategy: opt.value })}
              />
              <div>
                <strong>{opt.label}</strong>
                <p>{opt.detail}</p>
              </div>
            </label>
          ))}
        </div>
      </div>

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button className="btn-primary" onClick={onNext} disabled={!canContinue}>
          Continue →
        </button>
      </div>
    </div>
  );
}
