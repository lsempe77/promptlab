import { useEffect, useRef, useState } from "react";
import { api, type FieldInfo, type Job, type LlmJudgeSummary, type ModelSummary, type Thresholds } from "./api";
import { ModelComparisonTable } from "./components/ModelComparisonTable";
import { ModelCard } from "./components/ModelCard";
import { ModelFilter } from "./components/ModelFilter";
import { Methodology } from "./components/Methodology";
import { About } from "./components/About";
import "./App.css";

const JOBS_POLL_MS = 6000;

function App() {
  const [tab, setTab] = useState<"dashboard" | "about">("dashboard");
  const [fields, setFields] = useState<FieldInfo[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);

  const [summaries, setSummaries] = useState<ModelSummary[]>([]);
  const [llmJudge, setLlmJudge] = useState<LlmJudgeSummary[]>([]);
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set());
  const [loadingField, setLoadingField] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const prevRunningCount = useRef(0);

  useEffect(() => {
    api
      .fields()
      .then((f) => {
        setFields(f);
        setApiError(null);
        if (f.length > 0) setSelected(f[0].name);
      })
      .catch((e) => setApiError(String(e)));
    api.thresholds().then(setThresholds).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoadingField(true);
    prevRunningCount.current = 0;
    api
      .modelsSummary(selected)
      .then((s) => {
        setSummaries(s);
        setSelectedModels(new Set(s.map((m) => m.model_id)));
        setApiError(null);
      })
      .catch((e) => setApiError(String(e)))
      .finally(() => setLoadingField(false));
    api.llmJudgeSummary(selected).then(setLlmJudge).catch(() => setLlmJudge([]));
  }, [selected]);

  // Poll for running extraction/optimization jobs so the dashboard can show a
  // "currently running" indicator even though the backend has no push/websocket
  // mechanism — this is the only way to notice a job started after page load.
  // When a job's running count drops back to zero (it just finished), also
  // re-fetch the model summary so newly-logged runs show up without a reload.
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const poll = () => {
      api
        .jobs(selected)
        .then((j) => {
          if (cancelled) return;
          setJobs(j);
          const runningCount = j.filter((job) => job.status === "running" && !job.stale).length;
          if (runningCount === 0 && prevRunningCount.current > 0) {
            api.modelsSummary(selected).then(setSummaries).catch(() => {});
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
  }, [selected]);

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
        <h1>3ie Prompt Lab</h1>
        <p className="muted">
          We test different AI models and prompts on evidence-synthesis tasks — like screening
          studies for inclusion/exclusion and pulling structured details (authors, institutions,
          sectors) out of research papers — to see which combinations work best.
        </p>
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
                                  fieldName={selected!}
                                  summary={s}
                                  jobs={jobs.filter((j) => j.model_id === s.model_id)}
                                  llmJudge={llmJudge.find((j) => j.model_id === s.model_id) ?? null}
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
