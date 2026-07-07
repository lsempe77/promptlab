"""Shared LLM-as-judge helper.

A single source of truth for the semantic "is this predicted value correct?"
judgment, used both by the posterior sweep (`scripts/llm_judge.py`) and by the
optimizer's acceptance test (`optimizer._run_optimization`). Keeping it here
avoids the two drifting apart.
"""
from __future__ import annotations

import json
from typing import Any

from . import gateway, parsing, scoring
from .fields import FIELDS

JUDGE_SYSTEM = (
    "You are a strict but fair data-quality auditor for a research metadata extraction "
    "system. You will be shown a field being extracted from an academic paper, a value an "
    "LLM predicted, and the ground-truth value from a human-curated dataset. Decide whether "
    "the prediction is CORRECT \u2014 i.e. conveys the same real-world information as the ground "
    "truth, allowing for harmless differences in spelling, abbreviation, ordering, or "
    "formatting, but NOT allowing genuinely different entities/values. "
    'Respond with a JSON object: {"correct": true|false, "reasoning": "<one sentence>"}.'
)

# Cross-family judge: to avoid self-preference bias, a model's output is judged
# by a model from a DIFFERENT family. Anthropic outputs are judged by GPT; every
# other family (OpenAI, Google, DeepSeek, xAI, Qwen, ...) is judged by Claude.
# Using explicit versioned model IDs (not ~ aliases) for reliability.
GPT_JUDGE = "openai/gpt-4o"
CLAUDE_JUDGE = "anthropic/claude-sonnet-4-5"


def judge_for(model_id: str) -> str:
    m = model_id.lower()
    is_anthropic = "anthropic" in m or "claude" in m
    return GPT_JUDGE if is_anthropic else CLAUDE_JUDGE


def judge_prompt(field_name: str, predicted: Any, truth: Any) -> str:
    spec = FIELDS[field_name]
    # Fold away accents/mojibake/whitespace noise on both sides so the judge
    # never spends a verdict on a spurious 'López' vs 'Lopez' difference (the
    # string scorer already ignores these; the judge otherwise would not).
    predicted = scoring.fold_value(predicted)
    truth = scoring.fold_value(truth)
    # A single-valued ground truth may list several acceptable answers joined by
    # '|'; predicting any one of them is correct, so tell the judge that.
    if isinstance(truth, str) and "|" in truth:
        alts = scoring.split_alternatives(truth)
        truth_line = (
            "Ground truth value (ANY ONE of these is acceptable): "
            f"{json.dumps(alts, ensure_ascii=False)}"
        )
    else:
        truth_line = f"Ground truth value: {json.dumps(truth, ensure_ascii=False)}"
    return (
        f"Field: {spec.label} ({spec.description})\n"
        f"Predicted value: {json.dumps(predicted, ensure_ascii=False)}\n"
        f"{truth_line}\n\n"
        "Is the predicted value correct?"
    )


def judge_accuracy(
    field_name: str,
    items: list[tuple[Any, Any]],
    judge_model: str,
    max_workers: int = gateway.DEFAULT_MAX_CONCURRENCY,
) -> tuple[float, int]:
    """Judge a batch of (predicted, truth) pairs and return (mean_verdict, n).
    Unparseable/failed judge responses are dropped from the denominator. Returns
    (0.0, 0) if nothing could be judged."""
    if not items:
        return 0.0, 0
    jobs = [
        {
            "model_id": judge_model,
            "system_prompt": JUDGE_SYSTEM,
            "user_prompt": judge_prompt(field_name, predicted, truth),
            "temperature": 0.0,
            "max_tokens": 200,
        }
        for predicted, truth in items
    ]
    results = gateway.call_model_batch(jobs, max_workers=max_workers)
    verdicts: list[float] = []
    for resp in results:
        if isinstance(resp, gateway.GatewayError):
            continue
        try:
            obj = parsing.parse_json_object(resp.content)
            verdicts.append(1.0 if bool(obj["correct"]) else 0.0)
        except Exception:  # noqa: BLE001 - a malformed judge reply just doesn't count
            continue
    if not verdicts:
        return 0.0, 0
    return sum(verdicts) / len(verdicts), len(verdicts)
