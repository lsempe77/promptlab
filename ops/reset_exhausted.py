#!/usr/bin/env python3
"""Reset exhausted optimization state for a field by creating a new accepted
prompt version. This un-exhausts all models so the supervisor can try again.

Run on the Fly machine:
  python /data/reset_exhausted.py --field authors
  python /data/reset_exhausted.py --field sub_sector
"""
import argparse
import sys
sys.path.insert(0, '/app')

from backend.app import db, prompt_store

NOTES = {
    "authors": (
        "reset: fresh optimization pass after run_extraction errored-run fix "
        "and Phase 2 SQLite ground-truth fix"
    ),
    "sub_sector": (
        "reset: fresh optimization pass with new hierarchical options block "
        "(sector-grouped allowed values)"
    ),
    "author_affiliation": (
        "reset: fresh optimization pass"
    ),
}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True)
    ap.add_argument("--project", default="dep-extraction")
    args = ap.parse_args()

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)

        # Get current best accepted instruction
        current = prompt_store.get_or_create_baseline(conn, project_id, args.field, None)
        if not current:
            print(f"ERROR: no baseline found for field={args.field!r}")
            sys.exit(1)

        current_text = current["template"]
        current_version = current["version"]
        print(f"Current best: version={current_version}")
        print(f"Instruction (first 120 chars): {current_text[:120]}...")

        # Add a new accepted version (same text = same guidance, new pvid = clears exhausted)
        new_pv = prompt_store.add_version(
            conn, project_id, args.field,
            instruction=current_text,
            parent_id=current["id"],
            notes=NOTES.get(args.field, "reset: fresh optimization pass"),
            accepted=True,
            model_id=None,
        )
        if new_pv:
            print(f"Created new accepted version={new_pv['version']} (id={new_pv['id']})")
            print("Exhausted state cleared — supervisor will now optimize this field again.")
        else:
            print("ERROR: failed to create new version")
            sys.exit(1)

if __name__ == "__main__":
    main()
