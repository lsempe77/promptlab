import React, { useState } from "react";
import { API_BASE_URL } from "../api";
import type { WizardState, WizardStepKey } from "./wizard/types";
import { WIZARD_STEPS } from "./wizard/types";
import Step1ProjectSetup from "./wizard/Step1ProjectSetup";
import Step2FieldBuilder from "./wizard/Step2FieldBuilder";
import Step2ScreeningUpload from "./wizard/Step2ScreeningUpload";
import Step3CorpusUpload from "./wizard/Step3CorpusUpload";
import Step4GroundTruth from "./wizard/Step4GroundTruth";
import Step5Launch from "./wizard/Step5Launch";
import LoginModal from "./LoginModal";

interface Props {
  onClose: () => void;
  onProjectCreated: (slug: string) => void;
}

const EMPTY: WizardState = {
  projectName: "",
  projectSlug: "",
  description: "",
  projectType: "extraction",
  password: "",
  fields: [],
  screeningFile: null,
  exclusionCriteria: [],
  maybeStrategy: "cross_model",
  screeningRecordCount: 0,
  screeningIncludeCount: 0,
  screeningExcludeCount: 0,
  corpusFiles: [],
  groundTruthFile: null,
  selectedModels: ["~anthropic/claude-sonnet-latest", "~openai/gpt-mini-latest", "deepseek/deepseek-v4-flash"],
};

// Screening projects skip corpus + ground-truth steps (all comes from one EPPI file)
const SCREENING_STEPS: WizardStepKey[] = ["project", "fields", "launch"];
const EXTRACTION_STEPS: WizardStepKey[] = ["project", "fields", "corpus", "ground-truth", "launch"];

const _STEP_LABEL: Record<string, string> = { project: "Project", fields: "Fields", corpus: "Corpus", "ground-truth": "Ground Truth", launch: "Launch" };

export default function NewProjectWizard({ onClose, onProjectCreated }: Props) {
  const [state, setState] = useState<WizardState>(EMPTY);
  const [currentStep, setCurrentStep] = useState<WizardStepKey>("project");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(
    () => sessionStorage.getItem("promptlab_token")
  );

  // Show login gate first if no valid token
  if (!token) {
    return <LoginModal onSuccess={setToken} onCancel={onClose} />;
  }

  const isScreening = state.projectType !== "extraction";
  const stepKeys = isScreening ? SCREENING_STEPS : EXTRACTION_STEPS;
  const currentIdx = stepKeys.indexOf(currentStep);

  const update = (patch: Partial<WizardState>) =>
    setState((prev) => ({ ...prev, ...patch }));

  const goNext = () => {
    if (currentIdx < stepKeys.length - 1)
      setCurrentStep(stepKeys[currentIdx + 1]);
  };
  const goBack = () => {
    if (currentIdx > 0) setCurrentStep(stepKeys[currentIdx - 1]);
  };

  const handleLaunch = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/api/projects`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { "Authorization": `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          name: state.projectName,
          slug: state.projectSlug,
          description: state.description,
          project_type: state.projectType,
          password: state.password,
          config: {
            fields: state.fields,
            exclusion_criteria: state.exclusionCriteria,
            maybe_strategy: state.maybeStrategy,
            selected_models: state.selectedModels,
          },
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Server error ${res.status}`);
      }
      onProjectCreated(state.projectSlug);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  const stepProps = { state, update, onNext: goNext, onBack: goBack };

  return (
    <div className="wizard-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wizard-modal">
        {/* Header */}
        <div className="wizard-header">
          <h2>New Prompt Lab</h2>
          <button className="wizard-close" onClick={onClose} title="Close">✕</button>
        </div>

        {/* Step progress bar */}
        <div className="wizard-steps">
          {stepKeys.map((key, i) => (
            <React.Fragment key={key}>
              <button
                className={`wizard-step-btn ${key === currentStep ? "active" : ""} ${i < currentIdx ? "done" : ""}`}
                onClick={() => i <= currentIdx && setCurrentStep(key)}
                disabled={i > currentIdx}
              >
                <span className="step-num">{i < currentIdx ? "✓" : i + 1}</span>
                <span className="step-label">{isScreening && key === "fields" ? "EPPI Upload" : _STEP_LABEL[key] ?? key}</span>
              </button>
              {i < stepKeys.length - 1 && <div className={`wizard-step-connector ${i < currentIdx ? "done" : ""}`} />}
            </React.Fragment>
          ))}
        </div>

        {/* Step content */}
        <div className="wizard-body">
          {error && <div className="wizard-error">⚠ {error}</div>}

          {currentStep === "project" && <Step1ProjectSetup {...stepProps} />}

          {currentStep === "fields" && !isScreening && (
            <Step2FieldBuilder {...stepProps} />
          )}
          {currentStep === "fields" && isScreening && (
            <Step2ScreeningUpload {...stepProps} token={token} />
          )}

          {currentStep === "corpus" && <Step3CorpusUpload {...stepProps} />}
          {currentStep === "ground-truth" && <Step4GroundTruth {...stepProps} />}
          {currentStep === "launch" && (
            <Step5Launch
              {...stepProps}
              onLaunch={handleLaunch}
              submitting={submitting}
            />
          )}
        </div>
      </div>
    </div>
  );
}
