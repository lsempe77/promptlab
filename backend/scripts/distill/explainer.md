# explainer — what this is and what to do next (read me first)

Plain-language runbook for the fine-tuning experiment. If you've been away a while,
start here; the `README.md` has the technical detail.

## What we're trying to do

Get a **small, cheap model** to extract a field as well as the **big expensive model**
does now — at a fraction of the cost and CO₂. We do it by *fine-tuning* (training a
small model on lots of examples) and then testing it against your normal 90% quality
gate. If it passes cheaply, you use it; if not, you've spent very little.

We're starting with the two hardest fields: **`sector_name`** (11 categories) and
**`sub_sector`** (a second level *inside* each sector).

## The 3 steps

1. **Build the dataset** — turn your human ground truth into train / validation / **test** files. (Done.)
2. **Train** — send the train file to a hosted service (Fireworks) that fine-tunes a small model and serves it.
3. **Evaluate** — score the trained model on the **test** file it never saw, next to the big model, on the same gate + cost.

Two rules that must never break:
- **No leakage** — never score on rows the model trained on. The builder splits the data and
  writes `splits.json`; always evaluate with `--test-ids .../splits.json`.
- **Hierarchy** — `sub_sector` only makes sense *within* a `sector`, so the sub_sector model is
  trained and evaluated *with the sector as context*. That's why we fine-tune **both** and chain them.

## Where things stand right now

- ✅ Step 1 done. Pilot datasets built (small, cheap first pass) for both fields, in
  `backend/scripts/distill/data/<field>/` (these files are gitignored — they hold paper text + names):
  - `sector_name`: train 1,457 / val 300 / test 1,143
  - `sub_sector`:  train 1,500 / val 300 / test 1,142
- ⏳ Steps 2 & 3 are yours to run — they need a **Fireworks account + `FIREWORKS_API_KEY`** and cost money.

## Before you spend

- Make a **Fireworks** account, create an API key, and turn ON "don't train on my data / retention off"
  (the data has paper text + author names).
- Cost for the pilot is small (~9–11M trained tokens/field at 2 epochs — a few dollars on Fireworks).
  The big cost later is *inference*, which is exactly why we picked Fireworks (serverless LoRA runs at
  the small model's cheap token rate).

## API keys — where they go

**Never put a key in the code or in git.** Set them as environment variables in the shell
*before* you run the commands. (`.env` files here are already gitignored.)

| key | used by | get it from |
|-----|---------|-------------|
| `FIREWORKS_API_KEY` | `firectl` (training/deploy) **and** the eval step (`--api-key`) | Fireworks dashboard → API keys |
| `OPENROUTER_API_KEY` | the teacher model + existing pipeline | already set in `backend/.env` |
| account id (not secret) | `submit_fireworks --account <id>` (or `FIREWORKS_ACCOUNT_ID`) | Fireworks dashboard |

Set it for your shell session (do this in the same terminal you run the commands in):

```powershell
# Windows PowerShell
$env:FIREWORKS_API_KEY = "fw_xxx"
```
```bash
# macOS/Linux/Git-Bash
export FIREWORKS_API_KEY=fw_xxx
```

To persist it (optional), add a line `FIREWORKS_API_KEY=fw_xxx` to `backend/.env`. Note: that file
is auto-loaded only when the Python app runs — `firectl` reads the **shell** env var, so for the
training/deploy steps you still need the `export`/`$env:` above. The eval step's `--api-key
$FIREWORKS_API_KEY` also reads the shell env var, so setting it once in the shell covers everything.

## Do this (copy-paste, from the repo root)

Run `sector_name` first (simpler), confirm it passes, then `sub_sector`.

```bash
# --- SECTOR_NAME ---
# 1. (already done, re-run to regenerate)
python -m backend.scripts.distill.build_dataset_from_gt --field sector_name --max-per-label 150 --sample 1500

# 2. print the training playbook, then run the firectl commands it prints
python -m backend.scripts.distill.submit_fireworks --field sector_name --account <your-fw-account>
#    (firectl: upload dataset -> LoRA fine-tune -> deploy. Verify commands against `firectl --help`.)

# 3. evaluate the tuned STUDENT on the held-out test split
python -m backend.scripts.distill.eval_distilled --field sector_name \
    --base-url https://api.fireworks.ai/inference/v1 --api-key $FIREWORKS_API_KEY \
    --test-ids backend/scripts/distill/data/sector_name/splits.json \
    --models 'accounts/<your-fw-account>/models/dep-sector-name'
#    ...and the TEACHER on the SAME split for comparison (OpenRouter, default endpoint):
python -m backend.scripts.distill.eval_distilled --field sector_name \
    --test-ids backend/scripts/distill/data/sector_name/splits.json \
    --models '~anthropic/claude-sonnet-latest'

# --- SUB_SECTOR --- (same 3 steps, swap the field name)
python -m backend.scripts.distill.build_dataset_from_gt --field sub_sector --max-per-label 60 --sample 1500
python -m backend.scripts.distill.submit_fireworks --field sub_sector --account <your-fw-account>
# then eval as above with --field sub_sector and the sub_sector splits.json
```

## How to read the result

`eval_distilled.py` prints one row per model:
- **metric / value** — accuracy for these categorical fields; **pass = YES** means ≥ 0.90 (your gate).
- **$/rec** and **gCO2e/rec** — cost and carbon per paper.

**Win** = the small student shows `pass=YES` with clearly lower `$/rec` and `gCO2e/rec` than the teacher.
Then take it to a human decision before it ever goes into `models.yaml`. **Not a win** = keep the current
big-model + prompt approach; you spent a few dollars to find out.

## If you want to change scope

- **Full run instead of pilot**: drop `--max-per-label`/`--sample` from step 1 (bigger, better, costlier — ~$140 on OpenAI; less on Fireworks).
- **Other fields** (authors / affiliation / country): `build_dataset_from_gt --field <name>` (they're list fields, no hierarchy).
- **OpenAI instead of Fireworks**: use `submit_openai.py` (simplest, auto-served, but pricier inference).
- **Own GPU instead of a provider**: use `train_lora.py`.

## The golden rule

The fine-tuned model is treated like a **code change**, not an automatic prompt tweak: it is
**human-reviewed before it can enter production**. The autonomous supervisor never trains or promotes
a model on its own. Keep it that way.

## File map

| file | what it's for |
|------|---------------|
| `explainer.md` | this runbook |
| `README.md` | technical detail on every script + the two data paths |
| `build_dataset_from_gt.py` | step 1 — human GT → train/val/test |
| `submit_fireworks.py` | step 2 — validate + print the Fireworks training playbook (recommended) |
| `submit_openai.py` | step 2 alternative — fine-tune on OpenAI |
| `train_lora.py` | step 2 alternative — train on your own GPU |
| `eval_distilled.py` | step 3 — score student vs teacher on the gate + cost |
| `label_corpus.py`, `build_dataset.py` | the *distillation* path, for when you have few labels (not needed now — you have ~7k) |
| `data/<field>/` | generated datasets (gitignored: contains paper text + names) |
