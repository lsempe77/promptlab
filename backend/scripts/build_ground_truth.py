"""Builds the ground-truth dataset for the prompt-lab backend.

Joins the raw `1770900869-ier-records.xlsx` export (one row per co-author,
grouped by study `id`) with the QA'd markdown corpus (files named `<id>.md`),
keeping only studies that have both ground truth and full text, then writes
everything into the local SQLite db.

Run: python -m backend.scripts.build_ground_truth   (from the DEP root, with .venv active)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # allow `backend.app...` imports

from backend.app import config, db  # noqa: E402

FIELDS_TO_LOAD = ["id", "title", "authors", "author_affiliation", "author_country", "sector_name", "sub_sector"]


def load_raw() -> pd.DataFrame:
    print("Reading ier-records.xlsx ...")
    df = pd.read_excel(config.IER_RECORDS_XLSX, engine="calamine", usecols=FIELDS_TO_LOAD)
    df = df.dropna(subset=["id"])
    df["id"] = df["id"].astype(int)
    print(f"  raw rows: {len(df):,}, unique ids: {df['id'].nunique():,}")
    return df


def aggregate_by_id(df: pd.DataFrame) -> dict[int, dict]:
    """One author per raw row; sector/sub_sector/title are study-level and
    only populated on the first row of each id's group."""
    out: dict[int, dict] = {}
    for id_, group in df.groupby("id"):
        authors = [a for a in group["authors"].tolist() if isinstance(a, str) and a.strip()]
        affiliations = [a for a in group["author_affiliation"].tolist() if isinstance(a, str) and a.strip()]
        countries = [a for a in group["author_country"].tolist() if isinstance(a, str) and a.strip()]

        def first_non_null(col: str) -> str | None:
            vals = [v for v in group[col].tolist() if isinstance(v, str) and v.strip()]
            return vals[0] if vals else None

        out[int(id_)] = {
            "title": first_non_null("title"),
            "authors": authors,
            "author_affiliation": sorted(set(affiliations), key=affiliations.index),
            "author_country": sorted(set(countries), key=countries.index),
            "sector_name": first_non_null("sector_name"),
            "sub_sector": first_non_null("sub_sector"),
        }
    return out


def available_md_ids() -> set[int]:
    print(f"Scanning md corpus: {config.MD_DIR}")
    ids = set()
    for p in config.MD_DIR.glob("*.md"):
        try:
            ids.add(int(p.stem))
        except ValueError:
            continue
    print(f"  md files with numeric ids: {len(ids):,}")
    return ids


def main() -> None:
    raw = load_raw()
    by_id = aggregate_by_id(raw)
    md_ids = available_md_ids()

    usable_ids = sorted(set(by_id) & md_ids)
    print(f"Studies with both ground truth and full text: {len(usable_ids):,}")

    db.init_db()
    n_gt = {f: 0 for f in ("authors", "author_affiliation", "author_country", "sector_name", "sub_sector")}
    with db.get_conn() as conn:
        for id_ in usable_ids:
            rec = by_id[id_]
            md_path = str(config.MD_DIR / f"{id_}.md")
            db.upsert_record(conn, id_, rec["title"], md_path)
            for field in n_gt:
                value = rec[field]
                if not value:
                    continue
                db.upsert_ground_truth(conn, id_, field, value)
                n_gt[field] += 1

    print("Ground-truth rows written per field:")
    for field, n in n_gt.items():
        print(f"  {field}: {n:,}")
    print(f"\nDone. DB at {db.DB_PATH}")


if __name__ == "__main__":
    main()
