/**
 * FieldOverview — cross-field progress panel.
 *
 * Shows one row per field with a progress bar toward the 90% accuracy target,
 * the best-model accuracy, and a plain-English status label.
 * Clicking a row navigates to that field.
 */
import { useEffect, useState } from "react";
import { api, type StageStatus, type FieldInfo } from "../api";

const GATE = 0.9;

type FieldRow = {
  field: FieldInfo;
  status: StageStatus | null;
};

function bestAccuracy(s: StageStatus | null): number | null {
  if (!s || s.models.length === 0) return null;
  return Math.max(...s.models.map((m) => m.gate_metric));
}

// The optimizer cost-benefit policy statuses that mean "stop auto-optimizing,
// this needs a human" — surfaced from stage-status.opt_status per model.
const REVIEW_STATUSES = new Set(["budget", "plateaued", "task_limited"]);

function statusLabel(acc: number | null, passing: number, needsReview: boolean): {
  icon: string; label: string; cls: string;
} {
  // Needs-review takes priority over the pure-accuracy label: a field at 80%
  // that's plateaued is NOT "still improving" — the optimizer has given up.
  if (needsReview) {
    return { icon: "⏸", label: "Needs human review", cls: "fo-status-review" };
  }
  if (acc === null) return { icon: "⋯", label: "No data yet", cls: "fo-status-na" };
  if (passing > 0) return { icon: "✅", label: "Good enough to use", cls: "fo-status-pass" };
  if (acc >= 0.85) return { icon: "⚠", label: "Almost there", cls: "fo-status-close" };
  if (acc >= 0.70) return { icon: "↻", label: "Still improving", cls: "fo-status-progress" };
  return { icon: "✗", label: "Needs more work", cls: "fo-status-far" };
}

function pct(n: number | null) {
  if (n == null) return "—";
  return `${Math.round(n * 100)}%`;
}

export function FieldOverview({
  project,
  fields,
  selectedField,
  onSelectField,
}: {
  project: string;
  fields: FieldInfo[];
  selectedField: string | null;
  onSelectField: (name: string) => void;
}) {
  const [rows, setRows] = useState<FieldRow[]>([]);

  useEffect(() => {
    if (!project || fields.length === 0) return;
    let cancelled = false;
    // Initialise with nulls, fill as promises resolve
    setRows(fields.map((f) => ({ field: f, status: null })));
    fields.forEach((f, i) => {
      api.stageStatus(project, f.name).then((s) => {
        if (cancelled) return;  // a slow response from a prior project must not write here
        setRows((prev) => {
          const next = [...prev];
          next[i] = { field: f, status: s };
          return next;
        });
      }).catch(() => {});
    });
    return () => { cancelled = true; };
  }, [project, fields]);

  if (rows.length === 0) return null;

  const gatePct = GATE * 100;

  return (
    <section className="field-overview panel">
      <div className="fo-header">
        <h3 className="fo-title">How accurate are our AIs at pulling out each piece of information?</h3>
        <p className="fo-subtitle muted">
          We accept an AI when it gets answers right at least {gatePct}% of the time.
          Bars show the best AI's accuracy for each task.
        </p>
      </div>
      <div className="fo-rows">
        {rows.map(({ field, status }) => {
          const acc = bestAccuracy(status);
          const passing = status?.n_models_passing ?? 0;
          const needsReview = status?.n_needs_review != null
            ? status.n_needs_review > 0
            : status?.models.some((m) => m.opt_status && REVIEW_STATUSES.has(m.opt_status)) ?? false;
          const { icon, label, cls } = statusLabel(acc, passing, needsReview);
          const barPct = acc != null ? Math.min(acc * 100, 100) : 0;
          const isSelected = field.name === selectedField;

          return (
            <button
              key={field.name}
              className={`fo-row${isSelected ? " fo-row--active" : ""}`}
              onClick={() => onSelectField(field.name)}
              title={`Click to explore ${field.label} in detail`}
            >
              <span className="fo-field-label">{field.label}</span>
              <div className="fo-bar-wrap" aria-label={`${pct(acc)} accuracy`}>
                <div
                  className={`fo-bar ${acc != null && acc >= GATE ? "fo-bar--pass" : acc != null && acc >= 0.85 ? "fo-bar--close" : "fo-bar--fail"}`}
                  style={{ width: `${barPct}%` }}
                />
                {/* 90% gate marker */}
                <div className="fo-gate-line" style={{ left: `${gatePct}%` }} />
              </div>
              <span className="fo-pct">{pct(acc)}</span>
              <span className={`fo-status ${cls}`}>{icon} {label}</span>
            </button>
          );
        })}
      </div>
      <p className="fo-footnote muted">
        Click any row to see details and explore individual AI models.
      </p>
    </section>
  );
}
