import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { IterationLog } from "../api";

export function IterationChart({ iterations }: { iterations: IterationLog[] }) {
  if (iterations.length === 0) {
    return <p className="muted">No optimizer iterations logged yet for this field.</p>;
  }
  const data = iterations.map((it) => ({
    iteration: it.iteration_num,
    score: it.mean_score,
    accepted: it.accepted,
  }));
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#d5e2f0" />
        <XAxis dataKey="iteration" stroke="#57697c" label={{ value: "iteration", position: "insideBottom", offset: -2, fill: "#57697c" }} />
        <YAxis
          domain={[0, 1]}
          stroke="#57697c"
          label={{ value: "validation score (0-1)", angle: -90, position: "insideLeft", fill: "#57697c" }}
        />
        <Tooltip
          contentStyle={{ background: "#ffffff", border: "1px solid #d5e2f0", color: "#12283b" }}
          formatter={(value) => (typeof value === "number" ? value.toFixed(3) : value)}
        />
        <Line type="monotone" dataKey="score" name="validation score" stroke="#0067b1" strokeWidth={2} dot={{ r: 4 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
