"""Step 3 — LoRA supervised fine-tune of a cheap open base model on the
teacher-labelled chat dataset.

REQUIRES A GPU and the training extras:
    pip install "torch" "transformers>=4.44" "peft>=0.12" "trl>=0.9" "datasets" "accelerate" "bitsandbytes"

These are intentionally NOT in backend/requirements.txt — the deployed API is
CPU-only and read-only. This script is an offline experiment; it imports the
heavy deps lazily so the module still imports (and --help works) without them.

No GPU? The build_dataset.py output is already in the OpenAI/Fireworks chat
format — fine-tune via a hosted provider instead (see README.md) and skip this.

Usage (on a GPU box, repo root, venv with the extras):
    python -m backend.scripts.distill.train_lora --field sub_sector \
        --base-model Qwen/Qwen2.5-7B-Instruct --epochs 3
"""
from __future__ import annotations

import argparse

from backend.app.fields import FIELDS

from ._common import field_dir, setup_utf8


def main() -> None:
    setup_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", required=True, choices=list(FIELDS.keys()))
    ap.add_argument("--base-model", default="Qwen/Qwen2.5-7B-Instruct",
                    help="a small open instruct model to LoRA-tune (default: %(default)s)")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--max-seq-len", type=int, default=8192)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    ap.add_argument("--out", default=None, help="output dir (default out/<field>-lora)")
    args = ap.parse_args()

    fd = field_dir(args.field)
    train_path, val_path = fd / "train.jsonl", fd / "val.jsonl"
    if not train_path.exists():
        raise SystemExit("No train.jsonl — run build_dataset.py first.")
    out_dir = args.out or str(fd.parent.parent / "out" / f"{args.field}-lora")

    # --- lazy heavy imports (guarded so --help works without a GPU box) -------
    try:
        import torch  # noqa: F401
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import AutoModelForCausalLM, AutoTokenizer
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise SystemExit(
            f"Training deps missing ({exc}). Install with:\n"
            '  pip install "torch" "transformers>=4.44" "peft>=0.12" "trl>=0.9" '
            '"datasets" "accelerate" "bitsandbytes"\n'
            "or fine-tune {train,val}.jsonl via a hosted provider (see README.md)."
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    ds = load_dataset("json", data_files={
        "train": str(train_path), "validation": str(val_path)},
    ) if val_path.exists() and val_path.stat().st_size else load_dataset(
        "json", data_files={"train": str(train_path)})

    def format_chat(example):
        # trl SFTTrainer expects a text field; render our {messages:[...]} with
        # the base model's own chat template so the student learns that format.
        return {"text": tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False)}

    ds = ds.map(format_chat, remove_columns=["messages"])

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model, torch_dtype="auto", device_map="auto")

    peft_config = LoraConfig(
        r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    sft_config = SFTConfig(
        output_dir=out_dir,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_seq_length=args.max_seq_len,
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch" if "validation" in ds else "no",
        bf16=True,
        gradient_checkpointing=True,
        dataset_text_field="text",
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=ds["train"],
        eval_dataset=ds.get("validation"),
        peft_config=peft_config,
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(out_dir)
    tokenizer.save_pretrained(out_dir)
    print(f"\nSaved LoRA adapter -> {out_dir}")
    print("Serve it behind an OpenAI-compatible endpoint (e.g. vLLM), then run "
          "eval_distilled.py with --base-url pointing at it.")


if __name__ == "__main__":
    main()
