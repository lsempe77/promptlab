import { useEffect, useRef, useState } from "react";
import {
  api,
  type Calibration,
  type CrossModelAgreement,
  type FieldInfo,
  type Job,
  type LlmJudgeSummary,
  type ModelSummary,
  type ProjectInfo,
  type RunVersion,
  type SelfConsistency,
  type StageModelGate,
  type StageStatus,
  type Thresholds,
} from "./api";
import { ModelComparisonTable } from "./components/ModelComparisonTable";
import { AggregateCharts } from "./components/AggregateCharts";
import { WorkSavedChart } from "./components/WorkSavedChart";
import { ModelCard } from "./components/ModelCard";
import { ModelFilter } from "./components/ModelFilter";
import { Methodology } from "./components/Methodology";
import { About } from "./components/About";
import { VersionProgressionTable } from "./components/VersionProgressionTable";
import { useWalkthrough } from "./components/Walkthrough";
import NewProjectWizard from "./components/NewProjectWizard";
import SupervisorStatusBar from "./components/SupervisorStatusBar";
import { LiveActivity } from "./components/LiveActivity";
import { FieldOverview } from "./components/FieldOverview";
import { ProcessSteps } from "./components/ProcessSteps";
import "./App.css";

const JOBS_POLL_MS = 6000;

function shortModel(id: string): string {
  return (id.split("/").pop() ?? id).replace(/^~/, "").replace(/-latest$/, "");
}

// Headline verdict for a field: names the leading model, its score, and how it
// stands relative to the gate — the one-line answer to "how good are we here?".
function StageBadge({ s }: { s: StageStatus }) {
  const evaluated = s.n_models_evaluated;
  const passing = s.n_models_passing;
  const gatePct = Math.round(s.gate_threshold * 100);
  const leader =
    evaluated > 0 ? s.models.reduce((a, b) => (b.gate_metric > a.gate_metric ? b : a)) : null;
  const bestPct = leader != null ? Math.round(leader.gate_metric * 100) : null;
  const isList = s.models.length > 0 && s.models[0].gate_metric_name !== "accuracy";
  const metricName = isList ? "F1" : "accuracy";
  const gap = bestPct != null ? gatePct - bestPct : null;
  const cls =
    evaluated === 0
      ? "stage-badge neutral"
      : passing === evaluated
        ? "stage-badge pass"
        : passing === 0
          ? "stage-badge gated"
          : "stage-badge partial";
  return (
    <div className={cls}>
      {evaluated > 0 && passing > 0 && leader && bestPct != null ? (
        <span>✅ <strong>{shortModel(leader.model_id)}</strong> leads at <strong>{bestPct}% {metricName}</strong> — production-ready.
        <span className="muted"> {passing} of {evaluated} AIs clear the {gatePct}% bar.</span></span>
      ) : evaluated > 0 && leader && bestPct != null ? (
        <span>Best so far: <strong>{shortModel(leader.model_id)}</strong> at <strong>{bestPct}% {metricName}</strong>
        <span className="muted"> — {gap} {gap === 1 ? "pt" : "pts"} below the {gatePct}% bar; not production-ready yet (0 of {evaluated} pass).</span></span>
      ) : (
        <span className="muted">Not yet evaluated (need {gatePct}% {metricName})</span>
      )}
      <span className="muted stage-badge-sub">· {s.references} papers checked · {s.prompt_versions} prompt versions tried ({s.prompt_versions_accepted} improved accuracy)</span>
    </div>
  );
}

