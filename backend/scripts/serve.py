"""Convenience entry point for running the prompt-lab API locally.

Usage (from DEP root, .venv active):
    python -m backend.scripts.serve
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402


def main() -> None:
    uvicorn.run("backend.app.api:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
