"""Builds a small, self-contained production subset (records + ground truth +
the matching .md files) for deploying the backend somewhere other than this
machine (e.g. a cloud host), without shipping the full ~1.3 GB / 7,675-file
local corpus.

Selects up to `--n` (default 300, matching config.MAX_PRODUCTION_RECORDS)
records that have COMPLETE ground truth across all 5 fields, so the same
subset can be reused for every field's extraction run. Selection is
deterministic (seed=42, same convention as the other scripts).

Output (under --out-dir, default backend/deploy/):
    corpus/<id>.md              -- just the needed markdown files
    promptlab.db                -- fresh SQLite db with only `records` +
                                    `ground_truth` rows for the selected ids.
                                    `records.md_path` points at the LOCAL
                                    `corpus/` folder above, so you can run the
                                    production rollout (run_extraction.py /
                                    optimize_prompt.py) against this db
                                    locally first, with DEP_DB_PATH pointed at
                                    it. Once you're ready to actually deploy,
                                    run `rewrite_corpus_path_for_deploy.py` to
                                    swap the paths to wherever the deploy
                                    target mounts the corpus, THEN upload.

Usage (from DEP root, .venv active):
    python -m backend.scripts.export_production_subset
    python -m backend.scripts.export_production_subset --n 300 --out-dir backend/deploy
"""
from __future__ import annotations

import argparse
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import config, db  # noqa: E402
from backend.app.fields import FIELDS  # noqa: E402

SEED = 42


def select_complete_case_ids(conn, project_id: int, n: int) -> list[int]:
    field_names = list(FIELDS.keys())
    placeholders = ",".join("?" for _ in field_names)
    rows = conn.execute(
        f"SELECT record_id FROM ground_truth WHERE project_id = ? AND field_name IN ({placeholders}) "
        f"GROUP BY record_id HAVING COUNT(DISTINCT field_name) = ?",
        (project_id, *field_names, len(field_names)),
    ).fetchall()
    ids = [r["record_id"] for r in rows]

    # Only keep ids whose .md file actually exists (belt-and-braces; should
    # already be guaranteed by build_ground_truth.py, but this script may run
    # long after that and files can move/disappear).
    usable = []
    for id_ in ids:
        row = conn.execute(
            "SELECT md_path FROM records WHERE project_id = ? AND id = ?", (project_id, id_)
        ).fetchone()
        if row and Path(row["md_path"]).exists():
            usable.append(id_)

    random.Random(SEED).shuffle(usable)
    return sorted(usable[:n])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="dep-extraction", help="project slug (see backend/app/projects.py)")
    ap.add_argument("--n", type=int, default=config.MAX_PRODUCTION_RECORDS)
    ap.add_argument("--out-dir", type=Path, default=config.BACKEND_DIR / "deploy")
    args = ap.parse_args()

    out_dir = args.out_dir
    corpus_out = out_dir / "corpus"
    corpus_out.mkdir(parents=True, exist_ok=True)
    db_out_path = out_dir / "promptlab.db"
    if db_out_path.exists():
        db_out_path.unlink()

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        ids = select_complete_case_ids(conn, project_id, args.n)
        print(f"Selected {len(ids)} complete-case records (requested {args.n}).")

        records = []
        ground_truth_rows = []
        for id_ in ids:
            rec = conn.execute(
                "SELECT id, title, md_path FROM records WHERE project_id = ? AND id = ?", (project_id, id_)
            ).fetchone()
            records.append(rec)
            gt_rows = conn.execute(
                "SELECT field_name, value_json FROM ground_truth WHERE project_id = ? AND record_id = ?",
                (project_id, id_),
            ).fetchall()
            ground_truth_rows.append((id_, gt_rows))

    # Copy the .md files.
    copied = 0
    for rec in records:
        src = Path(rec["md_path"])
        dst = corpus_out / f"{rec['id']}.md"
        shutil.copyfile(src, dst)
        copied += 1
    print(f"Copied {copied} .md files to {corpus_out}")

    # Build the fresh production db, pointing md_path at the LOCAL corpus_out
    # folder so it's immediately usable for a local production rollout.
    db.init_db(db_out_path)
    with db.get_conn(db_out_path) as out_conn:
        out_project_id = db.get_project_id(out_conn, args.project)
        for rec in records:
            local_md_path = str(corpus_out / f"{rec['id']}.md")
            db.upsert_record(out_conn, out_project_id, rec["id"], rec["title"], local_md_path)
        for id_, gt_rows in ground_truth_rows:
            for gt in gt_rows:
                out_conn.execute(
                    "INSERT INTO ground_truth (project_id, record_id, field_name, value_json) VALUES (?, ?, ?, ?)",
                    (out_project_id, id_, gt["field_name"], gt["value_json"]),
                )

    print(f"Wrote production db to {db_out_path}")
    print(f"\nNext: run the production rollout locally against this db "
          f"(set DEP_DB_PATH={db_out_path}), then run "
          f"rewrite_corpus_path_for_deploy.py before uploading to the cloud target.")


if __name__ == "__main__":
    main()
