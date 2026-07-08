import { useRef, useState } from "react";
import type { WizardState, FieldDefinition, FieldType } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
}

function newField(): FieldDefinition {
  return {
    id: crypto.randomUUID(),
    name: "",
    label: "",
    type: "text",
    description: "",
    examples: [""],
    taxonomy: [],
  };
}

function slugify(s: string) {
  return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

/** Parse a CSV string into { headers, rows }.  Handles quoted fields. */
function parseCSV(text: string): { headers: string[]; rows: string[][] } {
  const lines = text.split(/\r?\n/).filter(Boolean);
  if (!lines.length) return { headers: [], rows: [] };
  const splitLine = (line: string) => {
    const cells: string[] = [];
    let cur = "", inQ = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (c === '"') { inQ = !inQ; }
      else if (c === "," && !inQ) { cells.push(cur.trim()); cur = ""; }
      else cur += c;
    }
    cells.push(cur.trim());
    return cells;
  };
  return { headers: splitLine(lines[0]), rows: lines.slice(1, 6).map(splitLine) };
}

/** Pick up to 3 non-empty example values for a column. */
function examplesForCol(rows: string[][], colIdx: number): string[] {
  return rows
    .map((r) => r[colIdx]?.trim() ?? "")
    .filter(Boolean)
    .slice(0, 3);
}

const FIELD_TYPES: { value: FieldType; label: string; hint: string }[] = [
  { value: "text", label: "Free text", hint: "Single string — e.g. study title, effect size" },
  { value: "list", label: "List of values", hint: "Multiple items — e.g. authors, countries" },
  { value: "categorical", label: "Pick one", hint: "One value from a fixed list — e.g. sector" },
];

