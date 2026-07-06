import { useState, type ReactNode } from "react";
import type { ModelSummary, StageModelGate } from "../api";

function pct(x: number | null) {
  if (x == null) return "—";
  return `${(x * 100).toFixed(1)}%`;
}

function num3(x: number | null) {
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

function co2(x: number | null) {
  if (x == null) return "—";
  if (x < 1) return `${(x * 1000).toFixed(0)} mg`;
  return `${x.toFixed(1)} g`;
}

// A model row merged from its run-summary and its per-model gate metrics.
type Row = ModelSummary & {
  gate_metric: number | null;
  gate_passed: boolean | null;
  precision: number | null;
  recall: number | null;
  kappa: number | null;
  judged: number | null;
};

interface Col {
  key: string;
  label: string;
  title?: string; // hover definition
  numeric: boolean;
  get: (r: Row) => number | string | null;
  render: (r: Row) => ReactNode;
}

export function ModelComparisonTable({
  summaries,
  stageModels = [],
  valueType = "",
}: {
  summaries: ModelSummary[];
  stageModels?: StageModelGate[];
  valueType?: string;
}) {
  const isList = valueType !== "single_categorical";
  const qualityLabel = isList ? "Quality (F1)" : "Quality (acc.)";
  const [sortKey, setSortKey] = useState<string>("gate_metric");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  if (summaries.length === 0) {
    return <p className="muted">No references processed yet for this field.</p>;
  }

  const gateBy = new Map(stageModels.map((m) => [m.model_id, m]));
  const rows: Row[] = summaries.map((s) => {
    const g = gateBy.get(s.model_id);
    return {
      ...s,
      gate_metric: g?.gate_metric ?? null,
      gate_passed: g?.gate_passed ?? null,
      precision: g?.precision ?? null,
      recall: g?.recall ?? null,
      kappa: g?.kappa ?? null,
      judged: g?.llm_judged_accuracy ?? null,
    };
  });

  // Columns lead with the DECISION metric (the gate), then the metrics that
  // explain it (precision/recall for list fields, kappa for categorical), then
  // corroboration (LLM-judge concordance), then the demoted heuristic
  // (fuzzy-match), then cost/footprint. Precision/recall only apply to the
  // multi-value list fields; kappa only to the single-categorical ones.
  const cols: Col[] = [
    { key: "model_id", label: "Model", numeric: false, get: (r) => r.model_id, render: (r) => <span className="model-id">{r.model_id}</span> },
    { key: "n", label: "# Refs", numeric: true, get: (r) => r.n, render: (r) => r.n },
    { key: "prompt_version", label: "Prompt v.", numeric: true, get: (r) => r.prompt_version, render: (r) => (r.prompt_version != null ? `v${r.prompt_version}` : "—") },
    {
      key: "gate_metric",
      label: qualityLabel,
      title: isList
        ? "Element-level F1 (precision & recall balanced) — the production gate metric for list fields."
        : "Record-level accuracy — the production gate metric for categorical fields.",
      numeric: true,
      get: (r) => r.gate_metric,
      render: (r) => (
        <span className={r.gate_passed == null ? "" : r.gate_passed ? "gate-ok" : "gate-bad"}>
          {pct(r.gate_metric)}
        </span>
      ),
    },
    ...(isList
      ? [
          { key: "precision", label: "Precision", title: "Of the values the model reported, the share that were correct (penalises wrong extras).", numeric: true, get: (r: Row) => r.precision, render: (r: Row) => pct(r.precision) },
          { key: "recall", label: "Recall", title: "Of the true values, the share the model found (penalises misses). Also called sensitivity.", numeric: true, get: (r: Row) => r.recall, render: (r: Row) => pct(r.recall) },
        ]
      : [
          { key: "kappa", label: "Cohen's κ", title: "Chance-corrected agreement — accuracy discounted for how often the categories would match by luck.", numeric: true, get: (r: Row) => r.kappa, render: (r: Row) => num3(r.kappa) },
        ]),
    { key: "judged", label: "Concordance", title: "Semantic accuracy from a cross-family LLM judge (\"same real-world value?\"). An independent corroboration of the gate.", numeric: true, get: (r) => r.judged, render: (r) => pct(r.judged) },
    { key: "accuracy", label: "Fuzzy-match", title: "Heuristic string-match rate (fuzzy matches count as correct). Superseded by the gate metric; shown for reference.", numeric: true, get: (r) => r.accuracy, render: (r) => pct(r.accuracy) },
    { key: "n_errors", label: "Errors", numeric: true, get: (r) => r.n_errors, render: (r) => <span className={r.n_errors > 0 ? "has-errors" : ""}>{r.n_errors}</span> },
    { key: "mean_latency_ms", label: "Latency", numeric: true, get: (r) => r.mean_latency_ms, render: (r) => ms(r.mean_latency_ms) },
    { key: "total_cost_usd", label: "Cost", numeric: true, get: (r) => r.total_cost_usd, render: (r) => usd(r.total_cost_usd) },
    { key: "total_co2e_grams", label: "CO₂e", numeric: true, get: (r) => r.total_co2e_grams, render: (r) => co2(r.total_co2e_grams) },
  ];

  const col = cols.find((c) => c.key === sortKey) ?? cols[0];
  const sorted = [...rows].sort((a, b) => {
    const av = col.get(a);
    const bv = col.get(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === "string" ? av.localeCompare(bv as string) : (av as number) - (bv as number);
    return sortDir === "asc" ? cmp : -cmp;
  });

  function toggleSort(key: string, numeric: boolean) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(numeric ? "desc" : "asc");
    }
  }

  return (
    <table className="model-table">
      <thead>
        <tr>
          {cols.map((c) => (
            <th
              key={c.key}
              className="sortable-th"
              title={c.title}
              onClick={() => toggleSort(c.key, c.numeric)}
              aria-sort={sortKey === c.key ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
            >
              {c.label}
              <span className="sort-indicator">{sortKey === c.key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}</span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.model_id}>
            {cols.map((c) => (
              <td key={c.key}>{c.render(r)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
