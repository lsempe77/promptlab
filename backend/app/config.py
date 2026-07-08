"""Central configuration: paths, environment loading, model roster."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEP_ROOT = BACKEND_DIR.parent

# Corpus of markdown full texts, filtered to only the verified-OK matches from
# the earlier QA pass (see ../../README.md). Overridable via DEP_MD_DIR (e.g. a
# mounted volume path in a cloud deploy) so the same code works locally and
# remotely without a fork.
MD_DIR = Path(
    os.environ.get(
        "DEP_MD_DIR",
        r"C:\Users\LucasSempe\OneDrive - International Initiative for Impact Evaluation"
        r"\Desktop\International Initiative for Impact Evaluation"
        r"\DEP Chatbot - full_text_md_apache_ok_only_final",
    )
)

IER_RECORDS_XLSX = DEP_ROOT / "1770900869-ier-records.xlsx"
MODELS_YAML = BACKEND_DIR / "models.yaml"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BACKEND_DIR / ".env")

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Production rollout: validate at 100 records, then 200, then 300 — never larger
# without a deliberate code change. Scripts clamp to this and warn if exceeded.
MAX_PRODUCTION_RECORDS = 200
# Stage progression: only one stage because run_extraction.py is restricted to
# ground-truth records (get_records_with_field JOINs records x ground_truth).
# With 100 GT records per field, --n 200 still only finds 100 → nothing to run.
# Stage 200 becomes reachable only after humans annotate 100 more GT records
# and load them into the DB.  Until then, 100 is both the start and finish.
PRODUCTION_ROLLOUT_STAGES = (100,)

# Directory for user-created project corpora (separate from the DEP corpus).
# Mirrors where the DB lives: /data/projects/{slug}/corpus/ on Fly.
_data_dir = Path(os.environ.get("DEP_DB_PATH", str(Path(__file__).resolve().parents[1] / "data" / "promptlab.db"))).parent
PROJECTS_DATA_DIR = _data_dir / "projects"


def load_models() -> list[dict]:
    """Return the configured model roster (see models.yaml), flattened to a
    list of {id, tier} dicts."""
    data = yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))
    models = []
    for tier, ids in data.items():
        for model_id in ids:
            models.append({"id": model_id, "tier": tier})
    return models
