/**
 * Work Saved curve: for each possible accuracy target, shows what fraction
 * of extractions could be auto-accepted (no human review needed) if we only
 * flag papers where the model's confidence falls below a threshold.
 *
 * Computed from the per-bin calibration data: bins are sorted by confidence
 * (high → low); cumulative inclusion builds the curve from
 * "only the most confident" → "accept everything".
 */
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
  Legend,
} from "recharts";
import type { Calibration } from "../api";

const COLORS = ["#2e7d4f", "#1976d2", "#7b1fa2", "#c62828", "#e65100", "#00796b"];

interface CurvePoint {
  accuracy: number;   // 0-100, gate metric on auto-accepted papers
  workSaved: number;  // 0-100, % of papers auto-accepted
}

function computeCurve(cal: Calibration): CurvePoint[] {
  const valid = cal.bins.filter(
    (b) => b.n > 0 && b.accuracy != null && b.mean_confidence != null
  );
  if (valid.length === 0) return [];
  const totalN = valid.reduce((s, b) => s + b.n, 0);
  if (totalN === 0) return [];

  // Sort bins high-confidence first; sweep down, accumulating papers
  const sorted = [...valid].sort(
    (a, b) => (b.mean_confidence ?? 0) - (a.mean_confidence ?? 0)
  );

  const pts: CurvePoint[] = [];
  let cumN = 0;
  let cumCorrect = 0;
  for (const bin of sorted) {
    cumN += bin.n;
    cumCorrect += (bin.accuracy ?? 0) * bin.n;
    pts.push({
      accuracy: Math.round((cumCorrect / cumN) * 1000) / 10,
      workSaved: Math.round((cumN / totalN) * 1000) / 10,
    });
  }
  // Always include the 100% point (accept everything)
  if (pts[pts.length - 1]?.workSaved !== 100) {
    const allAcc = valid.reduce((s, b) => s + (b.accuracy ?? 0) * b.n, 0) / totalN;
    pts.push({ accuracy: Math.round(allAcc * 1000) / 10, workSaved: 100 });
  }
  return pts.sort((a, b) => a.accuracy - b.accuracy);
}

/** Interpolate work-saved % at a given accuracy target. */
function workSavedAtAccuracy(curve: CurvePoint[], targetAcc: number): number | null {
  if (curve.length === 0) return null;
  for (let i = 0; i < curve.length - 1; i++) {
    const a = curve[i], b = curve[i + 1];
    if (a.accuracy <= targetAcc && targetAcc <= b.accuracy) {
      const t = (targetAcc - a.accuracy) / (b.accuracy - a.accuracy || 1);
      return Math.round(a.workSaved + t * (b.workSaved - a.workSaved));
    }
  }
  if (targetAcc <= curve[0].accuracy) return curve[0].workSaved;
  if (targetAcc >= curve[curve.length - 1].accuracy) return curve[curve.length - 1].workSaved;
  return null;
}

function short(id: string) {
  return (id.split("/").pop() ?? id).replace(/^~/, "").replace(/-latest$/, "");
}

export function WorkSavedChart({
  calibrations,
  gateThreshold,
}: {
  calibrations: Calibration[];
  gateThreshold: number | null;
}) {
  const gatePct = gateThreshold != null ? gateThreshold * 100 : 90;

  // Take up to 5 models with enough data
  const usable = calibrations
    .filter((c) => c.n_scored >= 20 && c.bins.some((b) => b.n > 0))
    .slice(0, 5);
  if (usable.length === 0) return null;

  // Build one curve per model and combine into unified x-ticks
  const curves = usable.map((cal) => ({
    id: cal.model_id,
    label: short(cal.model_id),
    pts: computeCurve(cal),
  }));

  // Unified x-axis: accuracy values 50–100 in 1% steps
  const xTicks = Array.from({ length: 51 }, (_, i) => 50 + i);
  const chartData = xTicks.map((acc) => {
    const row: Record<string, number | null> = { accuracy: acc };
    for (const c of curves) {
      row[c.label] = workSavedAtAccuracy(c.pts, acc);
    }
    return row;
  });

  // Summary callout: at gatePct% accuracy, how much work is saved?
  const summaries = curves.map((c) => ({
    label: c.label,
    ws95: workSavedAtAccuracy(c.pts, gatePct),
  })).filter((s) => s.ws95 != null);

  return (
    <div className="work-saved-wrap">
      <h4 className="work-saved-title">
        How much manual review can AI skip?
      </h4>
      <p className="muted work-saved-caption">
        If the AI flags low-confidence answers for human review and auto-accepts
        the rest, this curve shows the tradeoff: higher accuracy bar → fewer
        papers auto-accepted, more human effort required.
        The dashed line marks our {gatePct}% accuracy target.
      </p>

      {summaries.length > 0 && (
        <div className="work-saved-callout">
          At <strong>{gatePct}% accuracy</strong>, the best AI could auto-accept{" "}
          <strong>{Math.max(...summaries.map((s) => s.ws95 ?? 0))}%</strong>{" "}
          of extractions without human review.
        </div>
      )}

      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" strokeOpacity={0.3} />
          <XAxis
            dataKey="accuracy"
            type="number"
            domain={[50, 100]}
            tickFormatter={(v) => `${v}%`}
            label={{ value: "Accuracy on auto-accepted papers", position: "insideBottom", offset: -4, fontSize: 11 }}
          />
          <YAxis
            tickFormatter={(v) => `${v}%`}
            domain={[0, 100]}
            label={{ value: "Papers auto-accepted", angle: -90, position: "insideLeft", offset: 12, fontSize: 11 }}
          />
          <Tooltip
            formatter={(v: unknown) => [`${v}%`]}
            labelFormatter={(l: unknown) => `Accuracy target: ${l}%`}
          />
          <ReferenceLine
            x={gatePct}
            stroke="#888"
            strokeDasharray="4 4"
            label={{ value: `${gatePct}% gate`, position: "top", fontSize: 10, fill: "#888" }}
          />
          {curves.length > 1 && <Legend verticalAlign="top" height={24} />}
          {curves.map((c, i) => (
            <Line
              key={c.id}
              type="monotone"
              dataKey={c.label}
              stroke={COLORS[i % COLORS.length]}
              dot={false}
              strokeWidth={2}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
