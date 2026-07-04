# PromptLab (frontend)

Observability dashboard for 3ie's DEP LLM data-extraction prompt lab. Shows:

- Model comparison across the OpenRouter roster (accuracy, cost, latency) per field.
- Prompt version history / lineage (baseline -> optimizer iterations, accepted vs. rejected candidates, reflector diagnoses).
- Metric-over-iterations charts as the GEPA-lite optimizer improves a field's instruction.

## Status

Scaffolded with Vite + React + TypeScript. No UI has been built yet — this is
a placeholder while the backend (FastAPI layer over the existing SQLite
prompt-lab DB) is still in progress. See the `backend/` folder in the sibling
`DEP` workspace for the extraction/scoring/optimizer engine this will visualize.

## Development

```bash
npm install
npm run dev
```

## Planned data source

A FastAPI service (not yet built) will expose read endpoints over
`backend/data/promptlab.db`:

- `GET /fields` — configured extraction fields
- `GET /fields/{field}/prompt-versions` — full lineage (parent_id chain, accepted flag, notes/diagnosis, created_at)
- `GET /fields/{field}/runs` — per-record/per-model run history (score, latency, cost, errors)
- `GET /fields/{field}/iterations` — optimizer iteration log (train/val scores, accept/reject, reflector diagnosis)
- `POST /fields/{field}/optimize` — trigger an optimizer run

---

Original Vite template docs below.

# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.
