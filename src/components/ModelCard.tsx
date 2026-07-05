import { useEffect, useRef, useState } from "react";
import {
  api,
  type Calibration,
  type Confusion,
  type CrossModelAgreement,
  type IterationLog,
  type Job,
  type LlmJudgeSummary,
  type ModelSummary,
  type SelfConsistency,
} from "../api";
import { IterationChart } from "./IterationChart";
import { ConfusionMatrix } from "./ConfusionMatrix";

function pct(x: number) {
  return `${(x * 100).toFixed(1)}%`;
}

// 95% Wilson score interval for a proportion (accuracy = correct / n). Better
// behaved than the plain normal (Wald) interval for small n and accuracies
// near 0 or 1. The interval narrows ~1/√n as the sample grows (central limit
// theorem) — the whole point of the staged 30 → 60 → 100 rollout.
function wilson95(p: number, n: number): { low: number; high: number } | null {
  if (!n || n <= 0) return null;
  const z = 1.96;
  const z2 = z * z;
  const denom = 1 + z2 / n;
  const center = (p + z2 / (2 * n)) / denom;
  const half = (z / denom) * Math.sqrt((p * (1 - p)) / n + z2 / (4 * n * n));
  return { low: Math.max(0, center - half), high: Math.min(1, center + half) };
}

// Compact whisker: a 0–100% track, the 95% CI as a band, and the point estimate
// as a vertical tick. Visually shrinks as n grows.
function ConfidenceWhisker({ accuracy, ci }: { accuracy: number; ci: { low: number; high: number } }) {
  const W = 132;
  const H = 12;
  const pad = 3;
  const x = (v: number) => pad + v * (W - 2 * pad);
  return (
    <svg
      className="ci-whisker"
      width={W}
      height={H}
      role="img"
      aria-label={`95% confidence interval ${(ci.low * 100).toFixed(0)}% to ${(ci.high * 100).toFixed(0)}%`}
    >
      <line x1={pad} y1={H / 2} x2={W - pad} y2={H / 2} stroke="currentColor" strokeOpacity={0.2} />
      <rect
        x={x(ci.low)}
        y={H / 2 - 3}
        width={Math.max(1, x(ci.high) - x(ci.low))}
        height={6}
        rx={3}
        fill="#0067b1"
        fillOpacity={0.35}
      />
      <line x1={x(accuracy)} y1={1} x2={x(accuracy)} y2={H - 1} stroke="#0067b1" strokeWidth={2} />
    </svg>
  );
}

