/**
 * ImprovementHero — field cards showing baseline→current accuracy and Δ.
 *
 * This is the top-of-page answer to "is the system improving?" — a researcher
 * should see at a glance: which fields improved, by how much, and which need
 * attention. Each card shows:
 *   - Field name
 *   - Current best accuracy (large number)
 *   - Δ from baseline (green if positive, red if negative, — if no change)
 *   - Accepted iteration count ("2 accepted" or "no changes yet")
 *   - Status badge: improving / plateaued / done / needs review
 *
 * Clicking a card navigates to that field's detail view.
 */
import { type StageStatus, type FieldInfo } from "../api";

const REVIEW_STATUSES = new Set(["budget", "plateaued", "task_limited"]);

type HeroCardProps = {
  field: FieldInfo;
  status: StageStatus | null;
  baselineAccuracy: number | null;
  selected: boolean;
  onClick: () => void;
};

function bestAccuracy(s: StageStatus | null): number | null {
  if (!s || s.models.length === 0) return null;
  return Math.max(...s.models.map((m) => m.gate_metric));
}

function shortModel(id: string): string {
  return (id.split("/").pop() ?? id).replace(/^~/, "").replace(/-latest$/, "");
}

function statusInfo(
  status: StageStatus | null,
  acc: number | null,
): { label: string; cls: string; icon: string } {
  const needsReview = status?.n_needs_review != null
    ? status.n_needs_review > 0
    : status?.models.some((m) => m.opt_status && REVIEW_STATUSES.has(m.opt_status)) ?? false;
  if (needsReview) return { label: "Needs review", cls: "hero-badge--review", icon: "⏸" };
  if (acc === null) return { label: "No data yet", cls: "hero-badge--na", icon: "⋯" };
  const gate = status?.gate_threshold ?? 0.9;
  if (status && status.n_models_passing > 0) return { label: "Production-ready", cls: "hero-badge--pass", icon: "✅" };
  if (acc >= gate * 0.85) return { label: "Almost there", cls: "hero-badge--close", icon: "⚠" };
  if (acc >= gate * 0.70) return { label: "Improving", cls: "hero-badge--progress", icon: "↻" };
  return { label: "Needs work", cls: "hero-badge--far", icon: "✗" };
}

function HeroCard({ field, status, baselineAccuracy, selected, onClick }: HeroCardProps) {
  const acc = bestAccuracy(status);
  const gate = status?.gate_threshold ?? 0.9;
  const gatePct = Math.round(gate * 100);
  // For list fields this "best score" is element-level F1, not accuracy.
  const metricName = field.value_type === "single_categorical" ? "accuracy" : "F1";
  // Round DOWN so a sub-gate value (e.g. 0.895) never shows as "90%".
  const accPct = acc != null ? Math.floor(acc * 100) : null;
  // Compute Δ from the raw values (not the floored display) to avoid ±1 drift.
  const delta = acc != null && baselineAccuracy != null ? Math.round((acc - baselineAccuracy) * 100) : null;
  const accepted = status?.prompt_versions_accepted ?? 0;
  const { label, cls, icon } = statusInfo(status, acc);
  const leader = status && status.models.length > 0
    ? status.models.reduce((a, b) => (b.gate_metric > a.gate_metric ? b : a))
    : null;

  return (
    <button
      className={`hero-card${selected ? " hero-card--active" : ""}`}
      onClick={onClick}
      title={`Click to explore ${field.label} in detail`}
    >
      <span className="hero-card__name">{field.label}</span>
      <div className="hero-card__metric">
        {accPct != null ? (
          <span className="hero-card__pct" title={`${accPct}% ${metricName}`}>{accPct}%</span>
        ) : (
          <span className="hero-card__pct hero-card__pct--na">—</span>
        )}
        {delta != null && (
          <span className={`hero-card__delta ${delta > 0 ? "hero-card__delta--up" : delta < 0 ? "hero-card__delta--down" : ""}`}>
            {delta > 0 ? "↑" : delta < 0 ? "↓" : "—"}{delta !== 0 ? `${Math.abs(delta)}pt` : ""}
          </span>
        )}
      </div>
      <div className="hero-card__sub">
        {accepted > 0 ? (
          <span className="hero-card__accepted">{accepted} accepted</span>
        ) : (
          <span className="hero-card__accepted muted">no changes yet</span>
        )}
        {leader && (
          <span className="hero-card__leader muted"> · best: {shortModel(leader.model_id)}</span>
        )}
      </div>
      <div className={`hero-card__badge ${cls}`}>{icon} {label}</div>
      <div className="hero-card__gate muted">gate: {gatePct}% {metricName}</div>
    </button>
  );
}

export function ImprovementHero({
  fields,
  statuses,
  baselines,
  selectedField,
  onSelectField,
}: {
  fields: FieldInfo[];
  statuses: Map<string, StageStatus>;
  baselines: Map<string, number | null>;
  selectedField: string | null;
  onSelectField: (name: string) => void;
}) {
  if (fields.length === 0) return null;

  const totalAccepted = Array.from(statuses.values()).reduce((sum, s) => sum + (s?.prompt_versions_accepted ?? 0), 0);
  const totalNeedsReview = Array.from(statuses.values()).reduce((sum, s) => sum + (s?.n_needs_review ?? 0), 0);

  return (
    <section className="hero panel">
      <div className="hero-header">
        <h3 className="hero-title">Is the system improving?</h3>
        <p className="hero-subtitle muted">
          {totalAccepted > 0
            ? `${totalAccepted} prompt improvement${totalAccepted === 1 ? "" : "s"} accepted across ${fields.length} fields.`
            : "No prompt improvements accepted yet — extraction is running."}
          {totalNeedsReview > 0 && ` ${totalNeedsReview} field${totalNeedsReview === 1 ? "" : "s"} need human review.`}
        </p>
      </div>
      <div className="hero-cards">
        {fields.map((f) => (
          <HeroCard
            key={f.name}
            field={f}
            status={statuses.get(f.name) ?? null}
            baselineAccuracy={baselines.get(f.name) ?? null}
            selected={f.name === selectedField}
            onClick={() => onSelectField(f.name)}
          />
        ))}
      </div>
    </section>
  );
}
