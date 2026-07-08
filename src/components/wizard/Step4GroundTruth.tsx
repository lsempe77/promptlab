import { useRef, useState } from "react";
import type { WizardState } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
}

function parseCSVPreview(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.split("\n").filter(Boolean);
  if (lines.length === 0) return { headers: [], rows: [] };
  const headers = lines[0].split(",").map((h) => h.trim().replace(/^"|"$/g, ""));
  const rows = lines
    .slice(1, 6)
    .map((l) => l.split(",").map((c) => c.trim().replace(/^"|"$/g, "")));
  return { headers, rows };
}

export default function Step4GroundTruth({ state, update, onNext, onBack }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const file = state.groundTruthFile;
  const [preview, setPreview] = useState<{ headers: string[]; rows: string[][] } | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  const REQUIRED_COLS =
    state.projectType === "extraction"
      ? ["record_id", "field_name", "value"]
      : ["record_id", "decision", "exclusion_tag"];

  const handleFile = (f: File | null) => {
    if (!f) return;
    update({ groundTruthFile: f });
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      const parsed = parseCSVPreview(text);
      setPreview(parsed);
      const missing = REQUIRED_COLS.filter((col) => !parsed.headers.includes(col));
      setValidationError(
        missing.length > 0
          ? `Missing required columns: ${missing.join(", ")}`
          : null
      );
    };
    reader.readAsText(f);
  };

  const isScreening = state.projectType !== "extraction";

  return (
    <div className="wizard-step">
      <h3 className="step-title">Upload ground truth</h3>
      <p className="step-subtitle">
        A CSV of human-labelled examples. These are used to score model accuracy and train the
        prompt optimizer.
      </p>

      <div className="gt-format-box">
        <strong>Required columns:</strong>
        {isScreening ? (
          <code> record_id, decision (INCLUDE/EXCLUDE/MAYBE), exclusion_tag (if EXCLUDE)</code>
        ) : (
          <code> record_id, field_name, value</code>
        )}
        <p className="label-hint">
          {isScreening
            ? "One row per document. decision must be INCLUDE, EXCLUDE, or MAYBE. exclusion_tag is the verbatim EPPI tag when decision=EXCLUDE."
            : "One row per (record, field) pair. For list fields, use one row per element or pipe-separate values."}
        </p>
      </div>

      <div
        className="drop-zone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); handleFile(e.dataTransfer.files[0]); }}
        onClick={() => inputRef.current?.click()}
      >
        <span className="drop-icon">📊</span>
        <p>{file ? file.name : "Drag CSV here, or click to browse"}</p>
        <input
          ref={inputRef}
          type="file"
          accept=".csv"
          style={{ display: "none" }}
          onChange={(e) => handleFile(e.target.files?.[0] ?? null)}
        />
      </div>

      {validationError && <div className="wizard-error">⚠ {validationError}</div>}

      {preview && !validationError && (
        <div className="csv-preview">
          <p className="label-hint">Preview (first 5 rows):</p>
          <table className="preview-table">
            <thead>
              <tr>{preview.headers.map((h) => <th key={h}>{h}</th>)}</tr>
            </thead>
            <tbody>
              {preview.rows.map((row, i) => (
                <tr key={i}>{row.map((cell, j) => <td key={j}>{cell}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button
          className="btn-primary"
          onClick={onNext}
          disabled={!file || !!validationError}
        >
          Continue →
        </button>
      </div>
    </div>
  );
}
