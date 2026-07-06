"""Read-only ground-truth audit.

Scans the `ground_truth` table for the 5 prototype fields and reports data-
quality issues that can make the extraction task effectively unwinnable or
noisy to score/judge:

  * encoding noise (accents/mojibake/non-breaking spaces) that differs from the
    folded form the scorer/judge now compare on;
  * within-record duplicate values;
  * `authors` entries that look like "First Last" instead of the required
    "Last, First" format (a format inconsistency in the reference data);
  * categorical values (sector/sub_sector/country) that are NOT in the allowed
    taxonomy, and rare categories (<= 2 records) that are hard to learn.

Makes NO model calls and writes nothing to the database. Purely diagnostic:
the output is for a human to decide which reference values to correct.

Usage (from DEP root, .venv active):
    python -m backend.scripts.audit_ground_truth
    python -m backend.scripts.audit_ground_truth --field authors
    python -m backend.scripts.audit_ground_truth --csv issues.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db, scoring  # noqa: E402
from backend.app.fields import FIELDS  # noqa: E402
from backend.app.taxonomy import get_options  # noqa: E402

MAX_EXAMPLES = 12  # cap examples printed per issue category


def _load_gt(conn, field_name: str) -> list[tuple[int, str, object]]:
    """Return [(record_id, title, value)] for a field, value parsed from JSON."""
    rows = conn.execute(
        "SELECT g.record_id AS rid, r.title AS title, g.value_json AS vj "
        "FROM ground_truth g JOIN records r ON r.id = g.record_id "
        "WHERE g.field_name = ? ORDER BY g.record_id",
        (field_name,),
    ).fetchall()
    out = []
    for row in rows:
        try:
            val = json.loads(row["vj"])
        except (TypeError, json.JSONDecodeError):
            val = row["vj"]
        out.append((row["rid"], row["title"], val))
    return out


def _as_items(value) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if value in (None, ""):
        return []
    return [str(value)]


def _has_encoding_noise(s: str) -> bool:
    # The scorer/judge already fold accents+mojibake+nbsp; flag values whose
    # folded form differs so a human can see how much noise is in the reference.
    return scoring.fold_display(s) != s


def _looks_first_last(name: str) -> bool:
    # `authors` should be "Last, First ...". A comma-free multi-word token is a
    # likely "First Last" inconsistency. Single-token names are left alone.
    n = name.strip()
    return ("," not in n) and (len(n.split()) >= 2)


def audit_field(conn, field_name: str, issues: list[dict]) -> None:
    spec = FIELDS[field_name]
    data = _load_gt(conn, field_name)
    total = len(data)
    print(f"\n{'=' * 78}\nFIELD: {field_name}  ({spec.value_type})  \u2014 {total} records with ground truth")
    if total == 0:
        print("  (no ground truth rows)")
        return

    empty = [rid for rid, _t, v in data if not _as_items(v)]
    print(f"  empty/blank values: {len(empty)}")

    # --- encoding noise ---
    noisy = []
    for rid, _t, v in data:
        for item in _as_items(v):
            if _has_encoding_noise(item):
                noisy.append((rid, item, scoring.fold_display(item)))
    print(f"  values with accent/mojibake/nbsp noise: {len(noisy)}")
    for rid, raw, folded in noisy[:MAX_EXAMPLES]:
        print(f"      rec {rid}: {raw!r} -> folds to {folded!r}")
        issues.append({"field": field_name, "issue": "encoding_noise", "record_id": rid,
                       "value": raw, "detail": f"folds to {folded}"})

    # --- within-record duplicates (after folding) ---
    dup_records = []
    for rid, _t, v in data:
        items = _as_items(v)
        folded_items = [scoring.fold_display(x).lower() for x in items]
        if len(folded_items) != len(set(folded_items)):
            dup_records.append((rid, items))
    if dup_records:
        print(f"  records with duplicate entries: {len(dup_records)}")
        for rid, items in dup_records[:MAX_EXAMPLES]:
            print(f"      rec {rid}: {items}")
            issues.append({"field": field_name, "issue": "duplicate_entries", "record_id": rid,
                           "value": json.dumps(items, ensure_ascii=False), "detail": ""})

    # --- authors name-order inconsistency ---
    if field_name == "authors":
        first_last = []
        for rid, _t, v in data:
            for item in _as_items(v):
                if _looks_first_last(item):
                    first_last.append((rid, item))
        print(f"  author entries that look like 'First Last' (want 'Last, First'): {len(first_last)}")
        for rid, item in first_last[:MAX_EXAMPLES]:
            print(f"      rec {rid}: {item!r}")
            issues.append({"field": field_name, "issue": "name_order", "record_id": rid,
                           "value": item, "detail": "no comma; likely First Last"})

    # --- categorical: values outside taxonomy + rare categories ---
    if spec.taxonomy_key:
        allowed = get_options(spec.taxonomy_key)
        allowed_folded = {scoring.fold_display(a).lower() for a in allowed}
        counts: Counter = Counter()
        invalid = []
        for rid, _t, v in data:
            for item in _as_items(v):
                counts[item] += 1
                if scoring.fold_display(item).lower() not in allowed_folded:
                    invalid.append((rid, item))
        print(f"  distinct categories used: {len(counts)} / {len(allowed)} allowed")
        print(f"  values NOT in the allowed taxonomy: {len(invalid)}")
        for rid, item in invalid[:MAX_EXAMPLES]:
            print(f"      rec {rid}: {item!r}")
            issues.append({"field": field_name, "issue": "not_in_taxonomy", "record_id": rid,
                           "value": item, "detail": f"taxonomy_key={spec.taxonomy_key}"})
        rare = [(c, n) for c, n in counts.items() if n <= 2]
        print(f"  rare categories (<=2 records, hard to learn/measure): {len(rare)}")
        for cat, n in sorted(rare)[:MAX_EXAMPLES]:
            print(f"      {cat!r}: {n}")
            issues.append({"field": field_name, "issue": "rare_category", "record_id": "",
                           "value": cat, "detail": f"n={n}"})


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only ground-truth quality audit (no model calls).")
    ap.add_argument("--field", choices=list(FIELDS), help="audit a single field (default: all)")
    ap.add_argument("--csv", help="also write all flagged issues to this CSV path")
    args = ap.parse_args()

    fields = [args.field] if args.field else list(FIELDS)
    issues: list[dict] = []
    with db.get_conn() as conn:  # get_conn already sets sqlite3.Row row_factory
        for f in fields:
            audit_field(conn, f, issues)

    print(f"\n{'=' * 78}\nTOTAL flagged issues: {len(issues)}")
    by_kind = Counter(i["issue"] for i in issues)
    for kind, n in by_kind.most_common():
        print(f"  {kind}: {n}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["field", "issue", "record_id", "value", "detail"])
            w.writeheader()
            w.writerows(issues)
        print(f"\nWrote {len(issues)} issues to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
