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
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -16 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#333" />
        <XAxis dataKey="iteration" stroke="#999" label={{ value: "iteration", position: "insideBottom", offset: -2, fill: "#999" }} />
        <YAxis domain={[0, 1]} stroke="#999" />
        <Tooltip
          contentStyle={{ background: "#1e1e1e", border: "1px solid #444" }}
          formatter={(value) => (typeof value === "number" ? value.toFixed(3) : value)}
        />
        <Line type="monotone" dataKey="score" stroke="#61dafb" strokeWidth={2} dot={{ r: 4 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
