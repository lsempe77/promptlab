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

## Why distillation instead of training on ground truth

You have only ~100 human-labelled records per field. Training on those and then scoring against
them is leakage; holding out enough to evaluate honestly leaves too little to train on.
Distillation sidesteps both problems:

- **Training labels** come from the *teacher* (your best model + optimized prompt) run over the
  **unlabelled** corpus — free, abundant, and containing **zero** ground-truth records.
- **Evaluation** stays on the untouched 100 human GT records, scored with the *same*
  `scoring.score_field` / `analytics.gate_metrics` the production gate uses.

The scripts enforce the split: `label_corpus.py` only pulls records with **no** ground truth for
the field, and `build_dataset.py` drops (as a second guard) any labelled record that slips
through. The eval set is the human GT and never touches training.

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

## Run it (from the repo root, venv active)

```bash
# 1. Teacher-label unlabelled corpus (needs OPENROUTER_API_KEY + a DB with corpus).
python -m backend.scripts.distill.label_corpus \
    --field sub_sector --teacher "~anthropic/claude-sonnet-latest" --n 800

# 2. Build filtered, split, chat-format datasets (pure local transform).
python -m backend.scripts.distill.build_dataset \
    --field sub_sector --min-confidence 0.7 --val-frac 0.1

# 3a. Train locally (GPU). Or 3b: upload {train,val}.jsonl to a hosted FT provider.
python -m backend.scripts.distill.train_lora \
    --field sub_sector --base-model Qwen/Qwen2.5-7B-Instruct

# 4. Evaluate teacher vs. student on the human GT with the production gate.
#    Point --base-url/--api-key (or env DISTILL_BASE_URL/DISTILL_API_KEY) at wherever
#    the student is served (local vLLM, Fireworks/Together deployment, or OpenRouter).
python -m backend.scripts.distill.eval_distilled \
    --field sub_sector \
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
| `label_corpus.py`  | teacher labels unlabelled corpus → `raw.jsonl` | `OPENROUTER_API_KEY`, DB+corpus |
| `build_dataset.py` | filter/split → `train.jsonl`,`val.jsonl` (chat format) | local only |
| `train_lora.py`    | LoRA SFT a cheap open base model | GPU + `torch`,`transformers`,`peft`,`trl`,`datasets` |
| `eval_distilled.py`| teacher vs. student on human GT, production gate + cost/CO₂e | served student endpoint |
