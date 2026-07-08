// Shared types for the New Project Wizard

export type ProjectType = "extraction" | "screening_ta" | "screening_ft";
export type FieldType = "list" | "categorical" | "text";

export interface FieldDefinition {
  id: string; // local uuid for UI keying
  name: string; // slug, e.g. "author_country"
  label: string; // display name, e.g. "Author Country"
  type: FieldType;
  description: string; // plain-English instruction for the model
  examples: string[]; // 1-3 correct-answer examples
  taxonomy: string[]; // allowed values (categorical fields only)
}

export interface ExclusionCriterion {
  id: string;
  tag: string; // EPPI verbatim tag, e.g. "wrong_population"
  label: string; // human-readable label
  question: string; // yes/no question the model must answer
  order: number; // hierarchy order — checked in order, first match wins
}

export interface WizardState {
  // Step 1
  projectName: string;
  projectSlug: string;
  description: string;
  projectType: ProjectType;
  password: string;

  // Step 2 — extraction
  fields: FieldDefinition[];

  // Step 2 — screening
  exclusionCriteria: ExclusionCriterion[];
  maybeStrategy: "cross_model" | "excerpt_verify" | "self_consistency";

  // Step 3
  corpusFiles: File[];

  // Step 4
  groundTruthFile: File | null;

  // Step 5
  modelTiers: ("cheap" | "mid" | "expensive")[];
}

export const WIZARD_STEPS = [
  { key: "project", label: "Project" },
  { key: "fields", label: "Fields" },
  { key: "corpus", label: "Corpus" },
  { key: "ground-truth", label: "Ground Truth" },
  { key: "launch", label: "Launch" },
] as const;

export type WizardStepKey = typeof WIZARD_STEPS[number]["key"];
