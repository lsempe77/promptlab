# AGENTS.md — build, test, and lint commands for PromptLab

## Frontend (React + Vite + TypeScript)

```bash
npm install              # install deps
npm run dev              # dev server at http://localhost:5173/promptlab/
npm run build            # tsc -b && vite build
npm run lint             # oxlint
npm test                 # vitest run (single pass)
npm run test:watch       # vitest in watch mode
```

Type-check: `npx tsc -b` (runs as part of `npm run build`).

## Backend (Python + FastAPI)

```bash
cd backend
pip install -r requirements-dev.txt   # includes pytest
pytest                                 # run all tests
pytest tests/test_scoring.py -v        # run a single file
```

The backend pytest config lives in `backend/pyproject.toml` (`pythonpath = [".."]`
so tests import from `backend.app.*`).
