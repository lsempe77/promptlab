import { useRef, useState } from "react";
import type { WizardState, ExclusionCriterion } from "./types";
import { API_BASE_URL } from "../../api";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
  token: string | null;
}

const MAYBE_OPTIONS: { value: WizardState["maybeStrategy"]; label: string; detail: string }[] = [
  { value: "cross_model", label: "Cross-model disagreement", detail: "Run 2–3 diverse models. Agreement → take decision. Disagreement → MAYBE." },
  { value: "excerpt_verify", label: "Excerpt verification (recommended)", detail: "Model must cite verbatim text. No specific excerpt found → MAYBE." },
  { value: "self_consistency", label: "Self-consistency re-sampling", detail: "Run same model 3×. 3/3 agree → confident. Any disagreement → MAYBE." },
];

export default function Step2ScreeningUpload({ state, update, onNext, onBack, token }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [parsed, setParsed] = useState<boolean>(!!state.screeningFile);

  const screeningLabel = state.projectType === "screening_ta" ? "Title & Abstract" : "Full-Text";

  const handleFile = async (file: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("project_type", state.projectType);
      const res = await fetch(`${API_BASE_URL}/api/screening/parse-eppi`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Server error ${res.status}`);
      }
      const data = await res.json();
      // Build criteria from detected tags (ordered by frequency desc)
      const criteria: ExclusionCriterion[] = data.tags.map(
        (t: { tag: string; label: string; count: number }, i: number) => ({
          id: crypto.randomUUID(),
          tag: t.tag,
          label: t.label,
          question: "",
          order: i + 1,
        })
      );
      update({
        screeningFile: file,
        exclusionCriteria: criteria,
        screeningRecordCount: data.total,
        screeningIncludeCount: data.include_count,
        screeningExcludeCount: data.exclude_count,
      });
      setParsed(true);
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const criteria = state.exclusionCriteria;
  const setCriteria = (c: ExclusionCriterion[]) =>
    update({ exclusionCriteria: c.map((x, i) => ({ ...x, order: i + 1 })) });

  const changeCriterion = (id: string, c: ExclusionCriterion) =>
    setCriteria(criteria.map((x) => (x.id === id ? c : x)));
  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...criteria]; [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]]; setCriteria(next);
  };
  const moveDown = (idx: number) => {
    if (idx === criteria.length - 1) return;
    const next = [...criteria]; [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]]; setCriteria(next);
  };

  const canContinue = parsed && criteria.length > 0 && criteria.every((c) => c.question.trim());

  return (
    <div className="wizard-step">
      <h3 className="step-title">Upload your EPPI screening data — {screeningLabel}</h3>
      <p className="step-subtitle">
        Upload your EPPI-Reviewer export (.xlsx). The system reads the corpus (title + abstract),
        ground truth decisions, and auto-detects your exclusion criteria from the unique tags.
        One file does everything.
      </p>

      {/* File upload */}
      {!parsed ? (
        <div
          className={`drop-zone ${uploading ? "uploading" : ""}`}
          onClick={() => !uploading && fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handleFile(f); }}
        >
          <span className="drop-icon">📊</span>
          <p>{uploading ? "Parsing EPPI file…" : "Drop EPPI Excel here, or click to browse"}</p>
          <p className="drop-hint">Expected columns: T1 (title), AB (abstract), U1 (record ID), ta_decision</p>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls,.csv"
            style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
          />
        </div>
      ) : (
        <div className="screening-summary-card">
          <div className="screening-summary-row">
            <span>📄 {state.screeningFile?.name}</span>
            <button
              className="btn-secondary"
              style={{ fontSize: "0.8rem", padding: "3px 10px" }}
              onClick={() => { update({ screeningFile: null, exclusionCriteria: [], screeningRecordCount: 0, screeningIncludeCount: 0, screeningExcludeCount: 0 }); setParsed(false); }}
            >
              Replace
            </button>
          </div>
          <div className="screening-counts">
            <span className="count-pill include">✓ {state.screeningIncludeCount} INCLUDE</span>
            <span className="count-pill exclude">✕ {state.screeningExcludeCount} EXCLUDE</span>
            <span className="count-pill total">= {state.screeningRecordCount} total</span>
          </div>
        </div>
      )}

      {uploadError && <div className="wizard-error">⚠ {uploadError}</div>}

      {/* Criteria editor */}
      {parsed && criteria.length > 0 && (
        <div className="criteria-section">
          <h4 className="criteria-section-title">
            Exclusion criteria — auto-detected from your decisions
          </h4>
          <p className="label-hint">
            Add a yes/no question for each tag. The model checks them in this order — first match
            wins. Drag to reorder by priority.
          </p>
          <div className="criteria-list">
            {criteria.map((c, i) => (
              <div key={c.id} className="criterion-row">
                <div className="criterion-order">
                  <button onClick={() => moveUp(i)} disabled={i === 0}>▲</button>
                  <span>{c.order}</span>
                  <button onClick={() => moveDown(i)} disabled={i === criteria.length - 1}>▼</button>
                </div>
                <div className="criterion-fields">
                  <div className="field-row-2">
                    <div>
                      <div className="eppi-tag-chip">{c.tag}</div>
                      <div className="eppi-tag-label">{c.label}</div>
                    </div>
                    <input
                      className="wizard-input"
                      placeholder="Yes/no question for the model, e.g. 'Is this a duplicate publication?'"
                      value={c.question}
                      onChange={(e) => changeCriterion(c.id, { ...c, question: e.target.value })}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* MAYBE strategy */}
          <div className="maybe-section">
            <h4 className="maybe-title">How should MAYBE decisions be handled?</h4>
            <div className="maybe-options">
              {MAYBE_OPTIONS.map((opt) => (
                <label key={opt.value} className={`maybe-option ${state.maybeStrategy === opt.value ? "selected" : ""}`}>
                  <input
                    type="radio"
                    name="maybe"
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
        </div>
      )}

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button
          className="btn-primary"
          onClick={onNext}
          disabled={!canContinue}
          title={!canContinue ? "Upload a file and add a question for every criterion" : ""}
        >
          Continue →
        </button>
      </div>
    </div>
  );
}
