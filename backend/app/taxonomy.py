"""Loader + lookup helpers for the controlled vocabularies extracted from the
protocol workbook (see scripts/extract_taxonomy.py)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

TAXONOMY_PATH = Path(__file__).resolve().parent / "data" / "taxonomy.json"


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    flat: list[str] = []
    for values in data["sub_sectors_by_sector"].values():
        flat.extend(values)
    data["sub_sectors_flat"] = sorted(set(flat))
    return data


def get_options(taxonomy_key: str) -> list[str]:
    return load_taxonomy().get(taxonomy_key, [])
