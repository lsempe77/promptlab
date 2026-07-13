/**
 * FieldOverview — cross-field progress panel.
 *
 * Shows one row per field with a progress bar toward the 90% accuracy target,
 * the best-model accuracy, and a plain-English status label.
 * Clicking a row navigates to that field.
 */
import { useEffect, useState } from "react";
import { api, type StageStatus, type FieldInfo } from "../api";

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

function statusLabel(
  acc: number | null,
  passing: number,
  needsReview: boolean,
  gate: number,
): { icon: string; label: string; cls: string } {
  // Needs-review takes priority over the pure-accuracy label: a field at 80%
  // that's plateaued is NOT "still improving" — the optimizer has given up.
  if (needsReview) {
    return { icon: "⏸", label: "Needs human review", cls: "fo-status-review" };
  }
  if (acc === null) return { icon: "⋯", label: "No data yet", cls: "fo-status-na" };
  if (passing > 0) return { icon: "✅", label: "Good enough to use", cls: "fo-status-pass" };
  // The 0.85 / 0.70 fractions below are cosmetic "how close" display bands, NOT
  // gate constants — unrelated to the recall floor (scoring.RECALL_FLOOR=0.85).
  if (acc >= gate * 0.85) return { icon: "⚠", label: "Almost there", cls: "fo-status-close" };
  if (acc >= gate * 0.70) return { icon: "↻", label: "Still improving", cls: "fo-status-progress" };
  return { icon: "✗", label: "Needs more work", cls: "fo-status-far" };
}

function pct(n: number | null) {
  if (n == null) return "—";
  // Round DOWN so a value below the gate (e.g. 0.895) never displays as "90%"
  // and reads as passing when it isn't.
  return `${Math.floor(n * 100)}%`;
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
      }).catch(() => {
        // Silently leave the row at null (shows "No data yet") — a per-field
        // API failure shouldn't crash the whole overview.  The App-level
        // ErrorBoundary catches persistent failures.
      });
    });
    return () => { cancelled = true; };
  }, [project, fields]);

  if (rows.length === 0) return null;

  return (
    <section className="field-overview panel">
      <div className="fo-header">
        <h3 className="fo-title">How accurate are our AIs at pulling out each piece of information?</h3>
        <p className="fo-subtitle muted">
          We accept an AI when its quality score reaches 90%. Bars show the best AI's score for
          each task — that's F1 for list fields (authors, affiliations, countries) and accuracy for
          single-choice fields (sector, sub-sector).
        </p>
      </div>
      <div className="fo-rows">
        {rows.map(({ field, status }) => {
          const acc = bestAccuracy(status);
          const passing = status?.n_models_passing ?? 0;
          const gate = status?.gate_threshold ?? 0.9;
          const needsReview = status?.n_needs_review != null
            ? status.n_needs_review > 0
            : status?.models.some((m) => m.opt_status && REVIEW_STATUSES.has(m.opt_status)) ?? false;
          const { icon, label, cls } = statusLabel(acc, passing, needsReview, gate);
          const barPct = acc != null ? Math.min(acc * 100, 100) : 0;
          const gatePct = gate * 100;
          const isSelected = field.name === selectedField;
          // For list fields the score is element-level F1, not accuracy.
          const metricName = field.value_type === "single_categorical" ? "accuracy" : "F1";
          // Green only when the backend says a model actually passes the gate
          // (which includes the recall floor for list fields) — NOT on the bare
          // best-metric >= threshold, which would ignore that floor.
          const barCls = passing > 0 ? "fo-bar--pass" : acc != null && acc >= gate * 0.85 ? "fo-bar--close" : "fo-bar--fail";

          return (
            <button
              key={field.name}
              className={`fo-row${isSelected ? " fo-row--active" : ""}`}
              onClick={() => onSelectField(field.name)}
              title={`Click to explore ${field.label} in detail`}
            >
              <span className="fo-field-label">{field.label}</span>
              <div className="fo-bar-wrap" aria-label={`${pct(acc)} ${metricName}`}>
                <div
                  className={`fo-bar ${barCls}`}
                  style={{ width: `${barPct}%` }}
                />
                {/* Gate marker */}
                <div className="fo-gate-line" style={{ left: `${gatePct}%` }} />
              </div>
              <span className="fo-pct" title={`${pct(acc)} ${metricName}`}>{pct(acc)}</span>
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
