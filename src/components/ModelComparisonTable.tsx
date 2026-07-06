import { useState } from "react";
import type { ModelSummary } from "../api";

function pct(x: number | null) {
  if (x == null) return "—";
  return `${(x * 100).toFixed(1)}%`;
}

function usd(x: number | null) {
  if (x == null) return "—";
  return `$${x.toFixed(4)}`;
}

function ms(x: number | null) {
  if (x == null) return "—";
  return `${Math.round(x)} ms`;
}

function co2(x: number | null) {
  if (x == null) return "—";
  if (x < 1) return `${(x * 1000).toFixed(0)} mg`;
  return `${x.toFixed(1)} g`;
}

type SortKey = "model_id" | "n" | "prompt_version" | "accuracy" | "n_errors" | "mean_latency_ms" | "total_cost_usd" | "total_co2e_grams";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "model_id", label: "Model" },
  { key: "n", label: "# References" },
  { key: "prompt_version", label: "Prompt v." },
  { key: "accuracy", label: "Accuracy" },
  { key: "n_errors", label: "Errors" },
  { key: "mean_latency_ms", label: "Mean latency" },
  { key: "total_cost_usd", label: "Total cost" },
  { key: "total_co2e_grams", label: "CO₂e" },
];

export function ModelComparisonTable({ summaries }: { summaries: ModelSummary[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("accuracy");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  if (summaries.length === 0) {
    return <p className="muted">No references processed yet for this field.</p>;
  }

  const sorted = [...summaries].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
    return sortDir === "asc" ? cmp : -cmp;
  });

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "model_id" ? "asc" : "desc");
    }
  }

  return (
    <table className="model-table">
      <thead>
        <tr>
          {COLUMNS.map((col) => (
            <th
              key={col.key}
              className="sortable-th"
              onClick={() => toggleSort(col.key)}
              aria-sort={sortKey === col.key ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
            >
              {col.label}
              <span className="sort-indicator">{sortKey === col.key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}</span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((s) => (
          <tr key={s.model_id}>
            <td className="model-id">{s.model_id}</td>
            <td>{s.n}</td>
            <td>{s.prompt_version != null ? `v${s.prompt_version}` : "—"}</td>
            <td>{pct(s.accuracy)}</td>
            <td className={s.n_errors > 0 ? "has-errors" : ""}>{s.n_errors}</td>
            <td>{ms(s.mean_latency_ms)}</td>
            <td>{usd(s.total_cost_usd)}</td>
            <td>{co2(s.total_co2e_grams)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
