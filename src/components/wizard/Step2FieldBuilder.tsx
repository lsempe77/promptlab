import { useState } from "react";
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

  const canContinue = fields.length > 0 && fields.every((f) => f.name && f.description);

  return (
    <div className="wizard-step">
      <h3 className="step-title">Define your extraction fields</h3>
      <p className="step-subtitle">
        Add one field per piece of information you want to extract from each document. The
        description becomes the instruction the model follows.
      </p>

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

      <button className="btn-add-field" onClick={addField}>
        + Add field
      </button>

      <div className="wizard-footer">
        <button className="btn-secondary" onClick={onBack}>← Back</button>
        <button className="btn-primary" onClick={onNext} disabled={!canContinue}>
          Continue →
        </button>
      </div>
    </div>
  );
}
