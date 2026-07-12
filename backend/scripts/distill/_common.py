"""Shared helpers for the distillation scripts: paths, UTF-8 console, and the
canonical assistant-message reconstruction used as the SFT target."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# backend/scripts/distill/data/<field>/...
DATA_ROOT = Path(__file__).resolve().parent / "data"


def field_dir(field: str) -> Path:
    d = DATA_ROOT / field
    d.mkdir(parents=True, exist_ok=True)
    return d


def setup_utf8() -> None:
    """Windows consoles default to cp1252 and raise on diacritics in author
    names / paper text; force UTF-8 so one non-ASCII line can't kill a batch."""
    try:  # pragma: no cover
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def canonical_assistant_json(value_type: str, value: Any, meta: dict[str, Any]) -> str:
    """Rebuild a clean, schema-valid assistant message from the teacher's PARSED
    value + meta, rather than reusing the teacher's raw text. This guarantees
    well-formed JSON training targets (a teacher can emit fenced/te trailing-prose
    JSON that parses via our fallback but shouldn't be taught verbatim) and keeps
    the target identical in shape to prompts._{SINGLE,LIST}_JSON_CONTRACT."""
    excerpt = meta.get("excerpt")
    notes = meta.get("notes")
    confidence = meta.get("confidence")
    if value_type == "single_categorical":
        obj = {"excerpt": excerpt, "value": value, "confidence": confidence, "notes": notes}
    else:
        obj = {"excerpt": excerpt, "values": value or [], "confidence": confidence, "notes": notes}
    # ensure_ascii=False keeps accented names readable in the dataset.
    return json.dumps(obj, ensure_ascii=False)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    # Split ONLY on "\n" — never str.splitlines(), which also breaks on exotic
    # Unicode line separators (\x0b, \x85,  ,  , ...) that appear in the
    # extracted paper text and that json.dumps(ensure_ascii=False) leaves literal,
    # which would split a single JSONL record mid-string and corrupt parsing.
    for line in path.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )
