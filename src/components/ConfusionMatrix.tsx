import type { Confusion } from "../api";

function pct(x: number) {
  return `${(x * 100).toFixed(1)}%`;
}

export function ConfusionMatrix({ confusion }: { confusion: Confusion | null }) {
  if (!confusion) {
    return <p className="muted">Loading…</p>;
  }

  if (confusion.type === "list") {
    return (
      <div>
        <p className="muted panel-caption">
          List fields are open-set/multi-label, so a literal confusion matrix isn't meaningful —
          shown instead as matched (TP) / extra (FP) / missing (FN) item counts across all runs.
        </p>
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-value">{confusion.tp}</span>
            <span className="stat-label">matched (TP)</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{confusion.fp}</span>
            <span className="stat-label">extra (FP)</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{confusion.fn}</span>
            <span className="stat-label">missing (FN)</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{pct(confusion.precision)}</span>
            <span className="stat-label">precision</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{pct(confusion.recall)}</span>
            <span className="stat-label">recall</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{confusion.f1.toFixed(3)}</span>
            <span className="stat-label">F1</span>
          </div>
          <div className="stat-card highlight">
            <span className="stat-value">{confusion.f2.toFixed(3)}</span>
            <span className="stat-label">F2 (recall-weighted)</span>
          </div>
        </div>
      </div>
    );
  }

  if (confusion.n === 0) {
    return <p className="muted">No runs logged yet for this field.</p>;
  }

  return (
    <div>
      <p className="muted panel-caption">
        Rows = ground truth, columns = predicted. Diagonal = correct. {confusion.n} runs, overall
        accuracy {pct(confusion.accuracy)}.
      </p>
      <div className="confusion-scroll">
        <table className="confusion-table">
          <thead>
            <tr>
              <th></th>
              {confusion.pred_labels.map((label) => (
                <th key={label} title={label}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {confusion.truth_labels.map((rowLabel, i) => (
              <tr key={rowLabel}>
                <th title={rowLabel}>{rowLabel}</th>
                {confusion.matrix[i].map((count, j) => (
                  <td
                    key={j}
                    className={
                      confusion.pred_labels[j] === rowLabel
                        ? "diag" + (count > 0 ? " diag-hit" : "")
                        : count > 0
                          ? "off-diag-hit"
                          : ""
                    }
                  >
                    {count || ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
