import type { WizardState, ProjectType } from "./types";

interface Props {
  state: WizardState;
  update: (patch: Partial<WizardState>) => void;
  onNext: () => void;
  onBack: () => void;
}

function slugify(s: string) {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

const PROJECT_TYPES: { value: ProjectType; icon: string; title: string; description: string }[] = [
  {
    value: "extraction",
    icon: "🗂",
    title: "Data Extraction",
    description:
      "Extract structured fields from documents — authors, dates, outcomes, sector, etc. Each field has a type, description, and optional taxonomy.",
  },
  {
    value: "screening_ta",
    icon: "📋",
    title: "Title & Abstract Screening",
    description:
      "Classify each record as INCLUDE / EXCLUDE / MAYBE based on your eligibility criteria. Fast first-pass using title and abstract only.",
  },
  {
    value: "screening_ft",
    icon: "📄",
    title: "Full-Text Screening",
    description:
      "Same as TA screening but operates on the full paper. Usually the second pass after TA screening.",
  },
];

export default function Step1ProjectSetup({ state, update, onNext }: Props) {
  const canContinue =
    state.projectName.trim().length >= 3 &&
    state.projectSlug.length >= 2 &&
    state.projectType !== null;

  return (
    <div className="wizard-step">
      <h3 className="step-title">About this project</h3>
      <p className="step-subtitle">
        Give your prompt lab a name and pick the type of task. You can run extraction and
        screening as separate projects.
      </p>

      {/* Project type selector */}
      <div className="project-type-grid">
        {PROJECT_TYPES.map((pt) => (
          <button
            key={pt.value}
            className={`project-type-card ${state.projectType === pt.value ? "selected" : ""}`}
            onClick={() => update({ projectType: pt.value })}
            type="button"
          >
            <span className="pt-icon">{pt.icon}</span>
            <strong className="pt-title">{pt.title}</strong>
            <p className="pt-desc">{pt.description}</p>
          </button>
        ))}
      </div>

      {/* Name */}
      <label className="wizard-label">
        Project name
        <input
          className="wizard-input"
          type="text"
          placeholder="e.g. Nutrition Reviews 2025"
          value={state.projectName}
          onChange={(e) => {
            const name = e.target.value;
            update({
              projectName: name,
              projectSlug: slugify(name),
            });
          }}
        />
      </label>

      {/* Slug */}
      <label className="wizard-label">
        Slug <span className="label-hint">(used in URLs and file paths, auto-generated)</span>
        <input
          className="wizard-input monospace"
          type="text"
          placeholder="nutrition-reviews-2025"
          value={state.projectSlug}
          onChange={(e) => update({ projectSlug: slugify(e.target.value) })}
        />
      </label>

      {/* Description */}
      <label className="wizard-label">
        Description <span className="label-hint">(optional)</span>
        <textarea
          className="wizard-input"
          rows={2}
          placeholder="A brief description of this review / extraction task"
          value={state.description}
          onChange={(e) => update({ description: e.target.value })}
        />
      </label>

      {/* Password */}
      <label className="wizard-label">
        Access password
        <span className="label-hint">
          {" "}— colleagues need this to push changes. Leave blank for read-only public access.
        </span>
        <input
          className="wizard-input"
          type="password"
          placeholder="Optional"
          value={state.password}
          onChange={(e) => update({ password: e.target.value })}
        />
      </label>

      <div className="wizard-footer">
        <span />
        <button className="btn-primary" onClick={onNext} disabled={!canContinue}>
          Continue →
        </button>
      </div>
    </div>
  );
}
