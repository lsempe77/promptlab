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

type DetectedTag = { tag: string; label: string; count: number };

const MAYBE_OPTIONS: { value: WizardState["maybeStrategy"]; label: string; detail: string }[] = [
  { value: "cross_model", label: "Cross-model disagreement", detail: "Run 2-3 models. Agreement -> take decision. Disagreement -> MAYBE." },
  { value: "excerpt_verify", label: "Excerpt verification (recommended)", detail: "Model must cite verbatim text. No specific text found -> MAYBE." },
  { value: "self_consistency", label: "Self-consistency re-sampling", detail: "Run same model 3x. 3/3 agree -> confident. Otherwise -> MAYBE." },
];

export default function Step2ScreeningUpload({ state, update, onNext, onBack, token }: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<"upload" | "select" | "questions">(
    state.screeningFile ? "questions" : "upload"
  );
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [detectedTags, setDetectedTags] = useState<DetectedTag[]>([]);
  const [selectedTags, setSelectedTags] = useState<Set<string>>(
    new Set(state.exclusionCriteria.map((c) => c.tag))
  );
  const [suggesting, setSuggesting] = useState<string | null>(null);

  const screeningLabel = state.projectType === "screening_ta" ? "Title & Abstract" : "Full-Text";

  const handleFile = async (file: File) => {
    setUploading(true);
    setUploadError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_BASE_URL}/api/screening/parse-eppi`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (res.status === 401) sessionStorage.removeItem("promptlab_token");
        throw new Error(err.detail || `Server error ${res.status}`);
      }
      const data = await res.json();
      setDetectedTags(data.tags);
      setSelectedTags(new Set(data.tags.map((t: DetectedTag) => t.tag)));
      update({
        screeningFile: file,
        screeningRecordCount: data.total,
        screeningIncludeCount: data.include_count,
        screeningExcludeCount: data.exclude_count,
      });
      setPhase("select");
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : String(e));
    } finally {
      setUploading(false);
    }
  };

  const confirmSelection = () => {
    const existing = new Map(state.exclusionCriteria.map((c) => [c.tag, c]));
    const criteria: ExclusionCriterion[] = detectedTags
      .filter((t) => selectedTags.has(t.tag))
      .map((t, i) => existing.get(t.tag) ?? {
        id: crypto.randomUUID(), tag: t.tag, label: t.label, question: "", order: i + 1,
      });
    update({ exclusionCriteria: criteria });
    setPhase("questions");
  };

  const suggestQuestion = async (criterion: ExclusionCriterion) => {
    setSuggesting(criterion.tag);
    try {
      const res = await fetch(`${API_BASE_URL}/api/screening/suggest-question`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ label: criterion.label, project_name: state.projectName }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      updateQuestion(criterion.tag, data.question);
    } catch { /* silent */ }
    finally { setSuggesting(null); }
  };

  const suggestAll = async () => {
    for (const c of state.exclusionCriteria) {
      if (!c.question.trim()) await suggestQuestion(c);
    }
  };

  const updateQuestion = (tag: string, q: string) =>
    update({ exclusionCriteria: state.exclusionCriteria.map((c) => c.tag === tag ? { ...c, question: q } : c) });

  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...state.exclusionCriteria];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    update({ exclusionCriteria: next.map((c, i) => ({ ...c, order: i + 1 })) });
  };
  const moveDown = (idx: number) => {
    if (idx === state.exclusionCriteria.length - 1) return;
    const next = [...state.exclusionCriteria];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    update({ exclusionCriteria: next.map((c, i) => ({ ...c, order: i + 1 })) });
  };

  const canFinish = state.exclusionCriteria.length > 0 && state.exclusionCriteria.every((c) => c.question.trim());

  if (phase === "upload") {
    return (
      <div className="wizard-step">
        <h3 className="step-title">Upload your EPPI screening data — {screeningLabel}</h3>
        <p className="step-subtitle">
          Drop your EPPI-Reviewer export (.xlsx). One file gives the corpus, ground truth, and exclusion criteria.
        </p>
        <div className={`drop-zone ${uploading ? "uploading" : ""}`}
          onClick={() => !uploading && fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handleFile(f); }}>
          <span className="drop-icon">📊</span>
          <p>{uploading ? "Reading file…" : "Drop EPPI Excel here, or click to browse"}</p>
          <p className="drop-hint">Expected: T1 (title) · AB (abstract) · U1 (record ID) · ta_decision</p>
          <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
        </div>
        {uploadError && <div className="wizard-error">^ {uploadError}</div>}
        <div className="wizard-footer">
          <button className="btn-secondary" onClick={onBack}>Back</button>
        </div>
      </div>
    );
  }

  if (phase === "select") {
    return (
      <div className="wizard-step">
        <h3 className="step-title">Select exclusion criteria</h3>
        <div className="screening-summary-card">
          <div className="screening-summary-row">
            <span>📄 {state.screeningFile?.name}</span>
            <button className="btn-secondary" style={{ fontSize: "0.8rem", padding: "3px 10px" }}
              onClick={() => { update({ screeningFile: null, exclusionCriteria: [], screeningRecordCount: 0, screeningIncludeCount: 0, screeningExcludeCount: 0 }); setPhase("upload"); }}>
              Replace
            </button>
          </div>
          <div className="screening-counts">
            <span className="count-pill include">✓ {state.screeningIncludeCount} INCLUDE</span>
            <span className="count-pill exclude">✕ {state.screeningExcludeCount} EXCLUDE</span>
            <span className="count-pill total">= {state.screeningRecordCount} total</span>
          </div>
        </div>
        <p className="step-subtitle">
          Tick the exclusion tags you want to use. The model checks them in this order — drag to reprioritise later.
        </p>
        <div className="tag-selection-grid">
          {detectedTags.map((t) => {
            const checked = selectedTags.has(t.tag);
            return (
              <label key={t.tag} className={`tag-select-card ${checked ? "selected" : ""}`}>
                <input type="checkbox" checked={checked}
                  onChange={() => { const next = new Set(selectedTags); checked ? next.delete(t.tag) : next.add(t.tag); setSelectedTags(next); }} />
                <div className="tag-select-content">
                  <div className="eppi-tag-chip">{t.tag}</div>
                  <strong className="tag-select-label">{t.label}</strong>
                  <span className="tag-select-count">{t.count} papers excluded</span>
                </div>
              </label>
            );
          })}
        </div>
        <div className="wizard-footer">
          <button className="btn-secondary" onClick={() => setPhase("upload")}>Back</button>
          <button className="btn-primary" onClick={confirmSelection} disabled={selectedTags.size === 0}>
            Write questions for {selectedTags.size} criteria →
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="wizard-step">
      <h3 className="step-title">Write the screening questions</h3>
      <p className="step-subtitle">
        Each question is what the model answers YES/NO per paper. First match in order = EXCLUDE.
        Hit <strong>✨ Suggest</strong> to get an LLM-drafted question, then edit as needed.
      </p>
      <div className="suggest-all-bar">
        <button className="btn-suggest-all" onClick={suggestAll} disabled={suggesting !== null}>
          {suggesting ? "Suggesting…" : "✨ Suggest all missing questions"}
        </button>
        <span className="label-hint">
          {state.exclusionCriteria.filter((c) => c.question.trim()).length}/{state.exclusionCriteria.length} filled
        </span>
      </div>
      <div className="question-editor-list">
        {state.exclusionCriteria.map((c, i) => (
          <div key={c.id} className="question-editor-card">
            <div className="qe-header">
              <div className="criterion-order" style={{ minWidth: 36 }}>
                <button onClick={() => moveUp(i)} disabled={i === 0}>▲</button>
                <span>{c.order}</span>
                <button onClick={() => moveDown(i)} disabled={i === state.exclusionCriteria.length - 1}>▼</button>
              </div>
              <div className="qe-meta">
                <div className="eppi-tag-chip">{c.tag}</div>
                <strong className="eppi-tag-label">{c.label}</strong>
              </div>
              <button
                className={`btn-suggest ${suggesting === c.tag ? "loading" : ""}`}
                onClick={() => suggestQuestion(c)} disabled={suggesting !== null}>
                {suggesting === c.tag ? "⏳ …" : "✨ Suggest"}
              </button>
            </div>
            <textarea
              className={`wizard-input question-textarea ${c.question.trim() ? "has-value" : ""}`}
              rows={3}
              placeholder={`Yes/no question for "${c.label}"… (e.g. "Is this study about a population other than...")`}
              value={c.question}
              onChange={(e) => updateQuestion(c.tag, e.target.value)}
            />
          </div>
        ))}
      </div>
      <div className="maybe-section">
        <h4 className="maybe-title">How should MAYBE decisions be handled?</h4>
        <div className="maybe-options">
          {MAYBE_OPTIONS.map((opt) => (
            <label key={opt.value} className={`maybe-option ${state.maybeStrategy === opt.value ? "selected" : ""}`}>
              <input type="radio" name="maybe" value={opt.value}
                checked={state.maybeStrategy === opt.value}
                onChange={() => update({ maybeStrategy: opt.value })} />
              <div><strong>{opt.label}</strong><p>{opt.detail}</p></div>
            </label>
          ))}
        </div>
      </div>
      <div className="wizard-footer">
        <button className="btn-secondary" onClick={() => setPhase("select")}>← Back</button>
        <button className="btn-primary" onClick={onNext} disabled={!canFinish}>Continue →</button>
      </div>
    </div>
  );
}
