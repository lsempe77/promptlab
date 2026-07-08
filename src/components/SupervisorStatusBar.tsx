import { useEffect, useState } from "react";
import { api } from "../api";
import type { StageStatus, Job } from "../api";

interface FieldState {
  status: StageStatus | null;
  jobs: Job[];
  error: boolean;
}

const FIELDS = ["authors", "author_country", "author_affiliation", "sector_name", "sub_sector"] as const;
const FIELD_LABELS: Record<string, string> = {
  authors: "Authors",
  author_country: "Country",
  author_affiliation: "Affiliation",
  sector_name: "Sector",
  sub_sector: "Sub-sector",
};
const POLL_MS = 15_000;
const TOTAL_MODELS = 13;

function fieldActionLabel(jobs: Job[]): { label: string; kind: "extraction" | "optimization" | "judge" | null } {
  const running = jobs.find((j) => j.status === "running" && !j.stale);
  if (!running) return { label: "", kind: null };
  const model = running.model_id ? running.model_id.split("/").pop()!.slice(0, 18) : "";
  if (running.kind === "extraction") return { label: `extracting · ${model}`, kind: "extraction" };
  if (running.kind === "optimization") return { label: `optimizing · ${model}`, kind: "optimization" };
  return { label: "judging", kind: "judge" };
}

function MiniBar({ passing, total }: { passing: number; total: number }) {
  const pct = total > 0 ? Math.round((passing / total) * 100) : 0;
  return (
    <div className="sbar-minibar" title={`${passing}/${total} models pass gate`}>
      <div className="sbar-minibar-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function SupervisorStatusBar({ project }: { project: string }) {
  const [fields, setFields] = useState<Record<string, FieldState>>(() =>
    Object.fromEntries(FIELDS.map((f) => [f, { status: null, jobs: [], error: false }]))
  );
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchAll = async () => {
    const results = await Promise.all(
      FIELDS.map(async (f) => {
        try {
          const [status, jobs] = await Promise.all([
            api.stageStatus(project, f),
            api.jobs(project, f),
          ]);
          return [f, { status, jobs, error: false }] as const;
        } catch {
          return [f, { status: null, jobs: [], error: true }] as const;
        }
      })
    );
    setFields(Object.fromEntries(results));
    setLastUpdated(new Date());
  };

  useEffect(() => {
    fetchAll();
    const id = window.setInterval(fetchAll, POLL_MS);
    return () => window.clearInterval(id);
  }, [project]);

  const totalPassing = FIELDS.reduce((sum, f) => sum + (fields[f].status?.n_models_passing ?? 0), 0);
  const totalPairs = FIELDS.length * TOTAL_MODELS;
  const overallPct = Math.round((totalPassing / totalPairs) * 100);
  const anyRunning = FIELDS.some((f) => fields[f].jobs.some((j) => j.status === "running" && !j.stale));

  const secAgo = lastUpdated ? Math.round((Date.now() - lastUpdated.getTime()) / 1000) : null;

  return (
    <div className="sbar-card">
      <div className="sbar-header">
        <span className="sbar-title">
          <span className={`sbar-dot ${anyRunning ? "running" : "idle"}`} />
          Supervisor
        </span>
        <span className="sbar-overall">
          <strong>{totalPassing}</strong>/{totalPairs} model·field pairs pass gate
          <span className="sbar-pct"> ({overallPct}%)</span>
        </span>
        <span className="sbar-updated">
          {secAgo !== null ? `${secAgo}s ago` : "…"}
        </span>
      </div>

      <div className="sbar-fields">
        {FIELDS.map((f) => {
          const { status, jobs } = fields[f];
          const passing = status?.n_models_passing ?? 0;
          const evaluated = status?.n_models_evaluated ?? 0;
          const refs = status?.references ?? 0;
          const { label: actionLabel, kind: actionKind } = fieldActionLabel(jobs);
          const done = passing === TOTAL_MODELS;

          return (
            <div key={f} className={`sbar-field ${done ? "sbar-done" : ""}`}>
              <span className="sbar-fname">{FIELD_LABELS[f]}</span>
              <MiniBar passing={passing} total={TOTAL_MODELS} />
              <span className="sbar-counts">
                {passing}/{TOTAL_MODELS}
                {refs > 0 && <span className="sbar-refs"> · {refs} refs</span>}
              </span>
              {actionLabel ? (
                <span className={`sbar-action sbar-action-${actionKind}`}>{actionLabel}</span>
              ) : done ? (
                <span className="sbar-action sbar-action-done">✓ done</span>
              ) : evaluated === 0 ? (
                <span className="sbar-action sbar-action-wait">waiting</span>
              ) : (
                <span className="sbar-action sbar-action-wait">idle</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
