"""Step 2 — turn teacher labels into train/val SFT datasets.

Reads data/<field>/raw.jsonl (from label_corpus.py), filters by teacher
confidence, drops duplicates, GUARDS against ground-truth leakage (drops any
record that has GT for the field — belt-and-suspenders on top of label_corpus's
unlabelled-only query), splits deterministically, and writes chat-format
train.jsonl / val.jsonl usable by trl SFTTrainer or a hosted FT provider.

Pure local transform — no API calls. Safe to re-run with different filters.

Usage:
    python -m backend.scripts.distill.build_dataset --field sub_sector \
        --min-confidence 0.7 --val-frac 0.1
"""
from __future__ import annotations

import argparse
import random
from collections import Counter

from backend.app import db
from backend.app.fields import FIELDS

from ._common import field_dir, read_jsonl, setup_utf8, write_jsonl

SEED = 42


def _gt_record_ids(project: str, field: str) -> set[int]:
    """record_ids that DO have ground truth for this field — must never appear
    in training. If the DB is unavailable, return empty (label_corpus already
    excluded them at source)."""
    try:
        with db.get_conn() as conn:
            project_id = db.get_project_id(conn, project)
            rows = conn.execute(
                "SELECT record_id FROM ground_truth WHERE project_id = ? AND field_name = ?",
                (project_id, field),
            ).fetchall()
        return {r["record_id"] for r in rows}
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] could not read GT ids for leakage guard ({exc}); "
              "relying on label_corpus's unlabelled-only query.")
        return set()


def _chat_example(row: dict) -> dict:
    return {"messages": [
        {"role": "system", "content": row["system"]},
        {"role": "user", "content": row["user"]},
        {"role": "assistant", "content": row["assistant"]},
    ]}


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--min-confidence", type=float, default=0.0,
                    help="drop teacher labels below this self-reported confidence")
    ap.add_argument("--min-logprob", type=float, default=0.0,
                    help="drop labels below this mean-token-probability (0 = ignore)")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--max-per-label", type=int, default=0,
                    help="cap examples per categorical label to curb imbalance (0 = no cap)")
    args = ap.parse_args()

    raw = read_jsonl(field_dir(args.field) / "raw.jsonl")
    if not raw:
        raise SystemExit("No raw.jsonl — run label_corpus.py first.")

    gt_ids = _gt_record_ids(args.project, args.field)
    is_categorical = FIELDS[args.field].value_type == "single_categorical"

    kept, seen, dropped = [], set(), Counter()
    per_label: Counter = Counter()
    for row in raw:
        rid = row["record_id"]
        if rid in gt_ids:
            dropped["gt_leak"] += 1
            continue
        if rid in seen:
            dropped["dup"] += 1
            continue
        conf = row.get("confidence")
        if args.min_confidence and (conf is None or conf < args.min_confidence):
            dropped["low_confidence"] += 1
            continue
        lp = row.get("logprob_confidence")
        if args.min_logprob and (lp is None or lp < args.min_logprob):
            dropped["low_logprob"] += 1
            continue
        if is_categorical and args.max_per_label:
            label = str(row["value"])
            if per_label[label] >= args.max_per_label:
                dropped["over_cap"] += 1
                continue
            per_label[label] += 1
        seen.add(rid)
        kept.append(row)

    if not kept:
        raise SystemExit("All examples were filtered out — loosen --min-confidence.")

    rng = random.Random(SEED)
    rng.shuffle(kept)
    n_val = max(1, int(len(kept) * args.val_frac)) if len(kept) > 10 else 0
    val, train = kept[:n_val], kept[n_val:]

    fd = field_dir(args.field)
    write_jsonl(fd / "train.jsonl", [_chat_example(r) for r in train])
    write_jsonl(fd / "val.jsonl", [_chat_example(r) for r in val])

    print(f"kept={len(kept)}  train={len(train)}  val={len(val)}")
    print("dropped: " + (", ".join(f"{k}={v}" for k, v in dropped.items()) or "none"))
    if is_categorical:
        dist = Counter(str(r["value"]) for r in kept)
        print(f"distinct labels={len(dist)}; top 15:")
        for label, c in dist.most_common(15):
            print(f"  {c:4d}  {label}")
    print(f"\nWrote {fd/'train.jsonl'} and {fd/'val.jsonl'} (chat format).")


if __name__ == "__main__":
    main()
