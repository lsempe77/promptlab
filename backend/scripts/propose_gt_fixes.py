"""Read-only proposal of ground-truth fixes for the CATEGORICAL fields whose
reference values drifted from the controlled vocabulary (see
`audit_ground_truth.py`). Two cases are handled:

  * a GT value that is NOT in the allowed taxonomy but is a near-match to one
    allowed option (e.g. the Oxford-comma drift
    'Water, sanitation, and waste management' -> the taxonomy's
    'Water, sanitation and waste management') -> propose the canonical option;
  * a GT value that packs several taxonomy entries into one string with ' | '
    (illegal for a single-valued field) -> list the valid parts and flag it for
    a MANUAL pick (the script can't know which single one is intended).

Makes NO model calls and writes NOTHING to the database. It only prints a diff
(and optionally a CSV) for a human to review and apply by hand.

Usage (from the promptlab repo root, .venv active, DEP_DB_PATH set):
    python -m backend.scripts.propose_gt_fixes
    python -m backend.scripts.propose_gt_fixes --field sector_name
    python -m backend.scripts.propose_gt_fixes --csv gt_fixes.csv --min-score 85
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rapidfuzz import fuzz  # noqa: E402

from backend.app import db, scoring  # noqa: E402
from backend.app.fields import FIELDS  # noqa: E402
from backend.app.taxonomy import get_options  # noqa: E402

DEFAULT_MIN_SCORE = 85  # rapidfuzz token_sort_ratio (0-100) below which we won't auto-propose


def _load_gt(conn, field_name: str) -> list[tuple[int, object]]:
    rows = conn.execute(
        "SELECT g.record_id AS rid, g.value_json AS vj FROM ground_truth g "
        "WHERE g.field_name = ? ORDER BY g.record_id",
        (field_name,),
    ).fetchall()
    out = []
    for row in rows:
        try:
            out.append((row["rid"], json.loads(row["vj"])))
        except (TypeError, json.JSONDecodeError):
            out.append((row["rid"], row["vj"]))
    return out


def _as_items(value) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _best_match(value: str, allowed: list[str]) -> tuple[str, float]:
    """Closest allowed option to `value`, matched on the folded/lowered form so
    accents and Oxford-comma noise don't block the match; returns the canonical
    (un-folded) allowed string and the score."""
    target = scoring.fold_display(value).lower()
    best, best_score = "", -1.0
    for canon in allowed:
        s = fuzz.token_sort_ratio(target, scoring.fold_display(canon).lower())
        if s > best_score:
            best, best_score = canon, s
    return best, best_score


def propose_field(conn, field_name: str, min_score: float, out: list[dict]) -> None:
    spec = FIELDS[field_name]
    if not spec.taxonomy_key:
        return
    allowed = get_options(spec.taxonomy_key)
    allowed_folded = {scoring.fold_display(a).lower() for a in allowed}
    data = _load_gt(conn, field_name)

    print(f"\n{'=' * 80}\nFIELD: {field_name}  ({len(allowed)} allowed options)")
    n_ok = n_map = n_pipe = n_unsure = 0
    for rid, value in data:
        for item in _as_items(value):
            folded = scoring.fold_display(item).lower()
            if folded in allowed_folded:
                n_ok += 1
                continue
            if "|" in item:  # compound value crammed into a single-valued field
                parts = [p.strip() for p in item.split("|") if p.strip()]
                valid = [p for p in parts if scoring.fold_display(p).lower() in allowed_folded]
                n_pipe += 1
                print(f"  rec {rid}: {item!r}")
                print(f"      -> MANUAL pick one of: {valid or parts}")
                out.append({"field": field_name, "record_id": rid, "current": item,
                            "proposed": " OR ".join(valid or parts), "score": "", "action": "manual_pick"})
                continue
            match, score = _best_match(item, allowed)
            if score >= min_score:
                n_map += 1
                print(f"  rec {rid}: {item!r}\n      -> {match!r}   (match {score:.0f})")
                out.append({"field": field_name, "record_id": rid, "current": item,
                            "proposed": match, "score": f"{score:.0f}", "action": "remap"})
            else:
                n_unsure += 1
                print(f"  rec {rid}: {item!r}\n      -> ?? no confident match (best {match!r} @ {score:.0f})")
                out.append({"field": field_name, "record_id": rid, "current": item,
                            "proposed": match, "score": f"{score:.0f}", "action": "review"})
    print(f"  summary: {n_ok} already valid | {n_map} auto-remap proposed | "
          f"{n_pipe} pipe/manual | {n_unsure} needs review")


def main() -> int:
    categorical = [f for f, s in FIELDS.items() if s.taxonomy_key]
    ap = argparse.ArgumentParser(description="Propose (do not apply) ground-truth fixes for categorical fields.")
    ap.add_argument("--field", choices=categorical, help="single field (default: all categorical)")
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE,
                     help="min rapidfuzz score to auto-propose a remap (else flagged for review)")
    ap.add_argument("--csv", help="also write all proposals to this CSV path")
    args = ap.parse_args()

    fields = [args.field] if args.field else categorical
    out: list[dict] = []
    with db.get_conn() as conn:  # get_conn sets sqlite3.Row; this script never writes
        for f in fields:
            propose_field(conn, f, args.min_score, out)

    print(f"\n{'=' * 80}\nTOTAL proposals: {len(out)} (nothing was written to the database)")
    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["field", "record_id", "current", "proposed", "score", "action"])
            w.writeheader()
            w.writerows(out)
        print(f"Wrote {len(out)} proposals to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
