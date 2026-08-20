"""Apply human-reviewed ground-truth fixes back into the `ground_truth` table.

This is the APPLY half of "Loop B": the detection scripts (`audit_ground_truth.py`,
`propose_gt_fixes.py`) flagged suspect reference values for three fields; a human
curator filled a `curator_decision` column in per-field review CSVs. This script
reads those decisions and writes the accepted/edited corrections back to the DB,
recording every old -> new change in a `gt_audit_log` table plus a timestamped
JSON backup file so the whole batch is reversible.

Decision semantics (identical across fields: `keep` = reject the audit's change,
`accept` = apply the audit's proposed change, `edit: X` = curator's manual value):

  single_categorical (sector_name, sub_sector), one row (or agreeing rows) per record:
      keep    -> no change
      accept  -> GT := `suggestion`
      edit: X -> GT := X            (also covers the `pipe_pick_one` split)

  list_text (author_affiliation), one row per candidate affiliation `value`:
      models_agree_gt_lacks + accept  -> ADD `value` to the GT list
      models_agree_gt_lacks + keep    -> no change
      recall_miss           + accept  -> REMOVE `value` from the GT list
      recall_miss           + keep    -> no change
      recall_miss           + edit: X -> REPLACE `value` with X in the GT list

Dry-run by default (writes nothing). Pass --apply to commit.

Usage (from the promptlab repo root, .venv active):
    python -m backend.scripts.apply_gt_fixes --csv-dir <dir-with-the-3-csvs>
    python -m backend.scripts.apply_gt_fixes --csv-dir <dir> --target deploy --apply
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rapidfuzz import fuzz  # noqa: E402

from backend.app import db, scoring  # noqa: E402
from backend.app.fields import FIELDS  # noqa: E402
from backend.app.taxonomy import get_options  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252 by default
except Exception:
    pass

FIELD_FILES = {
    "sector_name": "sector_name.csv",
    "sub_sector": "sub_sector.csv",
    "author_affiliation": "author_affiliation.csv",
}
DEFAULT_FUZZY = 90  # token_sort_ratio cutoff for locating a value inside a GT list


# --------------------------------------------------------------------------- helpers
def _parse_decision(raw: str) -> tuple[str, str | None]:
    """-> ('keep'|'accept'|'edit'|'', edit_value_or_None)."""
    s = (raw or "").strip()
    low = s.lower()
    if low.startswith("edit:"):
        return "edit", s.split(":", 1)[1].strip()
    if low in ("keep", "accept", ""):
        return low, None
    # anything else: treat the whole cell as an explicit override value
    return "edit", s


def _fold(s: str) -> str:
    return scoring.fold_display(str(s)).lower().strip()


def _list_match(item: str, value: str, fuzzy: int) -> bool:
    a, b = _fold(item), _fold(value)
    if not a or not b:
        return a == b
    if a == b:
        return True
    return fuzz.token_sort_ratio(a, b) >= fuzzy


def _load_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _current_gt(conn, project_id: int, record_id: int, field: str):
    row = conn.execute(
        "SELECT value_json FROM ground_truth WHERE project_id=? AND record_id=? AND field_name=?",
        (project_id, record_id, field),
    ).fetchone()
    if row is None:
        return None, False
    try:
        return json.loads(row["value_json"]), True
    except (TypeError, json.JSONDecodeError):
        return row["value_json"], True


# --------------------------------------------------------------------------- planning
def plan_categorical(rows: list[dict], field: str, conn, project_id: int) -> tuple[list[dict], list[str]]:
    """Returns (changes, warnings). One change dict per record that actually differs."""
    allowed = get_options(FIELDS[field].taxonomy_key)
    allowed_fold = {_fold(o): o for o in allowed}
    by_record: dict[int, list[dict]] = {}
    for r in rows:
        by_record.setdefault(int(r["record_id"]), []).append(r)

    changes, warnings = [], []
    for rid, rrows in sorted(by_record.items()):
        targets: set[str] = set()
        had_keep = False
        for r in rrows:
            kind, edit_val = _parse_decision(r.get("curator_decision", ""))
            if kind == "keep" or kind == "":
                had_keep = had_keep or kind == "keep"
                continue
            if kind == "accept":
                targets.add((r.get("suggestion") or "").strip())
            elif kind == "edit" and edit_val:
                targets.add(edit_val)
        targets.discard("")
        if not targets:
            continue
        if len(targets) > 1:
            warnings.append(f"rid {rid}: CONFLICT, multiple target values {sorted(targets)} -> skipped")
            continue
        new_val = next(iter(targets))
        if had_keep:
            warnings.append(f"rid {rid}: mixed keep + change rows; using change target {new_val!r}")
        if _fold(new_val) not in allowed_fold:
            warnings.append(f"rid {rid}: value {new_val!r} is not a canonical taxonomy option")
        cur, exists = _current_gt(conn, project_id, rid, field)
        if not exists:
            warnings.append(f"rid {rid}: no existing GT row -> skipped")
            continue
        if cur == new_val:
            continue
        changes.append({"record_id": rid, "field": field, "old": cur, "new": new_val, "ops": ["set"]})
    return changes, warnings


def plan_list(rows: list[dict], field: str, conn, project_id: int, fuzzy: int) -> tuple[list[dict], list[str]]:
    by_record: dict[int, list[dict]] = {}
    for r in rows:
        by_record.setdefault(int(r["record_id"]), []).append(r)

    changes, warnings = [], []
    for rid, rrows in sorted(by_record.items()):
        cur, exists = _current_gt(conn, project_id, rid, field)
        if not exists:
            warnings.append(f"rid {rid}: no existing GT row -> skipped")
            continue
        work = [str(x) for x in (cur if isinstance(cur, list) else ([cur] if cur else []))]
        ops: list[str] = []
        for r in rrows:
            issue = (r.get("issue_type") or "").strip()
            value = (r.get("value") or "").strip()
            kind, edit_val = _parse_decision(r.get("curator_decision", ""))
            if kind in ("keep", ""):
                continue
            if issue == "models_agree_gt_lacks":
                if kind == "accept":
                    if any(_list_match(it, value, fuzzy) for it in work):
                        continue  # already present
                    work.append(value)
                    ops.append(f"add: {value}")
                elif kind == "edit" and edit_val:
                    if not any(_list_match(it, edit_val, fuzzy) for it in work):
                        work.append(edit_val)
                        ops.append(f"add: {edit_val}")
            elif issue == "recall_miss":
                idx = next((i for i, it in enumerate(work) if _list_match(it, value, fuzzy)), None)
                if kind == "accept":  # remove spurious/subunit value
                    if idx is None:
                        warnings.append(f"rid {rid}: remove target {value!r} not found in GT -> skipped")
                    else:
                        ops.append(f"remove: {work[idx]}")
                        work.pop(idx)
                elif kind == "edit" and edit_val:  # replace value with corrected name
                    if idx is None:
                        warnings.append(f"rid {rid}: replace target {value!r} not found in GT -> added {edit_val!r}")
                        work.append(edit_val)
                        ops.append(f"add: {edit_val}")
                    else:
                        ops.append(f"replace: {work[idx]} -> {edit_val}")
                        work[idx] = edit_val
        # dedupe preserving order
        seen, deduped = set(), []
        for it in work:
            k = _fold(it)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(it)
        if deduped != (cur if isinstance(cur, list) else cur):
            if not ops:
                continue
            changes.append({"record_id": rid, "field": field, "old": cur, "new": deduped, "ops": ops})
    return changes, warnings


# --------------------------------------------------------------------------- apply
def _ensure_audit_table(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gt_audit_log ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " applied_at TEXT NOT NULL,"
        " project_id INTEGER NOT NULL,"
        " record_id INTEGER NOT NULL,"
        " field_name TEXT NOT NULL,"
        " old_value_json TEXT,"
        " new_value_json TEXT,"
        " ops TEXT,"
        " source TEXT)"
    )


def apply_changes(conn, project_id: int, changes: list[dict], source: str) -> None:
    _ensure_audit_table(conn)
    ts = datetime.now(timezone.utc).isoformat()
    for ch in changes:
        db.upsert_ground_truth(conn, project_id, ch["record_id"], ch["field"], ch["new"])
        conn.execute(
            "INSERT INTO gt_audit_log (applied_at, project_id, record_id, field_name,"
            " old_value_json, new_value_json, ops, source) VALUES (?,?,?,?,?,?,?,?)",
            (ts, project_id, ch["record_id"], ch["field"],
             json.dumps(ch["old"], ensure_ascii=False),
             json.dumps(ch["new"], ensure_ascii=False),
             "; ".join(ch["ops"]), source),
        )


def invalidate_stale_judgments(conn, project_id: int, changes: list[dict]) -> int:
    """Delete LLM judgments for records whose ground truth just changed.

    A judgment is a verdict of "prediction vs ground truth". Once the ground
    truth changes, any earlier verdict was decided against a value that no
    longer exists, so it is not merely outdated but wrong — and because
    `llm_judge` only picks up runs with NO judgment, a stale row would never be
    revisited. Deleting lets the next judge pass regenerate it (judgments are
    derived data; `runs` and `ground_truth` are untouched).
    """
    deleted = 0
    for ch in changes:
        cur = conn.execute(
            "DELETE FROM llm_judgments WHERE run_id IN ("
            "  SELECT id FROM runs WHERE project_id = ? AND record_id = ? AND field_name = ?"
            ")",
            (project_id, ch["record_id"], ch["field"]),
        )
        deleted += cur.rowcount or 0
    return deleted


def _fmt(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 140 else s[:137] + "..."


# --------------------------------------------------------------------------- main
def resolve_dbs(target: str, explicit: list[str]) -> list[Path]:
    if explicit:
        return [Path(p) for p in explicit]
    root = Path(__file__).resolve().parents[1]  # backend/
    data_db = root / "data" / "promptlab.db"
    deploy_db = root / "deploy" / "promptlab.db"
    return {"data": [data_db], "deploy": [deploy_db], "both": [data_db, deploy_db]}[target]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv-dir", required=True, help="directory containing the per-field review CSVs")
    ap.add_argument("--target", choices=["data", "deploy", "both"], default="both",
                    help="which repo DB(s) to apply to (ignored if --db given); default both")
    ap.add_argument("--db", action="append", default=[], help="explicit DB path (repeatable)")
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--fields", default=",".join(FIELD_FILES),
                    help="comma-separated subset of fields to process")
    ap.add_argument("--fuzzy", type=int, default=DEFAULT_FUZZY)
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    args = ap.parse_args()

    csv_dir = Path(args.csv_dir)
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    dbs = resolve_dbs(args.target, args.db)
    source = f"apply_gt_fixes:{csv_dir.name}"

    mode = "APPLY" if args.apply else "DRY RUN (no writes)"
    print(f"\n=== apply_gt_fixes  [{mode}]  project={args.project}  fuzzy={args.fuzzy} ===")

    grand_total = 0
    for db_path in dbs:
        if not db_path.exists():
            print(f"\n### DB {db_path}  -> MISSING, skipped")
            continue
        print(f"\n### DB {db_path}")
        with db.get_conn(db_path) as conn:
            try:
                project_id = db.get_project_id(conn, args.project)
            except Exception:
                project_id = 1
                print(f"  (project slug {args.project!r} not found; defaulting project_id=1)")

            all_changes: list[dict] = []
            for field in fields:
                fname = FIELD_FILES.get(field)
                path = csv_dir / fname if fname else None
                if not path or not path.exists():
                    print(f"  [{field}] CSV not found in {csv_dir} -> skipped")
                    continue
                rows = _load_rows(path)
                if FIELDS[field].value_type == "list_text":
                    changes, warnings = plan_list(rows, field, conn, project_id, args.fuzzy)
                else:
                    changes, warnings = plan_categorical(rows, field, conn, project_id)
                all_changes.extend(changes)
                print(f"  [{field}] {len(rows)} rows reviewed -> {len(changes)} record(s) change")
                for ch in changes:
                    print(f"      rid {ch['record_id']}: {'; '.join(ch['ops'])}")
                    print(f"          old: {_fmt(ch['old'])}")
                    print(f"          new: {_fmt(ch['new'])}")
                for w in warnings:
                    print(f"      ! {w}")

            if args.apply and all_changes:
                backup = {
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "db": str(db_path),
                    "project_id": project_id,
                    "changes": [
                        {"record_id": c["record_id"], "field_name": c["field"],
                         "old": c["old"], "new": c["new"], "ops": c["ops"]}
                        for c in all_changes
                    ],
                }
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                backup_path = db_path.parent / f"gt_fixes_backup_{stamp}.json"
                backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
                apply_changes(conn, project_id, all_changes, source)
                n_judgments = invalidate_stale_judgments(conn, project_id, all_changes)
                print(f"  -> APPLIED {len(all_changes)} change(s); backup written to {backup_path.name}")
                if n_judgments:
                    print(f"  -> invalidated {n_judgments} LLM judgment(s) decided against the OLD "
                          f"ground truth (they will be re-judged on the next judge pass)")
                print("  -> NOTE: run `rescore_runs.py` to refresh runs.score/is_correct for these records.")
            elif args.apply:
                print("  -> nothing to apply")
            grand_total += len(all_changes)

    print(f"\n=== total record changes across DB(s): {grand_total} "
          f"({'written' if args.apply else 'dry run — rerun with --apply to write'}) ===\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
