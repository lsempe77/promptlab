import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  Cell,
  CartesianGrid,
  ScatterChart,
  Scatter,
  LabelList,
} from "recharts";
import type { ModelSummary, StageModelGate } from "../api";

const PASS = "#2e7d4f";
const FAIL = "#c79016";

function short(id: string): string {
  return (id.split("/").pop() ?? id).replace(/^~/, "").replace(/-latest$/, "");
}

interface Row {
  model: string;
  quality: number | null; // gate metric, 0-100
  passed: boolean | null;
  cost1k: number | null; // $ per 1000 references
  co2: number | null;
}

export function AggregateCharts({
  summaries,
  stageModels,
  valueType,
  gateThreshold,
}: {
  summaries: ModelSummary[];
  stageModels: StageModelGate[];
  valueType: string;
  gateThreshold: number | null;
}) {
  const gateBy = new Map(stageModels.map((m) => [m.model_id, m]));
  const rows: Row[] = summaries.map((s) => {
    const g = gateBy.get(s.model_id);
    const q = g?.gate_metric ?? null;
    return {
      model: short(s.model_id),
      quality: q != null ? q * 100 : null,
      passed: g?.gate_passed ?? null,
      cost1k: s.total_cost_usd != null && s.n > 0 ? (s.total_cost_usd / s.n) * 1000 : null,
      co2: s.total_co2e_grams,
    };
  });

  const bars = rows.filter((r) => r.quality != null).sort((a, b) => (b.quality ?? 0) - (a.quality ?? 0));
  const scatter = rows.filter((r) => r.quality != null && r.cost1k != null);
  const gatePct = gateThreshold != null ? gateThreshold * 100 : 90;
  const qLabel = valueType === "single_categorical" ? "accuracy" : "F1";

  if (bars.length === 0) {
    return <p className="muted">Not enough evaluated models yet to plot.</p>;
  }

  return (
    <div className="aggregate-charts">
      <div className="agg-chart">
        <h4 className="agg-chart-title">Quality leaderboard ({qLabel} per model)</h4>
        <ResponsiveContainer width="100%" height={Math.max(160, bars.length * 30 + 40)}>
          <BarChart data={bars} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
            <CartesianGrid horizontal={false} strokeDasharray="3 3" />
            <XAxis type="number" domain={[0, 100]} tickFormatter={(v) => `${v}%`} fontSize={11} />
            <YAxis type="category" dataKey="model" width={120} fontSize={11} interval={0} />
            <Tooltip formatter={(v: any) => [`${Number(v).toFixed(1)}%`, qLabel]} />
            <ReferenceLine x={gatePct} stroke="#555" strokeDasharray="4 3" label={{ value: `gate ${gatePct.toFixed(0)}%`, position: "top", fontSize: 10 }} />
            <Bar dataKey="quality" radius={[0, 3, 3, 0]}>
              {bars.map((r, i) => (
                <Cell key={i} fill={r.passed ? PASS : FAIL} />
              ))}
              <LabelList dataKey="quality" position="right" formatter={(v: any) => `${Number(v).toFixed(0)}%`} fontSize={10} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="agg-chart">
        <h4 className="agg-chart-title">Cost vs quality (efficient models are upper-left)</h4>
        <ResponsiveContainer width="100%" height={280}>
          <ScatterChart margin={{ top: 8, right: 24, bottom: 28, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="cost1k"
              name="Cost"
              tickFormatter={(v) => `$${v.toFixed(2)}`}
              fontSize={11}
              label={{ value: "Cost per 1,000 references ($)", position: "bottom", fontSize: 11 }}
            />
            <YAxis type="number" dataKey="quality" name={qLabel} domain={[0, 100]} tickFormatter={(v) => `${v}%`} fontSize={11} />
            <Tooltip
              cursor={{ strokeDasharray: "3 3" }}
              formatter={(v: any, name: any) => (name === "Cost" ? [`$${Number(v).toFixed(3)}`, "Cost /1k"] : [`${Number(v).toFixed(1)}%`, qLabel])}
              labelFormatter={() => ""}
            />
            <ReferenceLine y={gatePct} stroke="#555" strokeDasharray="4 3" label={{ value: `gate ${gatePct.toFixed(0)}%`, position: "right", fontSize: 10 }} />
            <Scatter data={scatter} fill="#3a6ea5">
              {scatter.map((r, i) => (
                <Cell key={i} fill={r.passed ? PASS : FAIL} />
              ))}
              <LabelList dataKey="model" position="top" fontSize={9} />
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
