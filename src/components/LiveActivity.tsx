import { useEffect, useRef, useState } from "react";
import { api, type ActivityData, type OptimizerHealth } from "../api";

const KIND_LABEL: Record<string, string> = {
  extraction: "extract",
  optimization: "optimize",
  judge: "judge",
};

function shortModel(m: string | null) {
  if (!m) return "*";
  return m.split("/").pop() ?? m;
}

function relTime(iso: string | null): string {
  if (!iso) return "";
  const diff = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

function OptimizerHealthBadge({ health }: { health?: OptimizerHealth }) {
  if (!health) return null;
  const { failure_rate, accept_rate, runs_24h, failed_24h } = health;
  // Highlight failure rate > 20% (red) or > 5% (amber) — the exact bug the
  // system had (68% crash rate shown as "idle") must never be invisible again.
  const fr = failure_rate;
  let cls = "oh-badge--ok";
  let label = "optimizer healthy";
  if (fr > 0.20) {
    cls = "oh-badge--bad";
    label = `optimizer failing ${Math.round(fr * 100)}%`;
  } else if (fr > 0.05) {
    cls = "oh-badge--warn";
    label = `optimizer ${Math.round(fr * 100)}% failures`;
  } else if (runs_24h === 0) {
    cls = "oh-badge--ok";
    label = "no optimizer runs (24h)";
  }
  const accPct = accept_rate != null ? `${Math.round(accept_rate * 100)}%` : "—";
  return (
    <span
      className={`oh-badge ${cls}`}
      title={`Last 24h: ${runs_24h} runs, ${failed_24h} failed (${Math.round(fr * 100)}%). Accept rate: ${accPct} of candidates.`}
    >
      {label}
      {runs_24h > 0 && <span className="oh-accept"> · accept {accPct}</span>}
    </span>
  );
}

export function LiveActivity() {
  const [data, setData] = useState<ActivityData | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [logExpanded, setLogExpanded] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const fetch = () =>
      api.activity(40).then((d) => { if (!cancelled) setData(d); }).catch(() => {});
    fetch();
    const id = setInterval(fetch, 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);

  // Auto-scroll log to bottom when it expands or new lines arrive
  useEffect(() => {
    if (logExpanded && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logExpanded, data?.log_tail]);

  if (!data) return null;

  const { queue, active_tasks, recently_done, log_tail, optimizer_health } = data;
  const isActive = queue.total_active > 0;

  // Most recent supervisor decision from the log
  const lastDecision = [...log_tail].reverse().find(
    (l) => l.includes("->") || l.includes("Queue drained") || l.includes("Sleeping") || l.includes("Waiting")
  ) ?? null;

  return (
    <div className={`live-activity ${isActive ? "live-activity--active" : "live-activity--idle"}`}>
      {/* ── Header row ─────────────────────────────────────────────────────── */}
      <div className="live-activity__header" onClick={() => setExpanded((e) => !e)}>
        <span className={`live-dot ${isActive ? "live-dot--pulse" : ""}`} />
        <span className="live-activity__title">
          {isActive
            ? `${queue.running} running · ${queue.pending} queued`
            : "Idle"}
        </span>
        <OptimizerHealthBadge health={optimizer_health} />
        {lastDecision && (
          <span className="live-activity__last-line" title={lastDecision}>
            {lastDecision.replace(/^\[.*?Z\]\s*/, "").slice(0, 80)}
          </span>
        )}
        <span className="live-activity__toggle">{expanded ? "▲" : "▼"}</span>
      </div>

      {/* ── Expanded detail ─────────────────────────────────────────────────── */}
      {expanded && (
        <div className="live-activity__body">
          {/* Active tasks */}
          {active_tasks.length > 0 && (
            <div className="live-task-list">
              {active_tasks.map((t, i) => (
                <div key={i} className={`live-task live-task--${t.status}`}>
                  <span className="live-task__kind">{KIND_LABEL[t.kind] ?? t.kind}</span>
                  <span className="live-task__field">{t.field_name}</span>
                  <span className="live-task__model">{shortModel(t.model_id)}</span>
                  <span className="live-task__time muted">
                    {t.status === "running" ? relTime(t.claimed_at) : "queued " + relTime(t.created_at)}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Recently done */}
          {recently_done.length > 0 && (
            <div className="live-task-list live-task-list--done">
              <div className="live-task-list__header muted">Recently finished</div>
              {recently_done.map((t, i) => (
                <div key={i} className={`live-task live-task--${t.status}`}>
                  <span className="live-task__kind">{KIND_LABEL[t.kind] ?? t.kind}</span>
                  <span className="live-task__field">{t.field_name}</span>
                  <span className="live-task__model">{shortModel(t.model_id)}</span>
                  {t.error && <span className="live-task__error" title={t.error}>⚠</span>}
                  <span className="live-task__time muted">{relTime(t.finished_at)}</span>
                </div>
              ))}
            </div>
          )}

          {/* Supervisor log toggle */}
          <div className="live-log-toggle" onClick={() => setLogExpanded((e) => !e)}>
            Supervisor log {logExpanded ? "▲" : "▼"}
          </div>
          {logExpanded && (
            <div className="live-log" ref={logRef}>
              {log_tail.map((line, i) => {
                const isDecision = line.includes("->") || line.includes("OPTIMIZE") || line.includes("EXTRACT") || line.includes("JUDGE") || line.includes("STUCK");
                const isDrain = line.includes("Queue drained") || line.includes("Sleeping");
                return (
                  <div key={i} className={`live-log__line ${isDecision ? "live-log__line--decision" : ""} ${isDrain ? "live-log__line--drain" : ""}`}>
                    {line}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
