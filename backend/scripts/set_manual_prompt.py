"""One-off/maintenance CLI: manually author and accept a new prompt version
for a field, instead of waiting for the optimizer to (re)discover it.

Why this exists: `prompts.BASELINE_INSTRUCTIONS` (sourced from `fields.FIELDS`)
is only ever read once per field, to create the v1 row the very first time
`get_or_create_baseline` runs for that field. Editing a field's description in
`fields.py` after that does NOT change what production extraction uses --
the DB's `best_accepted_prompt_version` (highest-version row with accepted=1)
always wins. So when we hand-improve an instruction (e.g. to better match the
DEP coding protocol) after the field has already been used, we need to insert
it as a new, explicitly-accepted prompt_versions row.

This intentionally bypasses optimizer validation (no minibatch/val-set
comparison) -- it's for cases where a human is confident the instruction is
better (e.g. it now matches the authoritative coding protocol), not for
optimizer-proposed candidates, which should keep going through
`optimize_field`.

Usage (from DEP root, .venv active):
    python -m backend.scripts.set_manual_prompt --field authors --notes "aligned to DEP protocol"
    python -m backend.scripts.set_manual_prompt --field author_country --instruction "..." --notes "..."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db, prompt_store, prompts  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="dep-extraction", help="project slug (see backend/app/projects.py)")
    ap.add_argument("--field", required=True, choices=list(prompts.BASELINE_INSTRUCTIONS.keys()))
    ap.add_argument(
        "--instruction", default=None,
        help="new instruction text; defaults to the field's current fields.py description",
    )
    ap.add_argument("--notes", default="manual: aligned to DEP protocol")
    args = ap.parse_args()

    instruction = args.instruction if args.instruction is not None else prompts.BASELINE_INSTRUCTIONS[args.field]

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        prompt_store.get_or_create_baseline(conn, project_id, args.field)  # ensure v1 exists first
        incumbent = db.best_accepted_prompt_version(conn, project_id, args.field)
        new_row = prompt_store.add_version(
            conn, project_id, args.field, instruction=instruction,
            parent_id=incumbent["id"] if incumbent else None,
            notes=args.notes, accepted=True,
        )

    print(f"Field: {args.field}")
    print(f"New accepted prompt version: v{new_row['version']} (id={new_row['id']})")
    print(f"Instruction:\n{instruction}")


if __name__ == "__main__":
    main()
