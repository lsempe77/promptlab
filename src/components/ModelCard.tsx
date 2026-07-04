import { useEffect, useState } from "react";
import { api, type Confusion, type IterationLog, type ModelSummary } from "../api";
import { IterationChart } from "./IterationChart";
import { ConfusionMatrix } from "./ConfusionMatrix";

function pct(x: number) {
  return `${(x * 100).toFixed(1)}%`;
}

export function ModelCard({ fieldName, summary }: { fieldName: string; summary: ModelSummary }) {
  const [iters, setIters] = useState<IterationLog[] | null>(null);
  const [confusion, setConfusion] = useState<Confusion | null>(null);

  useEffect(() => {
    setIters(null);
    setConfusion(null);
    Promise.all([
      api.iterations(fieldName, summary.model_id),
      api.confusion(fieldName, summary.model_id),
    ])
      .then(([it, c]) => {
        setIters(it);
        setConfusion(c);
      })
      .catch(() => {
        setIters([]);
        setConfusion(null);
      });
  }, [fieldName, summary.model_id]);

  const accepted = iters?.filter((i) => i.accepted).length ?? 0;
  const rejected = (iters?.length ?? 0) - accepted;

  return (
    <section className="panel model-card">
      <div className="model-card-header">
        <h4>{summary.model_id}</h4>
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-value">{summary.n}</span>
            <span className="stat-label">runs</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{summary.mean_score.toFixed(3)}</span>
            <span className="stat-label">mean score</span>
          </div>
          <div className="stat-card highlight">
            <span className="stat-value">{pct(summary.accuracy)}</span>
            <span className="stat-label">accuracy</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{summary.n_errors}</span>
            <span className="stat-label">errors</span>
          </div>
        </div>
      </div>

      <div className="model-card-section">
        <h5>Prompt optimization progress</h5>
        {iters === null ? (
          <p className="muted">Loading…</p>
        ) : iters.length === 0 ? (
          <p className="muted">This model has not been run through the optimizer for this field yet.</p>
        ) : (
          <>
            <p className="muted panel-caption">
              {iters.length} candidate{iters.length === 1 ? "" : "s"} tried &middot; {accepted} accepted &middot;{" "}
              {rejected} rejected
            </p>
            <IterationChart iterations={iters} />
          </>
        )}
      </div>

      <div className="model-card-section">
        <h5>Prompt versions &amp; failure analysis</h5>
        {iters === null ? (
          <p className="muted">Loading…</p>
        ) : iters.length === 0 ? (
          <p className="muted">No candidate prompts logged yet for this model.</p>
        ) : (
          <ol className="lineage">
            {iters.map((it) => (
              <li key={it.id} className={it.accepted ? "lineage-item accepted" : "lineage-item rejected"}>
                <div className="lineage-header">
                  <span className="lineage-version">
                    iter {it.iteration_num} → v{it.prompt_version}
                  </span>
                  {it.accepted ? (
                    <span className="badge badge-accepted">accepted</span>
                  ) : (
                    <span className="badge badge-rejected">rejected</span>
                  )}
                  <span className="muted">val score {it.mean_score.toFixed(3)}</span>
                  <span className="muted lineage-date">{new Date(it.created_at).toLocaleString()}</span>
                </div>
                <p className="lineage-template">{it.prompt_template}</p>
                {it.feedback && (
                  <p className="lineage-notes">
                    <strong>Failure analysis / reflector diagnosis:</strong> {it.feedback}
                  </p>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="model-card-section">
        <h5>Confusion matrix / F-scores</h5>
        <ConfusionMatrix confusion={confusion} />
      </div>
    </section>
  );
}
