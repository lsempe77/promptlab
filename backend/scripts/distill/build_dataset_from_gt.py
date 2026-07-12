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
  * sub_sector is built SINGLE-STEP by default (full grouped hierarchy, no sector
    context) so training and inference match. --sub-sector-context true uses the
    TRUE human sector as context instead (stronger signal, but then eval must
    supply a sector too — see README).

Usage (from repo root; point DEP_DB_PATH/DEP_MD_DIR at the full corpus):
    python -m backend.scripts.distill.build_dataset_from_gt --field sub_sector \
        --val-frac 0.15 --test-frac 0.15
"""
from __future__ import annotations

import argparse
import json
import random

from backend.app import db, prompts
from backend.app.corpus import read_md
from backend.app.fields import FIELDS
from backend.app.prompt_store import get_or_create_baseline

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
    ap.add_argument("--sub-sector-context", choices=["none", "true"], default="none",
                    help="sub_sector only: 'none' = single-step; 'true' = use the human "
                         "sector_name as context (needs a sector at eval time too)")
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

    buckets: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    split_ids: dict[str, list[int]] = {"train": [], "val": [], "test": []}
    skipped = 0
    for rec in records:
        rid = rec["id"]
        try:
            md_text = read_md(rec["md_path"])[: prompts.MAX_CHARS]
        except OSError:
            skipped += 1
            continue
        gt_value = rec["ground_truth"]
        # An empty GT value teaches abstention; keep it (abstention is a valid
        # target) but it carries no positive class signal.
        context_value = sector_gt.get(rid) if use_true_sector else None
        system, user = prompts.build_prompt(
            args.field, rec["title"] or "", md_text, instruction=template,
            context_value=context_value if isinstance(context_value, str) else None,
        )
        meta = {"excerpt": None, "notes": None, "confidence": 1.0}
        assistant = canonical_assistant_json(spec.value_type, gt_value, meta)
        split = split_of[rid]
        buckets[split].append(_chat_example(system, user, assistant))
        split_ids[split].append(rid)

    fd = field_dir(args.field)
    for split in ("train", "val", "test"):
        write_jsonl(fd / f"{split}.jsonl", buckets[split])
    (fd / "splits.json").write_text(json.dumps(split_ids, indent=2), encoding="utf-8")

    print(f"field={args.field}  usable={sum(len(v) for v in buckets.values())}  skipped_md={skipped}")
    print(f"  train={len(buckets['train'])}  val={len(buckets['val'])}  test={len(buckets['test'])}")
    print(f"Wrote {fd}/train.jsonl, val.jsonl, test.jsonl and splits.json")
    print("IMPORTANT: evaluate with  eval_distilled.py --test-ids "
          f"{fd/'splits.json'}  — scoring on train/val rows would be leakage.")


if __name__ == "__main__":
    main()