function FieldCard({
  field,
  onChange,
  onRemove,
}: {
  field: FieldDefinition;
  onChange: (f: FieldDefinition) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(true);

  const setField = (patch: Partial<FieldDefinition>) =>
    onChange({ ...field, ...patch });

  return (
    <div className={`field-card ${open ? "open" : ""}`}>
      <div className="field-card-header" onClick={() => setOpen((o) => !o)}>
        <span className="field-card-name">{field.label || <em>Unnamed field</em>}</span>
        <span className="field-card-type-badge">{field.type}</span>
        <button
          className="field-card-remove"
          onClick={(e) => { e.stopPropagation(); onRemove(); }}
          title="Remove field"
        >
          ✕
        </button>
        <span className="field-card-chevron">{open ? "▲" : "▼"}</span>
      </div>

      {open && (
        <div className="field-card-body">
          <div className="field-row-2">
            <label className="wizard-label">
              Display name
              <input
                className="wizard-input"
                placeholder="e.g. Author Country"
                value={field.label}
                onChange={(e) =>
                  setField({ label: e.target.value, name: slugify(e.target.value) })
                }
              />
            </label>
            <label className="wizard-label">
              Slug <span className="label-hint">(auto)</span>
              <input
                className="wizard-input monospace"
                value={field.name}
                onChange={(e) => setField({ name: slugify(e.target.value) })}
              />
            </label>
          </div>

          {/* Type selector */}
          <div className="field-type-selector">
            {FIELD_TYPES.map((ft) => (
              <label key={ft.value} className={`field-type-option ${field.type === ft.value ? "selected" : ""}`}>
                <input
                  type="radio"
                  name={`type-${field.id}`}
                  value={ft.value}
                  checked={field.type === ft.value}
                  onChange={() => setField({ type: ft.value })}
                />
                <strong>{ft.label}</strong>
                <span className="field-type-hint">{ft.hint}</span>
              </label>
            ))}
          </div>

          {/* Description */}
          <label className="wizard-label">
            Description
            <span className="label-hint"> — plain English instruction for the model</span>
            <textarea
              className="wizard-input"
              rows={2}
              placeholder="e.g. List every country where at least one author is based, using the full country name."
              value={field.description}
              onChange={(e) => setField({ description: e.target.value })}
            />
          </label>

          {/* Examples */}
          <label className="wizard-label">
            Example correct answers
            <span className="label-hint"> (one per line)</span>
            <textarea
              className="wizard-input monospace"
              rows={3}
              placeholder={"Uganda\nKenya\nUnited States"}
              value={field.examples.join("\n")}
              onChange={(e) =>
                setField({ examples: e.target.value.split("\n") })
              }
            />
          </label>

          {/* Taxonomy (categorical only) */}
          {field.type === "categorical" && (
            <label className="wizard-label">
              Allowed values (taxonomy)
              <span className="label-hint"> — one per line, used verbatim</span>
              <textarea
                className="wizard-input monospace"
                rows={5}
                placeholder={"Health\nEducation\nAgriculture fishing and forestry\nSocial protection"}
                value={field.taxonomy.join("\n")}
                onChange={(e) =>
                  setField({ taxonomy: e.target.value.split("\n").filter(Boolean) })
                }
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}

export default function Step2FieldBuilder({ state, update, onNext, onBack }: Props) {
  const fields = state.fields;
  const setFields = (fields: FieldDefinition[]) => update({ fields });

  const addField = () => setFields([...fields, newField()]);
  const removeField = (id: string) => setFields(fields.filter((f) => f.id !== id));
  const changeField = (id: string, f: FieldDefinition) =>
    setFields(fields.map((x) => (x.id === id ? f : x)));

  // CSV import state
  const csvRef = useRef<HTMLInputElement>(null);
  const [csvData, setCsvData] = useState<{ headers: string[]; rows: string[][] } | null>(null);
  const [csvSelected, setCsvSelected] = useState<Set<number>>(new Set());

  const handleCSVFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      const parsed = parseCSV(text);
      setCsvData(parsed);
      // Pre-select all columns
      setCsvSelected(new Set(parsed.headers.map((_, i) => i)));
    };
    reader.readAsText(file);
  };

  const importFromCSV = () => {
    if (!csvData) return;
    const newFields: FieldDefinition[] = Array.from(csvSelected)
      .sort((a, b) => a - b)
      .map((colIdx) => {
        const header = csvData.headers[colIdx];
        const examples = examplesForCol(csvData.rows, colIdx);
        // Guess type: if all examples look like they contain ";" or "|" → list
        const looksLikeList = examples.some((v) => v.includes("|") || v.includes(";"));
        return {
          id: crypto.randomUUID(),
          name: slugify(header),
          label: header,
          type: (looksLikeList ? "list" : "text") as FieldType,
          description: "",
          examples,
          taxonomy: [],
        };
      });
    setFields([...fields, ...newFields]);
    setCsvData(null);
    setCsvSelected(new Set());
  };

  const canContinue = fields.length > 0 && fields.every((f) => f.name && f.description);

  return (
    <div className="wizard-step">
      <h3 className="step-title">Define your extraction fields</h3>
      <p className="step-subtitle">
        Upload a CSV/Excel with your existing data — columns become fields and the values become
        examples automatically. Or add fields manually below.
      </p>

      {/* CSV import */}
      <div className="csv-import-box">
        <div className="csv-import-header">
          <span className="csv-import-label">📂 Import from CSV / Excel</span>
          <span className="label-hint"> — upload any file with a header row; select which columns to extract</span>
        </div>

        {!csvData ? (
          <div
            className="drop-zone drop-zone-sm"
            onClick={() => csvRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files[0]; if (f) handleCSVFile(f); }}
          >
            <span>Drop CSV here or <u>click to browse</u></span>
            <span className="label-hint"> (.csv — for Excel, export as CSV first)</span>
            <input
              ref={csvRef}
              type="file"
              accept=".csv,.tsv,.txt"
              style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleCSVFile(f); }}
            />
          </div>
        ) : (
          <div className="csv-column-picker">
            <p className="label-hint">
              Select the columns you want to extract. Examples are pulled from the first rows.
            </p>
            <div className="csv-col-grid">
              {csvData.headers.map((header, i) => {
                const examples = examplesForCol(csvData.rows, i);
                const checked = csvSelected.has(i);
                return (
                  <label key={i} className={`csv-col-card ${checked ? "selected" : ""}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        const next = new Set(csvSelected);
                        checked ? next.delete(i) : next.add(i);
                        setCsvSelected(next);
                      }}
                    />
                    <strong className="col-header">{header}</strong>
                    {examples.length > 0 && (
                      <span className="col-examples">
                        {examples.join(" · ")}
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
            <div className="csv-import-actions">
              <button className="btn-secondary" onClick={() => setCsvData(null)}>Cancel</button>
              <button
                className="btn-primary"
                onClick={importFromCSV}
                disabled={csvSelected.size === 0}
              >
                Add {csvSelected.size} field{csvSelected.size !== 1 ? "s" : ""} →
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Manual field list */}
      {fields.length > 0 && (
        <div className="field-list">
          {fields.map((f) => (
            <FieldCard
              key={f.id}
              field={f}
              onChange={(nf) => changeField(f.id, nf)}
              onRemove={() => removeField(f.id)}
            />
          ))}
        </div>
      )}

      <button className="btn-add-field" onClick={addField}>+ Add field manually</button>

      {fields.length > 0 && fields.some((f) => !f.description) && (
        <p className="wizard-hint">⚠ Fill in a description for each field — this becomes the model's instruction.</p>
      )}

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button className="btn-primary" onClick={onNext} disabled={!canContinue}>
          Continue →
        </button>
      </div>
    </div>
  );
}