// Reliability diagram: stated confidence (x) vs. observed accuracy (y) per
// confidence bin. The dashed diagonal is perfect calibration; points below it
// = overconfident, above = underconfident.
function ReliabilityDiagram({ calibration }: { calibration: Calibration }) {
  const W = 360;
  const H = 300;
  const pad = 50;
  const x = (v: number) => pad + v * (W - 2 * pad);
  const y = (v: number) => H - pad - v * (H - 2 * pad);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const pts = calibration.bins.filter(
    (b) => b.n > 0 && b.mean_confidence != null && b.accuracy != null,
  );
  return (
    <svg className="reliability" viewBox={`0 0 ${W} ${H}`} role="img"
         aria-label="Reliability diagram: stated confidence versus observed accuracy">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={x(t)} y1={y(0)} x2={x(t)} y2={y(1)} stroke="currentColor" strokeOpacity={0.08} />
          <line x1={x(0)} y1={y(t)} x2={x(1)} y2={y(t)} stroke="currentColor" strokeOpacity={0.08} />
          <text x={x(t)} y={y(0) + 15} textAnchor="middle" fontSize={9} fill="currentColor" opacity={0.6}>
            {t * 100}
          </text>
          <text x={x(0) - 8} y={y(t) + 3} textAnchor="end" fontSize={9} fill="currentColor" opacity={0.6}>
            {t * 100}
          </text>
        </g>
      ))}
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(0)} stroke="currentColor" strokeOpacity={0.35} />
      <line x1={x(0)} y1={y(0)} x2={x(0)} y2={y(1)} stroke="currentColor" strokeOpacity={0.35} />
      <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#8a9bb0" strokeOpacity={0.75} strokeDasharray="4 3" />
      <text x={x(0.62)} y={y(0.30)} fontSize={9} fill="currentColor" opacity={0.35}>overconfident</text>
      <text x={x(0.04)} y={y(0.9)} fontSize={9} fill="currentColor" opacity={0.35}>underconfident</text>
      {pts.map((b, i) => (
        <g key={i}>
          <circle cx={x(b.mean_confidence!)} cy={y(b.accuracy!)}
                  r={Math.min(7, 3 + Math.sqrt(b.n))} fill="#0067b1" fillOpacity={0.75}>
            <title>
              bucket {i + 1}: stated confidence {(b.mean_confidence! * 100).toFixed(0)}% →
              actual accuracy {(b.accuracy! * 100).toFixed(0)}% (n={b.n})
            </title>
          </circle>
          <text x={x(b.mean_confidence!)} y={y(b.accuracy!) - 9} textAnchor="middle"
                fontSize={8.5} fill="#0067b1" opacity={0.9}>
            n={b.n}
          </text>
        </g>
      ))}
      <text x={(x(0) + x(1)) / 2} y={H - 6} textAnchor="middle" fontSize={10} fill="currentColor" opacity={0.7}>
        stated confidence (%)
      </text>
      <text x={13} y={(y(0) + y(1)) / 2} textAnchor="middle" fontSize={10} fill="currentColor" opacity={0.7}
            transform={`rotate(-90 13 ${(y(0) + y(1)) / 2})`}>
        actual accuracy (%)
      </text>
      <text x={x(1)} y={y(1) - 5} textAnchor="end" fontSize={9} fill="#8a9bb0">
        perfect calibration
      </text>
    </svg>
  );
}

