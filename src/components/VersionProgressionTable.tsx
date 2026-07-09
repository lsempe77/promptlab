import type { StageModelGate } from "../api";

interface VersionData {
  version: number;
  accepted: number;
  models: StageModelGate[];
  n_runs?: number;
}

interface Props {
  versionData: VersionData[];
  gateThreshold: number | null;
  valueType: string;
}

function pct(x: number | null) {
  if (x == null) return "\u2014";
  return `${(x * 100).toFixed(1)}%`;
}

function cellClass(score: number | null, threshold: number): string {
  if (score == null) return "vprog-cell-na";
  if (score >= threshold) return "vprog-cell-pass";
  if (score >= threshold * 0.78) return "vprog-cell-close";
  return "vprog-cell-fail";
}

export function VersionProgressionTable({ versionData, gateThreshold, valueType }: Props) {
  if (versionData.length < 2) return null;

  const threshold = gateThreshold ?? 0.9;
  const isList = valueType !== "single_categorical";
  const metricLabel = isList ? "Element-level F1" : "Accuracy";
  const versions = versionData.map((v) => v.version);

  // Build lookup: version → modelId → {gate_metric, n}
  const lookup = new Map<number, Map<string, { score: number; n: number }>>();
  const modelSet = new Set<string>();
  for (const vd of versionData) {
    const m = new Map<string, { score: number; n: number }>();
    for (const g of vd.models) {
      m.set(g.model_id, { score: g.gate_metric, n: g.n ?? 0 });
      modelSet.add(g.model_id);
    }
    lookup.set(vd.version, m);
  }

  const getScore = (modelId: string, version: number): number | null =>
    lookup.get(version)?.get(modelId)?.score ?? null;
  const getN = (modelId: string, version: number): number | null =>
    lookup.get(version)?.get(modelId)?.n ?? null;

  const latestV = versions[versions.length - 1];
  const firstV = versions[0];

  const models = [...modelSet].sort((a, b) => {
    const sa = getScore(a, latestV) ?? getScore(a, firstV) ?? 0;
    const sb = getScore(b, latestV) ?? getScore(b, firstV) ?? 0;
    return sb - sa;
  });

  return (
    <section className="panel panel-vprog">
      <h3>{metricLabel} across prompt versions</h3>
      <p className="muted panel-caption">
        {metricLabel} per model for each production prompt version — the actual gate metric
        (same as the main table below). Green ≥ {Math.round(threshold * 100)}% gate · ★ = accepted version.
        Δ = change from v{firstV} → v{latestV}.        <strong> Note:</strong> small Δ values (&lt;2%) often reflect run-count differences between versions
        rather than real prompt changes — a version with 300 runs is more stable than one with 100.      </p>
      <div className="table-scroll">
        <table className="comparison-table vprog-table">
          <thead>
            <tr>
              <th className="col-model">Model</th>
              {versionData.map((vd) => (
                <th key={vd.version} className="col-version">
                  v{vd.version}
                  {vd.accepted ? <span className="vprog-star" title="accepted version"> ★</span> : null}
                </th>
              ))}
              <th className="col-delta">Δ</th>
            </tr>
          </thead>
          <tbody>
            {models.map((modelId) => {
              const first = getScore(modelId, firstV);
              const last = getScore(modelId, latestV);
              const delta = first != null && last != null ? last - first : null;
              return (
                <tr key={modelId}>
                  <td className="model-id">{modelId}</td>
                  {versions.map((v) => {
                    const score = getScore(modelId, v);
                    const n = getN(modelId, v);
                    return (
                      <td key={v} className={`numeric ${cellClass(score, threshold)}`}
                          title={n != null ? `n=${n} runs` : undefined}>
                        {pct(score)}
                        {score != null && n != null && <span className="vprog-n"> {n}</span>}
                      </td>
                    );
                  })}
                  <td
                    className={`numeric vprog-delta ${
                      delta == null ? "" : delta > 0.005 ? "vprog-delta-up" : delta < -0.005 ? "vprog-delta-down" : ""
                    }`}
                  >
                    {delta != null
                      ? `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}%`
                      : "\u2014"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
