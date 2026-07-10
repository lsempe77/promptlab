import { useEffect, useState } from "react";
import { api } from "../api";
import type { StageStatus, Job, FieldInfo } from "../api";

interface FieldState {
  status: StageStatus | null;
  jobs: Job[];
  error: boolean;
}

const POLL_MS = 15_000;

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
  // Fields and the per-field model count are derived from the project itself, not
  // hardcoded — so this bar is correct for any project (extraction, screening, …),
  // not just the default extraction one.
  const [fieldList, setFieldList] = useState<FieldInfo[]>([]);
  const [states, setStates] = useState<Record<string, FieldState>>({});
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    let pollId: number | undefined;

    const fetchAll = async (fields: FieldInfo[]) => {
      const results = await Promise.all(
        fields.map(async (f) => {
          try {
            const [status, jobs] = await Promise.all([
              api.stageStatus(project, f.name),
              api.jobs(project, f.name),
            ]);
            return [f.name, { status, jobs, error: false }] as const;
          } catch {
            return [f.name, { status: null, jobs: [], error: true }] as const;
          }
        })
      );
      if (cancelled) return;
      setStates(Object.fromEntries(results));
      setLastUpdated(new Date());
    };

    (async () => {
      let fields: FieldInfo[];
      try {
        fields = await api.fields(project);
      } catch {
        if (!cancelled) { setFieldList([]); setStates({}); }
        return;
      }
      if (cancelled) return;
      setFieldList(fields);
      await fetchAll(fields);
      pollId = window.setInterval(() => fetchAll(fields), POLL_MS);
    })();

    return () => {
      cancelled = true;
      if (pollId) window.clearInterval(pollId);
    };
  }, [project]);

  const totalPassing = fieldList.reduce((sum, f) => sum + (states[f.name]?.status?.n_models_passing ?? 0), 0);
  // Denominator = models actually evaluated per field, summed. No fixed roster size.
  const totalPairs = fieldList.reduce((sum, f) => sum + (states[f.name]?.status?.n_models_evaluated ?? 0), 0);
  const overallPct = totalPairs > 0 ? Math.round((totalPassing / totalPairs) * 100) : 0;
  const anyRunning = fieldList.some((f) =>
    (states[f.name]?.jobs ?? []).some((j) => j.status === "running" && !j.stale)
  );

  const secAgo = lastUpdated ? Math.round((Date.now() - lastUpdated.getTime()) / 1000) : null;

  if (fieldList.length === 0) return null;

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
        {fieldList.map((f) => {
          const { status, jobs } = states[f.name] ?? { status: null, jobs: [], error: false };
          const passing = status?.n_models_passing ?? 0;
          const evaluated = status?.n_models_evaluated ?? 0;
          const refs = status?.references ?? 0;
          const { label: actionLabel, kind: actionKind } = fieldActionLabel(jobs);
          const done = evaluated > 0 && passing === evaluated;

          return (
            <div key={f.name} className={`sbar-field ${done ? "sbar-done" : ""}`}>
              <span className="sbar-fname">{f.label || f.name}</span>
              <MiniBar passing={passing} total={evaluated} />
              <span className="sbar-counts">
                {passing}/{evaluated}
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
