"""Draw a blind double-extraction sample so we can measure HUMAN-vs-HUMAN agreement.

Why this exists
---------------
The project's premise is "extract as well as humans", but every quality bar we
currently enforce (F1 >= 0.90, kappa >= 0.80, sensitivity >= 0.70) is a number
taken from the literature, not from this corpus. Worse, the reference standard
itself is a single curator's labels, and single-pass human extraction is known to
be error-prone -- cleaning 49 ground-truth values earlier lifted *every* model on
*every* field, which measured our answer key rather than the models.

So a model scoring kappa 0.75 against one human might already be at human parity,
or might be far below it. We cannot tell, because nobody has measured what a
second curator would produce on the same papers.

This script draws the sample for that measurement. A second curator fills the
emitted sheet WITHOUT seeing the existing ground truth (hence "blind"), and
`human_agreement.py` then scores curator-2 against curator-1 using the exact same
metric code the model gate uses, so the two numbers are directly comparable.

Sampling is stratified by sector so rare sectors actually appear. An unstratified
draw of 40 records would be dominated by the common sectors -- and would then
report a reassuring agreement figure that says nothing about the rare categories,
which is precisely the blind spot the sensitivity floor exists to cover.

Usage
-----
    python -m backend.scripts.sample_double_extraction --n 40 --out curator2_sheet.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path

from backend.app import db
from backend.app.fields import FIELDS
from backend.app.taxonomy import get_options, load_taxonomy

LIST_SEPARATOR = " | "
STRATIFY_FIELD = "sector_name"


def _sampled_records(conn, project_id: int, n: int, seed: int) -> list[dict]:
    """Records that have ground truth for EVERY field, stratified by sector.

    Requiring all fields keeps one sheet usable for all five agreement figures;
    a record missing one field would silently shrink that field's sample.
    """
    rows = conn.execute(
        "SELECT r.id, r.title, r.md_path, "
        "       (SELECT g2.value_json FROM ground_truth g2 "
        "         WHERE g2.project_id = r.project_id AND g2.record_id = r.id "
        "           AND g2.field_name = ?) AS stratum "
        "FROM records r "
        "WHERE r.project_id = ? "
        "  AND (SELECT COUNT(*) FROM ground_truth g "
        "        WHERE g.project_id = r.project_id AND g.record_id = r.id "
        "          AND g.field_name IN (%s)) = ? "
        "ORDER BY r.id" % ",".join("?" * len(FIELDS)),
        [STRATIFY_FIELD, project_id, *FIELDS.keys(), len(FIELDS)],
    ).fetchall()

    by_stratum: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        stratum = json.loads(r["stratum"]) if r["stratum"] else "(none)"
        by_stratum[str(stratum)].append(
            {"id": r["id"], "title": r["title"] or "", "md_path": r["md_path"]}
        )

    rng = random.Random(seed)
    for bucket in by_stratum.values():
        rng.shuffle(bucket)

    # Round-robin across sectors: guarantees every sector present before any
    # sector contributes a second record.
    picked: list[dict] = []
    order = sorted(by_stratum)
    while len(picked) < n and any(by_stratum[s] for s in order):
        for s in order:
            if by_stratum[s] and len(picked) < n:
                picked.append(by_stratum[s].pop())
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--n", type=int, default=40,
                    help="records to double-extract (30-50 is the usual reliability sample)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="curator2_sheet.csv")
    ap.add_argument("--options-out", default="curator2_taxonomy_options.csv")
    args = ap.parse_args()

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        records = _sampled_records(conn, project_id, args.n, args.seed)

    if not records:
        raise SystemExit("No records have ground truth for all fields — nothing to sample.")

    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["record_id", "title", "md_file", *FIELDS.keys()])
        for r in records:
            w.writerow([r["id"], r["title"], Path(r["md_path"]).name, *([""] * len(FIELDS))])

    # The curator needs the permitted values for the categorical fields; without
    # them any disagreement is confounded with "didn't know the option existed".
    opts = Path(args.options_out)
    with opts.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "allowed_value", "parent_sector"])
        sub_by_sector = load_taxonomy().get("sub_sectors_by_sector", {})
        for name, spec in FIELDS.items():
            if spec.value_type != "single_categorical" or not spec.taxonomy_key:
                continue
            parent_of = {sub: sec for sec, subs in sub_by_sector.items() for sub in subs}
            for value in get_options(spec.taxonomy_key):
                w.writerow([name, value, parent_of.get(value, "")])

    manifest = out.with_suffix(".manifest.json")
    manifest.write_text(json.dumps({
        "project": args.project, "seed": args.seed, "n": len(records),
        "stratified_by": STRATIFY_FIELD, "record_ids": [r["id"] for r in records],
    }, indent=2), encoding="utf-8")

    # The protocol has to travel with the sheet. A curator handed a bare CSV has
    # no way to know the reading must be blind, which is the one rule that makes
    # the measurement valid at all.
    instructions = out.parent / "INSTRUCTIONS.md"
    template = Path(__file__).with_name("curator_instructions.md")
    instructions.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Wrote {out} — {len(records)} records, stratified by {STRATIFY_FIELD}.")
    print(f"Wrote {opts} — allowed values for the categorical fields.")
    print(f"Wrote {manifest} — records sampled, for reproducibility.")
    print(f"Wrote {instructions} — give this to the curator along with the sheet.\n")
    print("Send the curator: INSTRUCTIONS.md, the sheet, the options file, and the papers")
    print("named in the md_file column. The reading MUST be blind — if they see the existing")
    print("values or any model output first, the comparison measures nothing.\n")
    print("Then run:  python -m backend.scripts.human_agreement --sheet " + str(out))


if __name__ == "__main__":
    main()