function App() {
  const [tab, setTab] = useState<"dashboard" | "about">("dashboard");
  const [showWizard, setShowWizard] = useState(false);
  const [projects, setProjects] = useState<ProjectInfo[] | null>(null);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [fields, setFields] = useState<FieldInfo[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);

  const [summaries, setSummaries] = useState<ModelSummary[]>([]);
  const [llmJudge, setLlmJudge] = useState<LlmJudgeSummary[]>([]);
  const [crossAgreement, setCrossAgreement] = useState<CrossModelAgreement[]>([]);
  const [selfConsistency, setSelfConsistency] = useState<SelfConsistency[]>([]);
  const [calibration, setCalibration] = useState<Calibration[]>([]);
  const [stageStatus, setStageStatus] = useState<StageStatus | null>(null);
  const [versionData, setVersionData] = useState<{version: number; accepted: number; models: StageModelGate[]}[]>([]);
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set());
  const [loadingField, setLoadingField] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const prevRunningCount = useRef(0);

  useEffect(() => {
    api
      .projects()
      .then((p) => {
        setProjects(p);
        setApiError(null);
        if (p.length > 0) setSelectedProject(p[0].slug);
      })
      .catch((e) => setApiError(String(e)));
    api.thresholds().then(setThresholds).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    let cancelled = false;
    setFields(null);
    setSelected(null);
    api
      .fields(selectedProject)
      .then((f) => {
        if (cancelled) return;
        setFields(f);
        setApiError(null);
        if (f.length > 0) setSelected(f[0].name);
      })
      .catch((e) => { if (!cancelled) setApiError(String(e)); });
    return () => { cancelled = true; };
  }, [selectedProject]);

  // On project/field change: fetch version-independent data (self-consistency + gate status)
  // and version-dependent metrics — always using the backend default (best/latest version).
  // Also fetch all production prompt versions in parallel for the progression table.
  useEffect(() => {
    if (!selectedProject || !selected) return;
    // Guard every setState: switching field/project fast means a slow response
    // for the previous selection must not clobber the current one's data.
    let cancelled = false;
    api.selfConsistency(selectedProject, selected)
      .then((d) => { if (!cancelled) setSelfConsistency(d); }).catch(() => { if (!cancelled) setSelfConsistency([]); });
    api.stageStatus(selectedProject, selected)
      .then((d) => { if (!cancelled) setStageStatus(d); }).catch(() => { if (!cancelled) setStageStatus(null); });
    // Multi-version progression: fetch run-version list then gate metrics (real F1/accuracy) for each.
    api.runVersions(selectedProject, selected)
      .then((vs: RunVersion[]) => {
        const prod = vs.filter((v) => v.n_models >= 2).sort((a, b) => a.version - b.version);
        return Promise.all(
          prod.map((v) =>
            api.stageStatus(selectedProject, selected, v.version)
              .then((s) => ({ version: v.version, accepted: v.accepted, models: s.models }))
              .catch(() => ({ version: v.version, accepted: v.accepted, models: [] as StageModelGate[] }))
          )
        );
      })
      .then((d) => { if (!cancelled) setVersionData(d); })
      .catch(() => { if (!cancelled) setVersionData([]); });
    setLoadingField(true);
    prevRunningCount.current = 0;
    api
      .modelsSummary(selectedProject, selected)
      .then((s) => {
        if (cancelled) return;
        setSummaries(s);
        setSelectedModels(new Set(s.map((m) => m.model_id)));
        setApiError(null);
      })
      .catch((e) => { if (!cancelled) setApiError(String(e)); })
      .finally(() => { if (!cancelled) setLoadingField(false); });
    api.llmJudgeSummary(selectedProject, selected)
      .then((d) => { if (!cancelled) setLlmJudge(d); }).catch(() => { if (!cancelled) setLlmJudge([]); });
    api.crossModelAgreement(selectedProject, selected)
      .then((d) => { if (!cancelled) setCrossAgreement(d); }).catch(() => { if (!cancelled) setCrossAgreement([]); });
    api.calibration(selectedProject, selected)
      .then((d) => { if (!cancelled) setCalibration(d); }).catch(() => { if (!cancelled) setCalibration([]); });
    return () => { cancelled = true; };
  }, [selectedProject, selected]);

  // Poll for running extraction/optimization jobs so the dashboard can show a
  // "currently running" indicator even though the backend has no push/websocket
  // mechanism — this is the only way to notice a job started after page load.
  // When a job's running count drops back to zero (it just finished), also
  // re-fetch the model summary so newly-logged runs show up without a reload.
  useEffect(() => {
    if (!selectedProject || !selected) return;
    let cancelled = false;
    const poll = () => {
      api
        .jobs(selectedProject, selected)
        .then((j) => {
          if (cancelled) return;
          setJobs(j);
          const runningCount = j.filter((job) => job.status === "running" && !job.stale).length;
          if (runningCount === 0 && prevRunningCount.current > 0) {
            api.modelsSummary(selectedProject, selected).then(setSummaries).catch(() => {});
          }
          prevRunningCount.current = runningCount;
        })
        .catch(() => {});
    };
    poll();
    const id = window.setInterval(poll, JOBS_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [selectedProject, selected]);

  const activeField = fields?.find((f) => f.name === selected) ?? null;
  const runningJobs = jobs.filter((j) => j.status === "running" && !j.stale);
  const { start: startWalkthrough } = useWalkthrough();

  function toggleModel(modelId: string) {
    setSelectedModels((prev) => {
      const next = new Set(prev);
      if (next.has(modelId)) next.delete(modelId);
      else next.add(modelId);
      return next;
    });
  }

  return (
    <div className="dashboard">
      <header id="tour-header" className="dashboard-header">
        <h1>Agentic 3ie Prompt Lab</h1>
        <p className="muted">
          We test different AI models and prompts on evidence-synthesis tasks — like screening
          studies for inclusion/exclusion and pulling structured details (authors, institutions,
          sectors) out of research papers — to see which combinations work best.
        </p>
        {projects && projects.length > 0 && (
          <div id="tour-project-switcher" className="project-switcher">
            <label htmlFor="project-select">Project</label>
            <select
              id="project-select"
              value={selectedProject ?? ""}
              onChange={(e) => setSelectedProject(e.target.value)}
            >
              {projects.map((p) => (
                <option key={p.slug} value={p.slug} title={p.description}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <nav id="tour-tab-nav" className="tab-nav">
          <button className={tab === "dashboard" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("dashboard")}>
            Dashboard
          </button>
          <button className={tab === "about" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("about")}>
            How it works
          </button>
          <button className="tab-btn tour-btn" onClick={startWalkthrough} title="Start guided walkthrough">
            Tour
          </button>
          <button className="tab-btn new-project-btn" onClick={() => setShowWizard(true)} title="Create a new prompt lab">
            + New Project
          </button>
        </nav>
      </header>

      {tab === "about" ? (
        <About />
      ) : (
        <>
          {apiError && (
            <div className="api-error">
              <strong>Can't reach the API</strong> ({apiError}). Is the backend running locally? Start it with{" "}
              <code>python -m backend.scripts.serve</code> from the DEP project, then reload this page.
            </div>
          )}

          {!apiError && !fields && <p className="muted">Loading fields…</p>}

          {/* How it works — 3 plain steps up top; full pipeline is opt-in below. */}
          {!apiError && <ProcessSteps />}

          {/* Cross-field overview is the entry point: pick a field, then dive in. */}
          {!apiError && selectedProject && fields && fields.length > 0 && (
            <FieldOverview
              project={selectedProject}
              fields={fields}
              selectedField={selected}
              onSelectField={setSelected}
            />
          )}

          {/* Selected-field deep dive: verdict → charts → model cards. */}
          {fields && fields.length > 0 && (
            <div className="dashboard-body">
              <nav id="tour-field-nav" className="field-nav">
                {fields.map((f) => (
                  <button
                    key={f.name}
                    className={f.name === selected ? "field-btn active" : "field-btn"}
                    onClick={() => setSelected(f.name)}
                  >
                    {f.label}
                  </button>
                ))}
              </nav>

              <main className="field-detail">
                {activeField && (
                  <>
                    <section className="panel">
                      <h2>{activeField.label}</h2>
                      <p className="muted">{activeField.description}</p>
                      {stageStatus && <div id="tour-stage-badge"><StageBadge s={stageStatus} /></div>}
                    </section>

                    {runningJobs.length > 0 && (
                      <div className="job-banner">
                        <span className="job-spinner" aria-hidden="true" />
                        <span>
                          {runningJobs.length === 1
                            ? "1 model is"
                            : `${runningJobs.length} models are`}{" "}
                          currently running for this field:{" "}
                          {runningJobs
                            .map(
                              (j) =>
                                `${j.model_id} (${j.kind}${
                                  j.total ? `, ${j.completed}/${j.total}` : ""
                                })`,
                            )
                            .join(", ")}
                          . The dashboard will update automatically once it finishes.
                        </span>
                      </div>
                    )}

                    {loadingField ? (
                      <p className="muted">Loading…</p>
                    ) : (
                      <>
                        {/* Charts first — answer "which AI is best?" before the history */}
                        <section className="panel panel-aggregate">
                          <h3>Which AI performs best on this task?</h3>
                          <p className="muted panel-caption">
                            Bar chart shows each AI’s accuracy ({activeField.value_type === "single_categorical" ? "accuracy" : "F1 score"}).
                            Green ≥ {stageStatus ? Math.round(stageStatus.gate_threshold * 100) : 90}% = accurate enough to use.
                            Scatter shows accuracy vs. cost per 1,000 papers.
                          </p>
                          <AggregateCharts
                            summaries={summaries}
                            stageModels={stageStatus?.models ?? []}
                            valueType={activeField.value_type}
                            gateThreshold={stageStatus?.gate_threshold ?? null}
                          />
                          {calibration.length > 0 && (
                            <WorkSavedChart
                              calibrations={calibration}
                              gateThreshold={stageStatus?.gate_threshold ?? null}
                            />
                          )}
                          <details className="details-table">
                            <summary>Full metrics table (all {summaries.length} AIs, all columns)</summary>
                            <p className="muted panel-caption">
                              {activeField.value_type === "single_categorical"
                                ? "Accuracy = fraction of papers where the AI picked the right category. Cohen’s κ = accuracy corrected for chance. "
                                : "F1 = balance of precision (no wrong extras) and recall (no missed values). "}
                              <em>Second-opinion check</em> = a different AI family independently validates the answer.
                              Click any header to sort.
                            </p>
                            <div id="tour-model-table">
                            <ModelComparisonTable
                              summaries={summaries}
                              stageModels={stageStatus?.models ?? []}
                              valueType={activeField.value_type}
                            />
                            </div>
                          </details>
                        </section>

                        {/* Version history — backstory, shown after the key result */}
                        <VersionProgressionTable
                          versionData={versionData}
                          gateThreshold={stageStatus?.gate_threshold ?? null}
                          valueType={activeField.value_type}
                        />

                        {summaries.length === 0 ? (
                          <p className="muted">No references processed yet for this field.</p>
                        ) : (
                          <>
                            <div id="tour-model-filter">
                            <ModelFilter
                              models={summaries.map((s) => s.model_id)}
                              selected={selectedModels}
                              onToggle={toggleModel}
                              onSelectAll={() => setSelectedModels(new Set(summaries.map((s) => s.model_id)))}
                              onSelectNone={() => setSelectedModels(new Set())}
                            />
                            </div>
                            {summaries
                              .filter((s) => selectedModels.has(s.model_id))
                              .map((s) => (
                                <ModelCard
                                  key={s.model_id}
                                  projectSlug={selectedProject!}
                                  fieldName={selected!}
                                  summary={s}
                                  jobs={jobs.filter((j) => j.model_id === s.model_id)}
                                  llmJudge={llmJudge.find((j) => j.model_id === s.model_id) ?? null}
                                  crossAgreement={crossAgreement.find((c) => c.model_id === s.model_id) ?? null}
                                  selfConsistency={selfConsistency.find((c) => c.model_id === s.model_id) ?? null}
                                  calibration={calibration.find((c) => c.model_id === s.model_id) ?? null}
                                  gateThreshold={stageStatus?.gate_threshold ?? null}
                                />
                              ))}
                          </>
                        )}
                      </>
                    )}
                  </>
                )}
              </main>
            </div>
          )}

          {/* Operational status + deep methodology, below the results */}
          {!apiError && selectedProject && (
            <SupervisorStatusBar project={selectedProject} />
          )}

          {!apiError && <LiveActivity />}

          <div id="tour-methodology"><Methodology thresholds={thresholds} /></div>
        </>
      )}

      {showWizard && (
        <NewProjectWizard
          onClose={() => setShowWizard(false)}
          onProjectCreated={(slug) => {
            setShowWizard(false);
            setSelectedProject(slug);
          }}
        />
      )}
    </div>
  );
}

export default App;
