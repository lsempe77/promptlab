"""Field-type-aware comparison between a model's extracted value and ground
truth. Returns a 0-1 score plus a boolean `is_correct` (score >= threshold)
and a short textual explanation used as optimizer feedback.

Each result is also tagged with an `outcome` (one of the OUTCOME_* labels) and
a separate `honesty_score`. The raw `score`/`is_correct`/accuracy numbers are
left exactly as before (so historical aggregates stay comparable); the
honesty-adjusted score gives partial credit for an *honest abstention* -- the
model returning null/empty ("I don't know") when a value actually existed -- so
the optimizer can be steered to prefer calibrated honesty over a confident
wrong guess. A confident wrong answer and a hallucination (inventing a value
when none existed) get no such credit.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from .fields import FIELDS

FUZZY_MATCH_THRESHOLD = 95  # rapidfuzz 0-100 scale
CORRECT_THRESHOLD = 0.9  # score (0-1) at/above which a run counts as "correct"
GATE_THRESHOLD = 0.95  # per-(field, model) LLM-judged accuracy at/above which a model counts as
                       # production-ready and its staged rollout may advance to more references;
                       # below it the (field, model) is "gated" and the optimizer should improve
                       # the prompt instead of advancing. Single bar applied at every stage.
ABSTENTION_CREDIT = 0.5  # honesty-adjusted credit for an honest abstention (null/empty output)
                         # when a value actually existed -- rewards "I don't know" over a
                         # confident wrong guess. Only affects `honesty_score` (the optimizer's
                         # objective), never the raw `score`/`is_correct`/accuracy numbers.
EXCERPT_MATCH_THRESHOLD = 90  # rapidfuzz partial_ratio (0-100) cutoff for judging that a cited
                              # "verbatim" excerpt actually appears in the source text.
EXCERPT_PENALTY = 0.5  # multiplier applied to `honesty_score` when a model gave a value but cited
                       # an excerpt that could NOT be found in the source (fabricated evidence).
                       # Only affects the honesty score (optimizer objective), not raw accuracy.

# Per-run outcome categories (mutually exclusive):
OUTCOME_HIT = "hit"                       # gave a value that matches truth
OUTCOME_CORRECT_ABSTAIN = "correct_abstain"  # abstained and truth was also empty (good)
OUTCOME_ABSTAIN_MISS = "abstain_miss"    # abstained (or, for lists, under-reported w/o wrong
                                         # extras) when a value existed -- honest miss
OUTCOME_WRONG = "wrong"                   # gave a wrong value when truth existed
OUTCOME_HALLUCINATION = "hallucination"  # invented a value when truth was empty


@dataclass
class ScoreResult:
    score: float
    is_correct: bool
    explanation: str
    outcome: str = OUTCOME_WRONG
    honesty_score: float = 0.0


def _demojibake(s: str) -> str:
    """Best-effort repair of UTF-8-decoded-as-cp1252 mojibake (e.g. 'BaÃ±os' ->
    'Baños', 'SelcÌ§uk' -> 'Selçuk') in the *ground truth* -- the reference
    data mixes correct UTF-8 with mangled values. This is deliberately safe on
    already-correct text: a clean Latin-1 accent (e.g. 'é' = 0xE9) re-encoded to
    cp1252 is an invalid stand-alone UTF-8 byte, so the round-trip raises and we
    keep the original -- only genuine mojibake (valid UTF-8 byte sequences that
    were mis-decoded) round-trips cleanly."""
    if s.isascii():
        return s
    try:
        return s.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    # Repair mojibake in the reference data, then fold away diacritics so a
    # model's correct 'ç'/'ñ' matches a ground-truth value regardless of accent
    # encoding noise. Applied to both sides so the comparison is symmetric.
    s = _strip_accents(_demojibake(s))
    return " ".join(s.strip().lower().split())


def _fuzzy_equal(a: str, b: str, threshold: int = FUZZY_MATCH_THRESHOLD) -> bool:
    return fuzz.token_set_ratio(_norm(a), _norm(b)) >= threshold


def verify_excerpt(excerpt: Any, source_text: str | None) -> bool | None:
    """Whether the model's cited `excerpt` actually appears in `source_text` (a
    lightweight fabricated-evidence check). Returns True/False, or None when
    there is nothing to check (no excerpt cited, or no source text available).

    Tries a normalized substring match first, then a fuzzy partial match, since
    the excerpt is supposed to be verbatim but may pick up minor whitespace /
    OCR / reformatting noise from the Tika-extracted corpus.
    """
    if excerpt is None or not str(excerpt).strip():
        return None
    if not source_text or not str(source_text).strip():
        return None
    e = _norm(str(excerpt))
    src = _norm(str(source_text))
    if not e:
        return None
    if e in src:
        return True
    return fuzz.partial_ratio(e, src) >= EXCERPT_MATCH_THRESHOLD


def _score_single_categorical(predicted: Any, truth: Any) -> ScoreResult:
    pred = "" if predicted is None else str(predicted)
    truth_s = "" if truth is None else str(truth)
    if not truth_s:
        if not pred:
            return ScoreResult(1.0, 1.0 >= CORRECT_THRESHOLD, "no ground truth value; correctly abstained",
                               outcome=OUTCOME_CORRECT_ABSTAIN, honesty_score=1.0)
        return ScoreResult(0.0, 0.0 >= CORRECT_THRESHOLD,
                           f"no ground truth value but predicted {pred!r} (hallucination)",
                           outcome=OUTCOME_HALLUCINATION, honesty_score=0.0)
    if not pred:
        return ScoreResult(0.0, 0.0 >= CORRECT_THRESHOLD, "abstained (returned null) but a value existed",
                           outcome=OUTCOME_ABSTAIN_MISS, honesty_score=ABSTENTION_CREDIT)
    if _norm(pred) == _norm(truth_s):
        return ScoreResult(1.0, 1.0 >= CORRECT_THRESHOLD, "exact match", outcome=OUTCOME_HIT, honesty_score=1.0)
    if _fuzzy_equal(pred, truth_s):
        return ScoreResult(0.9, 0.9 >= CORRECT_THRESHOLD, f"fuzzy match ({pred!r} ~ {truth_s!r})",
                           outcome=OUTCOME_HIT, honesty_score=0.9)
    return ScoreResult(0.0, 0.0 >= CORRECT_THRESHOLD, f"mismatch: predicted {pred!r}, expected {truth_s!r}",
                       outcome=OUTCOME_WRONG, honesty_score=0.0)


def _score_list(predicted: Any, truth: Any, fuzzy: bool) -> ScoreResult:
    pred_list = [str(x) for x in predicted] if isinstance(predicted, list) else ([] if not predicted else [str(predicted)])
    truth_list = [str(x) for x in truth] if isinstance(truth, list) else ([] if not truth else [str(truth)])

    if not truth_list:
        if not pred_list:
            return ScoreResult(1.0, 1.0 >= CORRECT_THRESHOLD, "no ground truth values; correctly abstained",
                               outcome=OUTCOME_CORRECT_ABSTAIN, honesty_score=1.0)
        return ScoreResult(0.0, 0.0 >= CORRECT_THRESHOLD,
                           f"no ground truth values but predicted {pred_list} (hallucination)",
                           outcome=OUTCOME_HALLUCINATION, honesty_score=0.0)

    if not pred_list:
        return ScoreResult(0.0, 0.0 >= CORRECT_THRESHOLD, "abstained (returned empty list) but values existed",
                           outcome=OUTCOME_ABSTAIN_MISS, honesty_score=ABSTENTION_CREDIT)

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

    # Honesty-adjusted score: credit the *missing* truth items (things the model
    # simply didn't report) at ABSTENTION_CREDIT, while the *extra* wrong items
    # (false positives) keep hurting precision at full weight. So a cautious
    # high-precision / low-recall answer (under-reported, invented nothing)
    # scores better than one that padded the list with wrong values. Never
    # drops below the raw f1.
    fn = len(truth_list) - tp
    adjusted_recall = (tp + ABSTENTION_CREDIT * fn) / len(truth_list)
    honesty_f1 = (
        0.0 if (precision + adjusted_recall) == 0
        else 2 * precision * adjusted_recall / (precision + adjusted_recall)
    )
    honesty_score = max(f1, honesty_f1)

    is_correct = f1 >= CORRECT_THRESHOLD
    if is_correct:
        outcome = OUTCOME_HIT
    elif not extra:
        # under-reported only (no wrong extras) -> treat low recall as partial abstention
        outcome = OUTCOME_ABSTAIN_MISS
    else:
        outcome = OUTCOME_WRONG

    return ScoreResult(f1, is_correct, explanation, outcome=outcome, honesty_score=honesty_score)


def score_field(field_name: str, predicted: Any, truth: Any,
                excerpt_verified: bool | None = None) -> ScoreResult:
    spec = FIELDS[field_name]
    if spec.value_type == "single_categorical":
        result = _score_single_categorical(predicted, truth)
    elif spec.value_type == "list_categorical":
        result = _score_list(predicted, truth, fuzzy=False)
    elif spec.value_type == "list_text":
        result = _score_list(predicted, truth, fuzzy=True)
    else:
        raise ValueError(f"Unknown value_type for field {field_name!r}: {spec.value_type}")

    # Fabricated-evidence penalty: if the model gave a value AND cited an
    # excerpt that could not be found in the source, its provenance is
    # untrustworthy, so dock the honesty score (the optimizer's objective).
    # Raw score/is_correct/outcome (correctness vs. truth) are left unchanged.
    if excerpt_verified is False and bool(predicted):
        result.honesty_score *= EXCERPT_PENALTY
        result.explanation += " [cited excerpt not found in source]"
    return result
