export const API_BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || "http://127.0.0.1:8000";

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
  mean_score: number;
  n_errors: number;
  mean_latency_ms: number | null;
  total_cost_usd: number | null;
  accuracy: number;
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
  n: number;
}

export interface ListConfusion {
  type: "list";
  tp: number;
  fp: number;
  fn: number;
  precision: number;
  recall: number;
  f1: number;
  f2: number;
  n: number;
}

export type Confusion = CategoricalConfusion | ListConfusion;

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`API error ${res.status} for ${path}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  fields: () => getJson<FieldInfo[]>("/api/fields"),
  promptVersions: (field: string) => getJson<PromptVersion[]>(`/api/fields/${field}/prompt-versions`),
  modelsSummary: (field: string) => getJson<ModelSummary[]>(`/api/fields/${field}/models-summary`),
  iterations: (field: string) => getJson<IterationLog[]>(`/api/fields/${field}/iterations`),
  thresholds: () => getJson<Thresholds>("/api/config/thresholds"),
  confusion: (field: string) => getJson<Confusion>(`/api/fields/${field}/confusion`),
};
