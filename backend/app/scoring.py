"""Field-type-aware comparison between a model's extracted value and ground
truth. Returns a 0-1 score plus a boolean `is_correct` (score >= threshold)
and a short textual explanation used as optimizer feedback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from .fields import FIELDS

FUZZY_MATCH_THRESHOLD = 95  # rapidfuzz 0-100 scale
CORRECT_THRESHOLD = 0.9  # score (0-1) at/above which a run counts as "correct"


@dataclass
class ScoreResult:
    score: float
    is_correct: bool
    explanation: str


def _norm(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _fuzzy_equal(a: str, b: str, threshold: int = FUZZY_MATCH_THRESHOLD) -> bool:
    return fuzz.token_set_ratio(_norm(a), _norm(b)) >= threshold


def _score_single_categorical(predicted: Any, truth: Any) -> ScoreResult:
    pred = "" if predicted is None else str(predicted)
    truth_s = "" if truth is None else str(truth)
    if not truth_s:
        score = 1.0 if not pred else 0.0
        return ScoreResult(score, score >= CORRECT_THRESHOLD, "no ground truth value")
    if _norm(pred) == _norm(truth_s):
        return ScoreResult(1.0, 1.0 >= CORRECT_THRESHOLD, "exact match")
    if _fuzzy_equal(pred, truth_s):
        return ScoreResult(0.9, 0.9 >= CORRECT_THRESHOLD, f"fuzzy match ({pred!r} ~ {truth_s!r})")
    return ScoreResult(0.0, 0.0 >= CORRECT_THRESHOLD, f"mismatch: predicted {pred!r}, expected {truth_s!r}")


def _score_list(predicted: Any, truth: Any, fuzzy: bool) -> ScoreResult:
    pred_list = [str(x) for x in predicted] if isinstance(predicted, list) else ([] if not predicted else [str(predicted)])
    truth_list = [str(x) for x in truth] if isinstance(truth, list) else ([] if not truth else [str(truth)])

    if not truth_list:
        score = 1.0 if not pred_list else 0.0
        return ScoreResult(score, score == 1.0, "no ground truth values")

    matched_truth: set[int] = set()
    matched_pred: set[int] = set()
    for pi, p in enumerate(pred_list):
        for ti, t in enumerate(truth_list):
            if ti in matched_truth:
                continue
            same = _norm(p) == _norm(t) or (fuzzy and _fuzzy_equal(p, t))
            if same:
                matched_truth.add(ti)
                matched_pred.add(pi)
                break

    tp = len(matched_truth)
    precision = tp / len(pred_list) if pred_list else 0.0
    recall = tp / len(truth_list) if truth_list else 0.0
    f1 = 0.0 if (precision + recall) == 0 else 2 * precision * recall / (precision + recall)

    missing = [t for i, t in enumerate(truth_list) if i not in matched_truth]
    extra = [p for i, p in enumerate(pred_list) if i not in matched_pred]
    explanation = f"precision={precision:.2f} recall={recall:.2f} f1={f1:.2f}"
    if missing:
        explanation += f"; missing={missing}"
    if extra:
        explanation += f"; extra={extra}"

    return ScoreResult(f1, f1 >= CORRECT_THRESHOLD, explanation)


def score_field(field_name: str, predicted: Any, truth: Any) -> ScoreResult:
    spec = FIELDS[field_name]
    if spec.value_type == "single_categorical":
        return _score_single_categorical(predicted, truth)
    if spec.value_type == "list_categorical":
        return _score_list(predicted, truth, fuzzy=False)
    if spec.value_type == "list_text":
        return _score_list(predicted, truth, fuzzy=True)
    raise ValueError(f"Unknown value_type for field {field_name!r}: {spec.value_type}")
