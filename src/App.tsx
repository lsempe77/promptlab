import { useEffect, useState } from "react";
import { api, type FieldInfo, type IterationLog, type ModelSummary, type PromptVersion } from "./api";
import { ModelComparisonTable } from "./components/ModelComparisonTable";
import { PromptLineage } from "./components/PromptLineage";
import { IterationChart } from "./components/IterationChart";
import "./App.css";

function App() {
  const [fields, setFields] = useState<FieldInfo[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const [summaries, setSummaries] = useState<ModelSummary[]>([]);
  const [versions, setVersions] = useState<PromptVersion[]>([]);
  const [iterations, setIterations] = useState<IterationLog[]>([]);
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
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoadingField(true);
    Promise.all([
      api.modelsSummary(selected),
      api.promptVersions(selected),
      api.iterations(selected),
    ])
      .then(([s, v, it]) => {
        setSummaries(s);
        setVersions(v);
        setIterations(it);
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
      </header>

      {apiError && (
        <div className="api-error">
          <strong>Can't reach the API</strong> ({apiError}). Is the backend running locally? Start it with{" "}
          <code>python -m backend.scripts.serve</code> from the DEP project, then reload this page.
        </div>
      )}

      {!apiError && !fields && <p className="muted">Loading fields…</p>}

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
                    <section className="panel">
                      <h3>Model comparison</h3>
                      <ModelComparisonTable summaries={summaries} />
                    </section>

                    <section className="panel">
                      <h3>Optimizer progress</h3>
                      <IterationChart iterations={iterations} />
                    </section>

                    <section className="panel">
                      <h3>Prompt lineage</h3>
                      <PromptLineage versions={versions} />
                    </section>
                  </>
                )}
              </>
            )}
          </main>
        </div>
      )}
    </div>
  );
}

export default App;
