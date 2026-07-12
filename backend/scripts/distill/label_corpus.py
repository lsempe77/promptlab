"""Step 1 — teacher-label the UNLABELLED corpus for a field.

Runs the teacher (a strong model + its current best accepted prompt) over corpus
records that have NO ground truth for the target field, and writes one rich JSONL
row per successful extraction to data/<field>/raw.jsonl. These teacher outputs
become the distillation training signal; because they contain zero ground-truth
records, the human GT stays a clean, unseen eval set (see README.md).

For `sub_sector` the teacher mirrors production's two-step: it first extracts
`sector_name`, then extracts `sub_sector` with that sector passed as context
(narrowing 66 options -> ~8, the single biggest accuracy lever). Use
--single-step to distil a one-call model over the full grouped hierarchy instead
(cheaper at inference, harder to learn).

Usage (from repo root, venv active):
    python -m backend.scripts.distill.label_corpus --field sub_sector \
        --teacher "~anthropic/claude-sonnet-latest" --n 800
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app import config, db, gateway, prompts
from backend.app.corpus import read_md
from backend.app.fields import FIELDS
from backend.app.parsing import ParseError, parse_field_response
from backend.app.prompt_store import get_or_create_baseline

from ._common import canonical_assistant_json, field_dir, setup_utf8, write_jsonl

DEFAULT_TEACHER = "~anthropic/claude-sonnet-latest"


def unlabelled_records(conn, project_id: int, field_name: str, limit: int) -> list[dict]:
    """Corpus records that have a readable md_path but NO ground truth for this
    field — the distillation training pool. Ordered by id for determinism."""
    rows = conn.execute(
        "SELECT r.id, r.title, r.md_path FROM records r "
        "WHERE r.project_id = ? AND r.id NOT IN ("
        "  SELECT record_id FROM ground_truth WHERE project_id = ? AND field_name = ?"
        ") ORDER BY r.id LIMIT ?",
        (project_id, project_id, field_name, int(limit)),
    ).fetchall()
    return [{"id": r["id"], "title": r["title"], "md_path": r["md_path"]} for r in rows]


def _batch_extract(model_id: str, template: str, field: str, recs: list[dict],
                   md_cache: dict[int, str], contexts: dict[int, str | None],
                   concurrency: int) -> dict[int, tuple]:
    """Run one extraction pass for `field` over `recs`. Returns
    {record_id: (value, meta)} for the successfully parsed ones."""
    jobs, meta = [], []
    for rec in recs:
        md_text = md_cache[rec["id"]]
        system_prompt, user_prompt = prompts.build_prompt(
            field, rec["title"] or "", md_text, instruction=template,
            context_value=contexts.get(rec["id"]),
        )
        jobs.append({"model_id": model_id, "system_prompt": system_prompt,
                     "user_prompt": user_prompt, "logprobs": True})
        meta.append((rec["id"], system_prompt, user_prompt))

    out: dict[int, tuple] = {}
    results = gateway.call_model_batch(jobs, max_workers=concurrency)
    for (rid, sysp, usrp), resp in zip(meta, results):
        if isinstance(resp, gateway.GatewayError):
            print(f"  [error] {model_id} rec {rid}: {resp}")
            continue
        try:
            value, m = parse_field_response(field, resp.content)
        except ParseError as exc:
            print(f"  [parse-error] {model_id} rec {rid}: {exc}")
            continue
        out[rid] = (value, m, sysp, usrp, resp)
    return out


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="dep-extraction")
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--teacher", default=DEFAULT_TEACHER,
                    help="OpenRouter model id used as the teacher (default: %(default)s)")
    ap.add_argument("--n", type=int, default=500, help="max unlabelled records to label")
    ap.add_argument("--concurrency", type=int, default=gateway.DEFAULT_MAX_CONCURRENCY)
    ap.add_argument("--single-step", action="store_true",
                    help="for sub_sector: skip the sector_name context step")
    args = ap.parse_args()

    if not config.OPENROUTER_API_KEY:
        raise SystemExit("OPENROUTER_API_KEY is not set (see backend/.env.example).")

    spec = FIELDS[args.field]
    two_step = args.field == "sub_sector" and not args.single_step

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, args.project)
        recs = unlabelled_records(conn, project_id, args.field, args.n)
        template = get_or_create_baseline(conn, project_id, args.field, model_id=args.teacher)["template"]
        sector_template = (
            get_or_create_baseline(conn, project_id, "sector_name", model_id=args.teacher)["template"]
            if two_step else None
        )

    if not recs:
        raise SystemExit(
            f"No unlabelled records for field={args.field}. On the deploy subset the corpus may be "
            "entirely ground-truthed; point DEP_DB_PATH/DEP_MD_DIR at the full corpus."
        )

    # Cache the exact (truncated) text the model sees; skip unreadable md.
    md_cache: dict[int, str] = {}
    keep: list[dict] = []
    for rec in recs:
        try:
            md_cache[rec["id"]] = read_md(rec["md_path"])[: prompts.MAX_CHARS]
            keep.append(rec)
        except OSError as exc:
            print(f"  [skip] rec {rec['id']}: cannot read md ({exc})")
    recs = keep
    print(f"Field={args.field} teacher={args.teacher} records={len(recs)} "
          f"mode={'two-step' if two_step else 'single'}")

    # Two-step: first get sector_name to use as context for sub_sector.
    contexts: dict[int, str | None] = {}
    if two_step:
        print("Step A: teacher extracting sector_name for context...")
        sec = _batch_extract(args.teacher, sector_template, "sector_name", recs,
                             md_cache, {}, args.concurrency)
        for rid, (val, *_rest) in sec.items():
            contexts[rid] = val  # single_categorical -> str | None
        recs = [r for r in recs if r["id"] in sec]  # only records the teacher could sector
        print(f"  got sector for {len(recs)} records")

    print(f"Step B: teacher extracting {args.field}...")
    labelled = _batch_extract(args.teacher, template, args.field, recs,
                              md_cache, contexts, args.concurrency)

    rows = []
    for rid, (value, meta, sysp, usrp, resp) in labelled.items():
        # Drop empties: a null/[] target teaches abstention, not the mapping we
        # want to distil. (Abstention behaviour is better learned from the prompt.)
        if not value:
            continue
        rows.append({
            "record_id": rid,
            "field": args.field,
            "teacher": args.teacher,
            "context_value": contexts.get(rid),
            "system": sysp,
            "user": usrp,
            "assistant": canonical_assistant_json(spec.value_type, value, meta),
            "value": value,
            "confidence": meta.get("confidence"),
            "logprob_confidence": resp.logprob_confidence,
        })

    out_path = field_dir(args.field) / "raw.jsonl"
    write_jsonl(out_path, rows)
    print(f"\nWrote {len(rows)} teacher-labelled examples -> {out_path}")
    if spec.value_type == "single_categorical":
        from collections import Counter
        dist = Counter(str(r["value"]) for r in rows)
        print("Label distribution (top 15):")
        for label, c in dist.most_common(15):
            print(f"  {c:4d}  {label}")


if __name__ == "__main__":
    main()
