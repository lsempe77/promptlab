import { useEffect, useState } from "react";
import { api, type FieldInfo, type ModelSummary, type Thresholds } from "./api";
import { ModelComparisonTable } from "./components/ModelComparisonTable";
import { ModelCard } from "./components/ModelCard";
import { Methodology } from "./components/Methodology";
import { About } from "./components/About";
import "./App.css";

function App() {
  const [tab, setTab] = useState<"dashboard" | "about">("dashboard");
  const [fields, setFields] = useState<FieldInfo[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [thresholds, setThresholds] = useState<Thresholds | null>(null);

  const [summaries, setSummaries] = useState<ModelSummary[]>([]);
  const [loadingField, setLoadingField] = useState(false);

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
    api
      .modelsSummary(selected)
      .then((s) => {
        setSummaries(s);
        setApiError(null);
      })
      .catch((e) => setApiError(String(e)))
      .finally(() => setLoadingField(false));
  }, [selected]);

  const activeField = fields?.find((f) => f.name === selected) ?? null;

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1>3ie DEP Prompt Lab</h1>
        <p className="muted">
          Prompt optimization dashboard — extraction models, prompt lineage, and optimizer progress.
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

                    {loadingField ? (
                      <p className="muted">Loading…</p>
                    ) : (
                      <>
                        <section className="panel panel-aggregate">
                          <h3>All models — summary</h3>
                          {thresholds && (
                            <p className="muted panel-caption">
                              Accuracy = share of runs scoring ≥ {thresholds.correct_threshold.toFixed(2)}. Every
                              model is optimized and evaluated against its own prompt history — see the per-model
                              cards below for each model's own iteration progress, prompt lineage, and confusion
                              matrix.
                            </p>
                          )}
                          <ModelComparisonTable summaries={summaries} />
                        </section>

                        {summaries.length === 0 ? (
                          <p className="muted">No runs logged yet for this field.</p>
                        ) : (
                          summaries.map((s) => (
                            <ModelCard key={s.model_id} fieldName={selected!} summary={s} />
                          ))
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
