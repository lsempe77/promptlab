import type { ModelSummary } from "../api";

function pct(x: number | null) {
  if (x == null) return "—";
  return `${(x * 100).toFixed(1)}%`;
}

function score(x: number | null) {
  if (x == null) return "—";
  return x.toFixed(3);
}

function usd(x: number | null) {
  if (x == null) return "—";
  return `$${x.toFixed(4)}`;
}

function ms(x: number | null) {
  if (x == null) return "—";
  return `${Math.round(x)} ms`;
}

export function ModelComparisonTable({ summaries }: { summaries: ModelSummary[] }) {
  if (summaries.length === 0) {
    return <p className="muted">No runs logged yet for this field.</p>;
  }
  return (
    <table className="model-table">
      <thead>
        <tr>
          <th>Model</th>
          <th># runs</th>
          <th>Mean score</th>
          <th>Accuracy</th>
          <th>Errors</th>
          <th>Mean latency</th>
          <th>Total cost</th>
        </tr>
      </thead>
      <tbody>
        {summaries.map((s) => (
          <tr key={s.model_id}>
            <td className="model-id">{s.model_id}</td>
            <td>{s.n}</td>
            <td>{score(s.mean_score)}</td>
            <td>{pct(s.accuracy)}</td>
            <td className={s.n_errors > 0 ? "has-errors" : ""}>{s.n_errors}</td>
            <td>{ms(s.mean_latency_ms)}</td>
            <td>{usd(s.total_cost_usd)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
