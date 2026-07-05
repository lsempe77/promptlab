"""One-off step: rewrite `records.md_path` in the production db (built by
export_production_subset.py) from the local testing path to wherever the
deploy target (e.g. a Fly.io volume) will actually mount the corpus.

Run this ONCE, after you've finished the local production rollout and are
ready to upload the db + corpus to the cloud target -- not before, since
run_extraction.py / optimize_prompt.py need the LOCAL path to actually read
the files while you're still testing on this machine.

Usage (from DEP root, .venv active):
    python -m backend.scripts.rewrite_corpus_path_for_deploy --db backend/deploy/promptlab.db --target-dir /data/corpus
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, required=True, help="path to the production db to rewrite in place")
    ap.add_argument("--target-dir", type=str, default="/data/corpus", help="corpus path on the deploy target")
    args = ap.parse_args()

    target_dir = args.target_dir.rstrip("/")
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id FROM records").fetchall()
    for r in rows:
        conn.execute(
            "UPDATE records SET md_path = ? WHERE id = ?",
            (f"{target_dir}/{r['id']}.md", r["id"]),
        )
    conn.commit()
    conn.close()
    print(f"Rewrote md_path for {len(rows)} records in {args.db} to point at {target_dir}/<id>.md")


if __name__ == "__main__":
    main()
