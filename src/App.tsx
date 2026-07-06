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
  type SelfConsistency,
  type StageStatus,
  type RunVersion,
  type Thresholds,
} from "./api";
import { ModelComparisonTable } from "./components/ModelComparisonTable";
import { ModelCard } from "./components/ModelCard";
import { ModelFilter } from "./components/ModelFilter";
import { Methodology } from "./components/Methodology";
import { About } from "./components/About";
import "./App.css";

const JOBS_POLL_MS = 6000;

function StageBadge({ s }: { s: StageStatus }) {
  const evaluated = s.n_models_evaluated;
  const passing = s.n_models_passing;
  const gatePct = Math.round(s.gate_threshold * 100);
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
      <span className="stage-pill">Stage {s.references}/{s.final_stage}</span>
      {evaluated > 0 ? (
        <span>
          {passing}/{evaluated} models pass gate{" "}
          <span className="muted">(≥{gatePct}% quality metric — F1 for lists, accuracy for categorical, per model)</span>
        </span>
      ) : (
        <span className="muted">gate (≥{gatePct}%): not yet judged</span>
      )}
      <span className="muted">
        · {s.prompt_versions} prompt version{s.prompt_versions === 1 ? "" : "s"}{" "}
        ({s.prompt_versions_accepted} accepted)
      </span>
    </div>
  );
}

function App() {
  const [tab, setTab] = useState<"dashboard" | "about">("dashboard");
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
  const [runVersionList, setRunVersionList] = useState<RunVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
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
    setFields(null);
    setSelected(null);
    api
      .fields(selectedProject)
      .then((f) => {
        setFields(f);
        setApiError(null);
        if (f.length > 0) setSelected(f[0].name);
      })
      .catch((e) => setApiError(String(e)));
  }, [selectedProject]);

  // On project/field change: reset the version selector, load the versions that
  // have runs (default to the latest = the current "best"), and fetch the
  // version-independent data (self-consistency study + rollout/gate status).
  useEffect(() => {
    if (!selectedProject || !selected) return;
    setSelectedVersion(null);
    api
      .runVersions(selectedProject, selected)
      .then((vs) => {
        setRunVersionList(vs);
        // Default to the version with the most runs (the real production
        // dataset) rather than the highest version number, which may be a thin
        // optimizer-trial version with only a handful of validation runs.
        const best = vs.reduce<RunVersion | null>((a, b) => (a && a.n_runs >= b.n_runs ? a : b), null);
        setSelectedVersion(best ? best.version : null);
      })
      .catch(() => {
        setRunVersionList([]);
        setSelectedVersion(null);
      });
    api.selfConsistency(selectedProject, selected).then(setSelfConsistency).catch(() => setSelfConsistency([]));
    api.stageStatus(selectedProject, selected).then(setStageStatus).catch(() => setStageStatus(null));
  }, [selectedProject, selected]);

  // On project/field/version change: fetch the version-dependent metrics for the
  // selected prompt version (undefined => backend defaults to the latest/best).
  useEffect(() => {
    if (!selectedProject || !selected) return;
    const v = selectedVersion ?? undefined;
    setLoadingField(true);
    prevRunningCount.current = 0;
    api
      .modelsSummary(selectedProject, selected, v)
      .then((s) => {
        setSummaries(s);
        setSelectedModels(new Set(s.map((m) => m.model_id)));
        setApiError(null);
      })
      .catch((e) => setApiError(String(e)))
      .finally(() => setLoadingField(false));
    api.llmJudgeSummary(selectedProject, selected, v).then(setLlmJudge).catch(() => setLlmJudge([]));
    api.crossModelAgreement(selectedProject, selected, v).then(setCrossAgreement).catch(() => setCrossAgreement([]));
    api.calibration(selectedProject, selected, v).then(setCalibration).catch(() => setCalibration([]));
  }, [selectedProject, selected, selectedVersion]);

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
      <header className="dashboard-header">
        <h1>Agentic 3ie Prompt Lab</h1>
        <p className="muted">
          We test different AI models and prompts on evidence-synthesis tasks — like screening
          studies for inclusion/exclusion and pulling structured details (authors, institutions,
          sectors) out of research papers — to see which combinations work best.
        </p>
        {projects && projects.length > 0 && (
          <div className="project-switcher">
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
        <nav className="tab-nav">
          <button className={tab === "dashboard" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("dashboard")}>
            Dashboard
          </button>
          <button className={tab === "about" ? "tab-btn active" : "tab-btn"} onClick={() => setTab("about")}>
            How it works
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

          <Methodology thresholds={thresholds} />

          {fields && fields.length > 0 && (
            <div className="dashboard-body">
              <nav className="field-nav">
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
                      {stageStatus && <StageBadge s={stageStatus} />}
                      {(() => {
                        // Only offer versions from a real production run (many
                        // models); hide the optimizer's single-model 12-paper
                        // trial versions that would otherwise clutter this.
                        const prod = runVersionList.filter((v) => v.n_models >= 2);
                        if (prod.length === 0) return null;
                        const bestV = prod.reduce((a, b) => (b.n_runs > a.n_runs ? b : a)).version;
                        return (
                          <div className="version-select">
                            <label>
                              Prompt version:{" "}
                              <select
                                value={selectedVersion ?? ""}
                                onChange={(e) => setSelectedVersion(Number(e.target.value))}
                              >
                                {prod.map((v) => (
                                  <option key={v.version} value={v.version}>
                                    v{v.version}{v.version === bestV ? " (current)" : ""} · {v.n_runs} runs
                                  </option>
                                ))}
                              </select>
                            </label>{" "}
                            <span className="muted">metrics &amp; plots below reflect this version</span>
                          </div>
                        );
                      })()}
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
                        <section className="panel panel-aggregate">
                          <h3>All models — summary</h3>
                          {thresholds && (
                            <p className="muted panel-caption">
                              Accuracy here = share of references scoring ≥ {thresholds.correct_threshold.toFixed(2)}
                              (fuzzy matches count as correct). Each model card below also shows a
                              stricter exact-match accuracy and an LLM-judged accuracy — see "How to
                              read this dashboard" above for what each one means and why they can
                              differ. Every model is optimized and evaluated against its own prompt
                              history — see the per-model cards below for each model's own iteration
                              progress, prompt lineage, and confusion matrix.
                            </p>
                          )}
                          <ModelComparisonTable summaries={summaries} />
                        </section>

                        {summaries.length === 0 ? (
                          <p className="muted">No references processed yet for this field.</p>
                        ) : (
                          <>
                            <ModelFilter
                              models={summaries.map((s) => s.model_id)}
                              selected={selectedModels}
                              onToggle={toggleModel}
                              onSelectAll={() => setSelectedModels(new Set(summaries.map((s) => s.model_id)))}
                              onSelectNone={() => setSelectedModels(new Set())}
                            />
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
                                  promptVersion={selectedVersion ?? undefined}
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
        </>
      )}
    </div>
  );
}

export default App;