export function ModelCard({
  projectSlug,
  fieldName,
  summary,
  jobs = [],
  llmJudge = null,
  crossAgreement = null,
  selfConsistency = null,
  calibration = null,
  gateThreshold = null,
}: {
  projectSlug: string;
  fieldName: string;
  summary: ModelSummary;
  jobs?: Job[];
  llmJudge?: LlmJudgeSummary | null;
  crossAgreement?: CrossModelAgreement | null;
  selfConsistency?: SelfConsistency | null;
  calibration?: Calibration | null;
  gateThreshold?: number | null;
}) {
  const [iters, setIters] = useState<IterationLog[] | null>(null);
  const [confusion, setConfusion] = useState<Confusion | null>(null);
  const runningJobs = jobs.filter((j) => j.status === "running" && !j.stale);
  // Re-fetch this model's own iteration/confusion data once its running job
  // count drops back to zero, so a finished run shows up without a reload.
  const wasRunning = useRef(false);

  useEffect(() => {
    const isRunningNow = runningJobs.length > 0;
    const justFinished = wasRunning.current && !isRunningNow;
    wasRunning.current = isRunningNow;
    if (iters !== null && !justFinished) return;
    setIters(null);
    setConfusion(null);
    Promise.all([
      api.iterations(projectSlug, fieldName, summary.model_id),
      api.confusion(projectSlug, fieldName, summary.model_id),
    ])
      .then(([it, c]) => {
        setIters(it);
        setConfusion(c);
      })
      .catch(() => {
        setIters([]);
        setConfusion(null);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectSlug, fieldName, summary.model_id, runningJobs.length]);

  const accepted = iters?.filter((i) => i.accepted).length ?? 0;
  const rejected = (iters?.length ?? 0) - accepted;
  const accCi = wilson95(summary.accuracy, summary.n);

  return (
    <section className="panel model-card">
      <div className="model-card-header">
        <h4>{summary.model_id}</h4>
        {runningJobs.map((j) => (
          <span key={j.id} className="badge badge-running">
            <span className="job-spinner" aria-hidden="true" />
            {j.kind} running{j.total ? ` (${j.completed}/${j.total})` : ""}
          </span>
        ))}
        <div className="stat-section-label">Accuracy</div>
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-value">{summary.n}</span>
            <span className="stat-label">references</span>
          </div>
          <div className="stat-card highlight">
            <span className="stat-value">{pct(summary.accuracy)}</span>
            <span className="stat-label">threshold accuracy</span>
            {accCi && (
              <>
                <ConfidenceWhisker accuracy={summary.accuracy} ci={accCi} />
                <span className="stat-ci">
                  95% CI {pct(accCi.low)}–{pct(accCi.high)}
                </span>
              </>
            )}
          </div>
          <div className="stat-card">
            <span className="stat-value">{summary.n_errors}</span>
            <span className="stat-label">errors</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">
              {llmJudge && llmJudge.n_judged > 0 ? pct(llmJudge.llm_judged_accuracy) : "—"}
            </span>
            <span className="stat-label">
              llm-judged accuracy{llmJudge && llmJudge.n_judged > 0 ? ` (${llmJudge.n_judged})` : ""}
            </span>
            {llmJudge && llmJudge.n_judged > 0 && gateThreshold != null && (
              <span
                className={`gate-chip ${
                  llmJudge.llm_judged_accuracy >= gateThreshold ? "pass" : "gated"
                }`}
              >
                {llmJudge.llm_judged_accuracy >= gateThreshold
                  ? `✓ gate ≥${Math.round(gateThreshold * 100)}%`
                  : `✗ gated (<${Math.round(gateThreshold * 100)}%)`}
              </span>
            )}
          </div>
        </div>
        <div className="stat-section-label">Honesty</div>
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-value">
              {summary.mean_honesty_score != null ? summary.mean_honesty_score.toFixed(2) : "—"}
            </span>
            <span className="stat-label">honesty-adjusted score</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{pct(summary.abstention_rate)}</span>
            <span className="stat-label">abstention rate</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{pct(summary.hallucination_rate)}</span>
            <span className="stat-label">hallucination rate</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{pct(summary.wrong_rate)}</span>
            <span className="stat-label">wrong rate</span>
          </div>
        </div>
        <div className="stat-section-label">Confidence signals</div>
        <div className="stat-grid">
          <div className="stat-card">
            <span className="stat-value">
              {summary.mean_logprob_confidence != null ? pct(summary.mean_logprob_confidence) : "—"}
            </span>
            <span className="stat-label">avg token confidence</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{crossAgreement ? pct(crossAgreement.agreement_rate) : "—"}</span>
            <span className="stat-label">
              cross-model agreement{crossAgreement ? ` (${crossAgreement.n_records})` : ""}
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{selfConsistency ? pct(selfConsistency.mean_agreement) : "—"}</span>
            <span className="stat-label">
              self-consistency{selfConsistency ? ` (${selfConsistency.n_records})` : ""}
            </span>
          </div>
          <div className="stat-card">
            <span className="stat-value">
              {summary.excerpt_verified_rate != null ? pct(summary.excerpt_verified_rate) : "—"}
            </span>
            <span className="stat-label">excerpt verified</span>
          </div>
        </div>
      </div>

      {calibration && calibration.n_scored > 0 && (
        <div className="model-card-section">
          <h5>Confidence calibration</h5>
          <p className="muted panel-caption">
            Brier score {calibration.brier.toFixed(3)}{" "}
            <span className="muted">
              (0 = perfect · ≤0.10 well-calibrated · ~0.25 ≈ no better than guessing; lower is better)
            </span>{" "}
            &middot; avg stated confidence {pct(calibration.mean_confidence)} vs{" "}
            {pct(calibration.accuracy)} actual accuracy &middot; n={calibration.n_scored}
          </p>
          <ReliabilityDiagram calibration={calibration} />
          <p className="muted panel-caption">
            Each point is a confidence bucket (left → right = lower → higher stated confidence); the
            label <em>n</em> is how many predictions fall in it, so small-<em>n</em> points (e.g. a
            lone prediction sitting at 0% accuracy) are noise, not a trend. A point below the diagonal
            means the model was overconfident there; above means underconfident.
          </p>
        </div>
      )}

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
