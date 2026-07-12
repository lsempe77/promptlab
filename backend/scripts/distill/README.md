# Distillation experiment — cheap student model for a single field

**Goal:** test whether a small, cheap model *fine-tuned by distillation* can match the
production **gate** on a target field (start with `sub_sector`, the hardest categorical) at a
fraction of the cost / CO₂e of the large prompted teacher — a new point on the cost-vs-quality
frontier, not a metric game.

This is an **offline, human-gated experiment**. It is deliberately **not** wired into the
autonomous supervisor. A fine-tuned model is a *weight artifact*, so — exactly like an
eval-logic/code change — it must go through **human review before it ever enters `models.yaml`**.
The autonomous loop only ever moves prompts; it must never mint or promote a fine-tuned model on
its own (that would hand the reward-hacking loop a weight-space surface to overfit the answer key).

## Two training paths — pick by how many human labels you have

| Human labels / field | Recommended path | Build step |
|----------------------|------------------|------------|
| ~100 | **Distillation** — too few humans to train on directly | `build_dataset.py` (teacher labels the unlabelled corpus) |
| a few hundred | Ground truth, optionally + distilled examples | `build_dataset_from_gt.py` (+ `build_dataset.py`) |
| **1,000+ (this repo has ~7k/field)** | **Direct fine-tune on human ground truth** — strongest, cleanest signal | `build_dataset_from_gt.py` |

**Ground-truth path (recommended here).** Human answers beat a big model's guesses, so with
thousands of labels you train directly on them. The only rule is **no leakage**: you must not score
on records you trained on. `build_dataset_from_gt.py` splits records into **train / val / test**
(70/15/15, seeded) and writes `splits.json`; `eval_distilled.py --test-ids splits.json` scores only
the held-out **test** split with the same `scoring.score_field` / `analytics.gate_metrics` as the
production gate.

**Distillation path (for scarce labels).** The teacher (your best model + optimized prompt) labels
the **unlabelled** corpus — free, abundant, and containing **zero** ground-truth records — so the
whole human GT stays a clean eval set. `label_corpus.py` only pulls records with no GT, and
`build_dataset.py` drops (second guard) any labelled record that slips through.

You can also **combine**: fine-tune on the human train split *plus* distilled examples for more
coverage on the hard fields.

## Pipeline

```
label_corpus.py     teacher (big model + best prompt) labels N unlabelled corpus records
      │                → data/<field>/raw.jsonl   (one rich record per line)
      ▼
build_dataset.py    filter by teacher confidence, dedup, exclude GT, split train/val
      │                → data/<field>/{train,val}.jsonl   (chat-format for SFT/hosted FT)
      ▼
train_lora.py       LoRA-SFT a cheap open base model on train.jsonl   (GPU required)
      │                → out/<field>-lora/   (or use a hosted FT provider — see below)
      ▼
eval_distilled.py   run teacher AND student over the 100 human GT, score with the
                    production gate, print gate pass/fail + cost + CO₂e side by side
```

## Run it — ground-truth path (recommended, ~7k labels/field)

```bash
# 1. Build train/val/test from human GT (point DEP_DB_PATH/DEP_MD_DIR at the full corpus).
python -m backend.scripts.distill.build_dataset_from_gt --field sub_sector

# 2. Train (GPU) — or upload {train,val}.jsonl to a hosted FT provider.
python -m backend.scripts.distill.train_lora \
    --field sub_sector --base-model Qwen/Qwen2.5-7B-Instruct

# 3. Evaluate ONLY on the held-out test split (leakage-safe) vs. the teacher.
python -m backend.scripts.distill.eval_distilled --field sub_sector \
    --test-ids backend/scripts/distill/data/sub_sector/splits.json \
    --models "~anthropic/claude-sonnet-latest,my-distilled-sub-sector"
```

## Run it — distillation path (scarce labels)

```bash
# 1. Teacher-label unlabelled corpus (needs OPENROUTER_API_KEY + a DB with corpus).
python -m backend.scripts.distill.label_corpus \
    --field sub_sector --teacher "~anthropic/claude-sonnet-latest" --n 800

# 2. Build filtered, split, chat-format datasets (pure local transform).
python -m backend.scripts.distill.build_dataset \
    --field sub_sector --min-confidence 0.7 --val-frac 0.1

# 3. Train (as above). 4. Evaluate on the whole human GT (no --test-ids needed —
#    training used no GT, so the entire GT is a clean holdout):
python -m backend.scripts.distill.eval_distilled --field sub_sector \
    --models "~anthropic/claude-sonnet-latest,my-distilled-sub-sector"
```

## Hosted fine-tuning (no local GPU)

`train_lora.py` needs a GPU. If you don't have one, the `{train,val}.jsonl` files are already in
the OpenAI/Fireworks **chat** format, so you can fine-tune via a hosted API instead:

- **OpenAI** (`gpt-mini` family) — `client.fine_tuning.jobs.create(...)`; serve the returned model
  id through OpenAI's endpoint.
- **Fireworks / Together** (open weights: Qwen, Llama, Mistral) — upload the JSONL, launch a LoRA
  job, deploy the adapter to an OpenAI-compatible endpoint.

Either way, `eval_distilled.py` reaches the model through the same OpenAI-compatible call the rest
of the codebase uses — just override the base URL / key.

## Success criterion

The student **passes** if, on the 100 human GT:
- categorical (`sector_name`/`sub_sector`): `accuracy ≥ scoring.GATE_THRESHOLD` (0.90);
- list fields: `f1 ≥ 0.90 AND recall ≥ scoring.RECALL_FLOOR` (0.85);

**and** its cost/record and CO₂e/record are materially below the teacher's. If it passes, expand to
`sector_name`; if it doesn't, you've spent little and learned the prompt path is sufficient.

## Files

| file | what it does | needs |
|------|--------------|-------|
| `build_dataset_from_gt.py` | **human GT** → `train/val/test.jsonl` + `splits.json` (recommended) | DB+corpus |
| `label_corpus.py`  | teacher labels unlabelled corpus → `raw.jsonl` (scarce-label path) | `OPENROUTER_API_KEY`, DB+corpus |
| `build_dataset.py` | filter/split teacher labels → `train/val.jsonl` (chat format) | local only |
| `train_lora.py`    | LoRA SFT a cheap open base model on `train.jsonl` | GPU + `torch`,`transformers`,`peft`,`trl`,`datasets` |
| `eval_distilled.py`| teacher vs. student on the gate + cost/CO₂e; `--test-ids` for leakage-safe holdout | served student endpoint |
