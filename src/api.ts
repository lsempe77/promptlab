export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://127.0.0.1:8000";

export interface ProjectInfo {
  slug: string;
  name: string;
  description: string;
}

export interface FieldInfo {
  name: string;
  label: string;
  value_type: string;
  taxonomy_key: string | null;
  description: string;
}

export interface PromptVersion {
  id: number;
  field_name: string;
  version: number;
  template: string;
  parent_id: number | null;
  notes: string | null;
  accepted: number;
  created_at: string;
}

export interface ModelSummary {
  model_id: string;
  n: number;
  mean_score: number | null;
  mean_honesty_score: number | null;
  mean_logprob_confidence: number | null;
  n_errors: number;
  mean_latency_ms: number | null;
  total_cost_usd: number | null;
  accuracy: number;
  abstention_rate: number;
  hallucination_rate: number;
  wrong_rate: number;
  excerpt_verified_rate: number | null;
}

export interface LlmJudgeSummary {
  model_id: string;
  n_judged: number;
  llm_judged_accuracy: number;
}

// Confidence signals (see backend scripts/self_consistency.py + the
// cross-model-agreement endpoint).
export interface CrossModelAgreement {
  model_id: string;
  n_records: number;
  agreement_rate: number;
}

export interface SelfConsistency {
  model_id: string;
  n_records: number;
  mean_agreement: number;
  mean_samples: number;
}

// Calibration of a model's verbalized 0-1 confidence vs. actual correctness.
export interface CalibrationBin {
  lo: number;
  hi: number;
  n: number;
  mean_confidence: number | null;
  accuracy: number | null;
}

export interface Calibration {
  model_id: string;
  n_scored: number;
  brier: number;
  mean_confidence: number;
  accuracy: number;
  bins: CalibrationBin[];
}

export interface IterationLog {
  id: number;
  field_name: string;
  iteration_num: number;
  prompt_version_id: number;
  model_id: string;
  mean_score: number;
  n_records: number;
  feedback: string | null;
  accepted: number;
  created_at: string;
  prompt_version: number;
  prompt_template: string;
  prompt_notes: string | null;
}

export interface Thresholds {
  correct_threshold: number;
  fuzzy_match_threshold: number;
  improvement_epsilon: number;
}

export interface CategoricalConfusion {
  type: "categorical";
  truth_labels: string[];
  pred_labels: string[];
  matrix: number[][];
  accuracy: number;
  sensitivity: number;
  specificity: number;
  f2: number;
  n: number;
}

export interface ListConfusion {
  type: "list";
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  sensitivity: number;
  specificity: number | null;
  f1: number;
  f2: number;
  n: number;
}

export type Confusion = CategoricalConfusion | ListConfusion;

export interface Job {
  id: number;
  field_name: string;
  model_id: string;
  kind: "extraction" | "optimization";
  status: "running" | "completed" | "failed";
  total: number | null;
  completed: number;
  started_at: string;
  updated_at: string;
  finished_at: string | null;
  error: string | null;
  stale: boolean;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`API error ${res.status} for ${path}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  projects: () => getJson<ProjectInfo[]>("/api/projects"),
  fields: (project: string) => getJson<FieldInfo[]>(`/api/projects/${project}/fields`),
  promptVersions: (project: string, field: string) =>
    getJson<PromptVersion[]>(`/api/projects/${project}/fields/${field}/prompt-versions`),
  modelsSummary: (project: string, field: string) =>
    getJson<ModelSummary[]>(`/api/projects/${project}/fields/${field}/models-summary`),
  iterations: (project: string, field: string, modelId?: string) =>
    getJson<IterationLog[]>(
      `/api/projects/${project}/fields/${field}/iterations${modelId ? `?model_id=${encodeURIComponent(modelId)}` : ""}`,
    ),
  thresholds: () => getJson<Thresholds>("/api/config/thresholds"),
  jobs: (project: string, field: string) => getJson<Job[]>(`/api/projects/${project}/fields/${field}/jobs`),
  confusion: (project: string, field: string, modelId?: string) =>
    getJson<Confusion>(
      `/api/projects/${project}/fields/${field}/confusion${modelId ? `?model_id=${encodeURIComponent(modelId)}` : ""}`,
    ),
  llmJudgeSummary: (project: string, field: string) =>
    getJson<LlmJudgeSummary[]>(`/api/projects/${project}/fields/${field}/llm-judge-summary`),
  crossModelAgreement: (project: string, field: string) =>
    getJson<CrossModelAgreement[]>(`/api/projects/${project}/fields/${field}/cross-model-agreement`),
  selfConsistency: (project: string, field: string) =>
    getJson<SelfConsistency[]>(`/api/projects/${project}/fields/${field}/self-consistency`),
  calibration: (project: string, field: string) =>
    getJson<Calibration[]>(`/api/projects/${project}/fields/${field}/calibration`),
};
