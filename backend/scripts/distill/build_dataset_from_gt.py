"""Step 2-GT — build train/val/test datasets directly from HUMAN ground truth.

Use this instead of (or alongside) the teacher-labelled path when you have many
human labels per field. With ~7k labels/field, training on the verified human
answers is a stronger, cleaner signal than teacher pseudo-labels.

Splits the labelled records into train / val / TEST by record_id (deterministic,
seed 42) and writes chat-format {train,val,test}.jsonl plus splits.json (the
record_ids in each split). The TEST split is what eval_distilled.py must score
on — because training now USES ground truth, evaluating on trained rows would be
leakage. Pass eval_distilled.py --test-ids splits.json to enforce the holdout.

Two limitations to know:
  * The JSON contract asks the model for an `excerpt` (the supporting quote), but
    human GT has no excerpt, so targets carry excerpt=null. Training only on this
    teaches the student NOT to cite. To keep citation behaviour, populate excerpts
    from a teacher/existing runs (a documented enhancement) or mix in distilled
    examples that do carry excerpts.
  * sub_sector is a 2nd-level hierarchy UNDER sector, so it is built with the
    human sector as context by DEFAULT (--sub-sector-context true): the prompt
    narrows the ~66 sub-sectors to the ~8 under that sector. eval_distilled.py
    mirrors this (it extracts sector first, then sub_sector with that context).
    Caveat: if the upstream sector prediction is wrong at eval time, the correct
    sub_sector may not even be in the narrowed list — so pair this with a good
    sector model (ideally fine-tune sector too and chain them). Use
    --sub-sector-context none for a flat 66-way single-step model instead.

Usage (from repo root; point DEP_DB_PATH/DEP_MD_DIR at the full corpus):
    python -m backend.scripts.distill.build_dataset_from_gt --field sub_sector \
        --val-frac 0.15 --test-frac 0.15
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from backend.app import db, prompts
from backend.app.corpus import read_md
from backend.app.fields import FIELDS
from backend.app.prompt_store import get_or_create_baseline
from backend.app.scoring import split_alternatives

from ._common import canonical_assistant_json, field_dir, setup_utf8, write_jsonl

SEED = 42


def _chat_example(system: str, user: str, assistant: str) -> dict:
    return {"messages": [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
        {"role": "assistant", "content": assistant},
    ]}


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--val-frac", type=float, default=0.15)
    ap.add_argument("--test-frac", type=float, default=0.15)
    ap.add_argument("--sub-sector-context", choices=["none", "true"], default="true",
                    help="sub_sector only (default 'true'): sub_sector is a 2nd-level hierarchy "
                         "UNDER sector. 'true' puts the human sector_name in the prompt so the "
                         "allowed sub-sectors are narrowed to that sector's ~8 children (matches "
                         "production; the biggest accuracy lever). 'none' = flat 66-way single-step.")
    # Pilot sizing (cheap first run): shrink TRAIN/VAL only; TEST stays full so the
    # gate reading is honest. Applied after the split so no test record leaks in.
    ap.add_argument("--max-per-label", type=int, default=0,
                    help="cap training examples per categorical label (0=off) — curbs the "
                         "majority-class skew (e.g. Health ~39%%) and shrinks a pilot")
    ap.add_argument("--sample", type=int, default=0,
                    help="cap total TRAIN examples (0=off); VAL scaled to ~1/5 of it. For a "
                         "cheap pilot before committing to the full run")
    args = ap.parse_args()

    spec = FIELDS[args.field]
    use_true_sector = args.field == "sub_sector" and args.sub_sector_context == "true"

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        records = db.get_records_with_field(conn, project_id, args.field)
        template = get_or_create_baseline(conn, project_id, args.field, model_id=None)["template"]
        # For true-sector context: map record_id -> human sector_name label.
        sector_gt: dict[int, object] = {}
        if use_true_sector:
            for r in db.get_records_with_field(conn, project_id, "sector_name"):
                sector_gt[r["id"]] = r["ground_truth"]

    if not records:
        raise SystemExit(f"No ground-truth records for field={args.field}.")

    # Deterministic split BY RECORD so train/val/test never share a record.
    ids = sorted(r["id"] for r in records)
    rng = random.Random(SEED)
    rng.shuffle(ids)
    n = len(ids)
    n_test = int(n * args.test_frac)
    n_val = int(n * args.val_frac)
    split_of = {}
    for rid in ids[:n_test]:
        split_of[rid] = "test"
    for rid in ids[n_test:n_test + n_val]:
        split_of[rid] = "val"
    for rid in ids[n_test + n_val:]:
        split_of[rid] = "train"

    # Per split: list of (record_id, target_label, chat_example). target_label is
    # the single-categorical value (for capping) or None for list fields.
    built: dict[str, list[tuple]] = {"train": [], "val": [], "test": []}
    skipped = 0
    for rec in records:
        rid = rec["id"]
        try:
            md_text = read_md(rec["md_path"])[: prompts.MAX_CHARS]
        except OSError:
            skipped += 1
            continue
        gt_value = rec["ground_truth"]
        # A single-categorical GT cell may list several equally-valid answers
        # joined by " | " (curators tagged >1 label). The scorer accepts any one,
        # but as a TRAINING target we must emit a single valid label — teaching
        # the model to output the literal "A | B" string would be wrong. Take the
        # first alternative as the target.
        if spec.value_type == "single_categorical" and isinstance(gt_value, str) and "|" in gt_value:
            gt_value = split_alternatives(gt_value)[0]
        # An empty GT value teaches abstention; keep it (abstention is a valid
        # target) but it carries no positive class signal.
        context_value = sector_gt.get(rid) if use_true_sector else None
        system, user = prompts.build_prompt(
            args.field, rec["title"] or "", md_text, instruction=template,
            context_value=context_value if isinstance(context_value, str) else None,
        )
        meta = {"excerpt": None, "notes": None, "confidence": 1.0}
        assistant = canonical_assistant_json(spec.value_type, gt_value, meta)
        label = gt_value if spec.value_type == "single_categorical" else None
        built[split_of[rid]].append((rid, label, _chat_example(system, user, assistant)))

    def _cap(rows: list[tuple], max_per_label: int, sample: int, seed: int) -> list[tuple]:
        """Shrink a split: at most `max_per_label` per categorical label and at
        most `sample` total. Deterministic. 0 = no limit."""
        if not (max_per_label or sample):
            return rows
        order = list(range(len(rows)))
        random.Random(seed).shuffle(order)
        per: Counter = Counter()
        kept = []
        for i in order:
            rid, label, _ex = rows[i]
            if max_per_label and label is not None:
                if per[label] >= max_per_label:
                    continue
                per[label] += 1
            kept.append(rows[i])
            if sample and len(kept) >= sample:
                break
        return kept

    val_sample = (args.sample // 5) if args.sample else 0
    built["train"] = _cap(built["train"], args.max_per_label, args.sample, SEED)
    built["val"] = _cap(built["val"], args.max_per_label, val_sample, SEED + 1)
    # TEST is never capped — the gate must be read on the full held-out split.

    fd = field_dir(args.field)
    split_ids: dict[str, list[int]] = {}
    for split in ("train", "val", "test"):
        write_jsonl(fd / f"{split}.jsonl", [ex for _rid, _lbl, ex in built[split]])
        split_ids[split] = [rid for rid, _lbl, _ex in built[split]]
    (fd / "splits.json").write_text(json.dumps(split_ids, indent=2), encoding="utf-8")

    pilot = " (PILOT: train/val capped)" if (args.max_per_label or args.sample) else ""
    print(f"field={args.field}  skipped_md={skipped}{pilot}")
    print(f"  train={len(built['train'])}  val={len(built['val'])}  test={len(built['test'])}")
    print(f"Wrote {fd}/train.jsonl, val.jsonl, test.jsonl and splits.json")
    print("IMPORTANT: evaluate with  eval_distilled.py --test-ids "
          f"{fd/'splits.json'}  — scoring on train/val rows would be leakage.")


if __name__ == "__main__":
    main()
