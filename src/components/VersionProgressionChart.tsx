/**
 * VersionProgressionChart — line chart of accuracy per prompt version.
 *
 * The hero of the field-detail view: shows whether each accepted prompt
 * version actually improved accuracy. Includes:
 *   - A reference line at the gate threshold
 *   - Green dots for accepted versions, grey for rejected
 *   - Δ labels between versions showing the improvement
 *   - The baseline (v1) as the starting point
 *
 * This directly answers "did the optimizer's accepted revisions make things
 * better?" — the #1 question for 3ie researchers.
 */
import { LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine, Tooltip, ResponsiveContainer, LabelList, Dot } from "recharts";
import type { StageModelGate } from "../api";

type VersionPoint = {
  version: number;
  accepted: boolean;
  bestMetric: number | null;
  bestModel: string | null;
};

function shortModel(id: string): string {
  return (id.split("/").pop() ?? id).replace(/^~/, "").replace(/-latest$/, "");
}

export function VersionProgressionChart({
  versionData,
  gateThreshold,
  metricName,
}: {
  versionData: { version: number; accepted: number; models: StageModelGate[] }[];
  gateThreshold: number | null;
  metricName: string;
}) {
  if (versionData.length < 2) {
    return (
      <div className="vpc-empty">
        <p className="muted">
          {versionData.length === 0
            ? "No prompt versions yet — extraction is running."
            : "Only the baseline prompt so far. Optimizer hasn't accepted any revisions yet."}
        </p>
      </div>
    );
  }

  const points: VersionPoint[] = versionData.map((v) => {
    const best = v.models.length > 0
      ? v.models.reduce((a, b) => (b.gate_metric > a.gate_metric ? b : a))
      : null;
    return {
      version: v.version,
      accepted: v.accepted === 1,
      bestMetric: best?.gate_metric ?? null,
      bestModel: best?.model_id ?? null,
    };
  });

  const data = points.map((p) => ({
    version: `v${p.version}`,
    accuracy: p.bestMetric != null ? Math.round(p.bestMetric * 1000) / 10 : null,
    accepted: p.accepted,
    model: p.bestModel ? shortModel(p.bestModel) : "",
  }));

  const gatePct = gateThreshold != null ? gateThreshold * 100 : 90;
  const allValues = data.map((d) => d.accuracy).filter((v): v is number => v != null);
  const yMin = Math.max(0, Math.floor(Math.min(...allValues, gatePct) - 5));
  const yMax = Math.min(100, Math.ceil(Math.max(...allValues, gatePct) + 5));

  const renderDot = (props: { cx?: number; cy?: number; payload?: { accepted?: boolean } }) => {
    const { cx, cy, payload: p } = props;
    if (cx == null || cy == null) return null;
    const color = p?.accepted ? "var(--good)" : "var(--text-muted)";
    return <Dot cx={cx} cy={cy} r={5} fill={color} stroke={color} strokeWidth={2} />;
  };

  return (
    <div className="vpc">
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data} margin={{ top: 20, right: 20, bottom: 5, left: -10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="version" stroke="var(--text-muted)" fontSize={13} />
          <YAxis
            domain={[yMin, yMax]}
            stroke="var(--text-muted)"
            fontSize={13}
            tickFormatter={(v) => `${v}%`}
          />
          <ReferenceLine
            y={gatePct}
            stroke="var(--accent)"
            strokeDasharray="5 5"
            label={{ value: `gate ${gatePct}%`, position: "right", fontSize: 11, fill: "var(--accent)" }}
          />
          <Tooltip
            contentStyle={{
              background: "var(--bg-elevated)",
              border: "1px solid var(--border)",
              borderRadius: "8px",
              fontSize: "0.85rem",
            }}
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={((value: any, _name: any, entry: any) => {
              const acc = entry?.payload?.accepted ? " accepted" : " (rejected)";
              const model = entry?.payload?.model ? ` · ${entry.payload.model}` : "";
              return [`${value}%${acc}${model}`, metricName];
            }) as any}
          />
          <Line
            type="monotone"
            dataKey="accuracy"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={renderDot}
            connectNulls
          >
            <LabelList dataKey="accuracy" position="top" formatter={((v: number | string) => `${v}%`) as any} fontSize={12} fill="var(--text)" />
          </Line>
        </LineChart>
      </ResponsiveContainer>
      <div className="vpc-legend">
        <span className="vpc-legend__item">
          <span className="vpc-legend__dot vpc-legend__dot--accepted" /> accepted
        </span>
        <span className="vpc-legend__item">
          <span className="vpc-legend__dot vpc-legend__dot--rejected" /> rejected/baseline
        </span>
      </div>
    </div>
  );
}
