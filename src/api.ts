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
  prompt_version: number | null;
  total_co2e_grams: number | null;
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

// Derived staged-rollout / quality-gate status for a field. The gate is
// evaluated PER MODEL within the field on a field-type-aware quality metric
// (F1 for the list fields, accuracy for the categorical ones), with LLM-judged
// accuracy kept as a reported concordance companion.
export interface StageModelGate {
  model_id: string;
  gate_metric_name: string; // "f1" | "accuracy"
  gate_metric: number;
  precision: number | null;
  recall: number | null;
  f1: number | null;
  accuracy: number | null;
  kappa: number | null;
  n: number;
  llm_judged_accuracy: number | null;
  n_judged: number;
  gate_passed: boolean;
  prompt_version?: number | null;
}

export interface StageStatus {
  references: number;
  stages: number[];
  stage_target: number | null;
  final_stage: number;
  gate_threshold: number;
  models: StageModelGate[];
  n_models_evaluated: number;
  n_models_judged: number;
  n_models_passing: number;
  n_judged: number;
  prompt_versions: number;
  prompt_versions_accepted: number;
}

// A prompt version that has logged runs for a field (for the version selector).
export interface RunVersion {
  version: number;
  accepted: number;
  n_runs: number;
  n_models: number;
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
  kappa: number | null;
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

export interface WorkerTask {
  field_name: string;
  model_id: string | null;
  kind: string;
  status: "pending" | "running";
  claimed_at: string | null;
  created_at: string;
  error: string | null;
}

export interface RecentTask {
  field_name: string;
  model_id: string | null;
  kind: string;
  status: "done" | "failed";
  finished_at: string | null;
  error: string | null;
}

export interface ActivityData {
  queue: { pending: number; running: number; total_active: number; error?: string };
  active_tasks: WorkerTask[];
  recently_done: RecentTask[];
  log_tail: string[];
}

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

// Build a `?a=1&b=2` query string, skipping undefined/null params.
function qs(params: Record<string, string | number | undefined | null>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  projects: () => getJson<ProjectInfo[]>("/api/projects"),
  fields: (project: string) => getJson<FieldInfo[]>(`/api/projects/${project}/fields`),
  promptVersions: (project: string, field: string) =>
    getJson<PromptVersion[]>(`/api/projects/${project}/fields/${field}/prompt-versions`),
  modelsSummary: (project: string, field: string, promptVersion?: number) =>
    getJson<ModelSummary[]>(
      `/api/projects/${project}/fields/${field}/models-summary${qs({ prompt_version: promptVersion })}`,
    ),
  iterations: (project: string, field: string, modelId?: string) =>
    getJson<IterationLog[]>(
      `/api/projects/${project}/fields/${field}/iterations${qs({ model_id: modelId })}`,
    ),
  thresholds: () => getJson<Thresholds>("/api/config/thresholds"),
  jobs: (project: string, field: string) => getJson<Job[]>(`/api/projects/${project}/fields/${field}/jobs`),
  confusion: (project: string, field: string, modelId?: string, promptVersion?: number) =>
    getJson<Confusion>(
      `/api/projects/${project}/fields/${field}/confusion${qs({ model_id: modelId, prompt_version: promptVersion })}`,
    ),
  llmJudgeSummary: (project: string, field: string, promptVersion?: number) =>
    getJson<LlmJudgeSummary[]>(
      `/api/projects/${project}/fields/${field}/llm-judge-summary${qs({ prompt_version: promptVersion })}`,
    ),
  crossModelAgreement: (project: string, field: string, promptVersion?: number) =>
    getJson<CrossModelAgreement[]>(
      `/api/projects/${project}/fields/${field}/cross-model-agreement${qs({ prompt_version: promptVersion })}`,
    ),
  selfConsistency: (project: string, field: string) =>
    getJson<SelfConsistency[]>(`/api/projects/${project}/fields/${field}/self-consistency`),
  calibration: (project: string, field: string, promptVersion?: number) =>
    getJson<Calibration[]>(
      `/api/projects/${project}/fields/${field}/calibration${qs({ prompt_version: promptVersion })}`,
    ),
  runVersions: (project: string, field: string) =>
    getJson<RunVersion[]>(`/api/projects/${project}/fields/${field}/run-versions`),
  stageStatus: (project: string, field: string, promptVersion?: number) =>
    getJson<StageStatus>(`/api/projects/${project}/fields/${field}/stage-status${qs({ prompt_version: promptVersion })}`),
  activity: (logLines = 30) =>
    getJson<ActivityData>(`/api/activity${qs({ log_lines: logLines })}`),
};
