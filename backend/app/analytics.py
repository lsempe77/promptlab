"""Aggregate analytics over already-logged runs: a confusion matrix for
single-categorical fields (sector/sub-sector), and micro-averaged
precision/recall/F1/F2 for list fields (authors, institutions, countries),
where a literal confusion matrix isn't meaningful (open-set, multi-label).

Also reports sensitivity/specificity/F2:
- Categorical fields: standard one-vs-rest per class, macro-averaged across
  the visible classes (see `_categorical_confusion`).
- `list_categorical` fields (currently only author_country) have a closed
  vocabulary (the taxonomy), so "negative" is well-defined: specificity is
  computed by treating every taxonomy value not in a record's ground truth as
  a potential negative, and checking how many of those the model avoided
  (see `_list_confusion`'s `universe_size` path).
- `list_text` fields (authors, author_affiliation) are free-text/open-vocab
  \u2014 there is no fixed set of "negatives" to measure specificity against, so
  it's reported as `None` (surfaced as "n/a" in the dashboard).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .fields import FIELDS
from .normalize import authors_equal
from .scoring import FUZZY_MATCH_THRESHOLD, _fuzzy_equal, _norm
from .taxonomy import get_options

MAX_CATEGORIES = 12  # most frequent ground-truth categories shown individually; rest -> "(other)"


def _fbeta(precision: float, recall: float, beta: float) -> float:
    if precision == 0.0 and recall == 0.0:
        return 0.0
    b2 = beta * beta
    denom = b2 * precision + recall
    return (1 + b2) * precision * recall / denom if denom else 0.0


def _cohens_kappa(rows: list[dict[str, Any]]) -> float | None:
    """Chance-corrected agreement between predicted and ground-truth category
    for single-categorical fields. Reported alongside accuracy because raw
    accuracy can look high just from an imbalanced class distribution; kappa
    discounts the agreement expected by chance. Returns None when undefined
    (no data, or chance agreement == 1)."""
    pairs = [(_norm(r["predicted"] or ""), _norm(r["truth"])) for r in rows if r["truth"]]
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for p, t in pairs if p == t) / n
    pred_counts = Counter(p for p, _ in pairs)
    truth_counts = Counter(t for _, t in pairs)
    pe = sum((pred_counts[c] / n) * (truth_counts[c] / n) for c in set(pred_counts) | set(truth_counts))
    return (po - pe) / (1 - pe) if (1 - pe) != 0 else None


def _categorical_confusion(rows: list[dict[str, Any]]) -> dict:
    truth_counts = Counter(_norm(r["truth"]) for r in rows if r["truth"])
    top = [c for c, _ in truth_counts.most_common(MAX_CATEGORIES)]
    top_set = set(top)
    has_overflow = len(truth_counts) > len(top)

    truth_labels = top + (["(other)"] if has_overflow else [])
    pred_labels = top + ["(other)", "(none)"]
    truth_index = {label: i for i, label in enumerate(truth_labels)}
    pred_index = {label: i for i, label in enumerate(pred_labels)}

    def bucket_truth(v: str | None) -> str:
        n = _norm(v) if v else ""
        return n if n in top_set else "(other)"

    def bucket_pred(v: str | None) -> str:
        if not v:
            return "(none)"
        n = _norm(v)
        return n if n in top_set else "(other)"

    matrix = [[0] * len(pred_labels) for _ in truth_labels]
    n_correct = 0
    n_total = 0
    for r in rows:
        if not r["truth"]:
            continue
        ti = truth_index[bucket_truth(r["truth"])]
        pj = pred_index[bucket_pred(r["predicted"])]
        matrix[ti][pj] += 1
        n_total += 1
        if _norm(r["predicted"] or "") == _norm(r["truth"]):
            n_correct += 1

    # One-vs-rest sensitivity/specificity/F2 per visible truth class, then
    # macro-averaged (each class weighted equally, standard multi-class
    # convention) -- computed straight from the matrix above, no extra pass
    # over `rows` needed.
    sensitivities, specificities, f2s = [], [], []
    for c, label in enumerate(truth_labels):
        pj = pred_index[label]
        tp = matrix[c][pj]
        fn = sum(matrix[c]) - tp
        fp = sum(matrix[i][pj] for i in range(len(truth_labels))) - tp
        tn = n_total - tp - fn - fp
        sens = tp / (tp + fn) if (tp + fn) else 0.0
        spec = tn / (tn + fp) if (tn + fp) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        sensitivities.append(sens)
        specificities.append(spec)
        f2s.append(_fbeta(prec, sens, 2.0))

    def macro(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return {
        "type": "categorical",
        "truth_labels": truth_labels,
        "pred_labels": pred_labels,
        "matrix": matrix,
        "accuracy": (n_correct / n_total) if n_total else 0.0,
        "kappa": _cohens_kappa(rows),
        "sensitivity": macro(sensitivities),
        "specificity": macro(specificities),
        "f2": macro(f2s),
        "n": n_total,
    }


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    return [] if not v else [str(v)]


def _match_counts(predicted: list[str], truth: list[str], fuzzy: bool, field_name: str = "") -> tuple[int, int, int]:
    """Greedy matching mirroring scoring._score_list (same thresholds + the
    authors matcher) so the gate F1 equals the per-run scores. Returns (tp, fp, fn)."""
    threshold = 98 if field_name == "authors" else FUZZY_MATCH_THRESHOLD
    matched_truth: set[int] = set()
    matched_pred: set[int] = set()
    for pi, p in enumerate(predicted):
        for ti, t in enumerate(truth):
            if ti in matched_truth:
                continue
            if (
                _norm(p) == _norm(t)
                or (field_name == "authors" and authors_equal(p, t))
                or (fuzzy and _fuzzy_equal(p, t, threshold=threshold))
            ):
                matched_truth.add(ti)
                matched_pred.add(pi)
                break
    tp = len(matched_truth)
    fp = len(predicted) - len(matched_pred)
    fn = len(truth) - len(matched_truth)
    return tp, fp, fn


def _list_confusion(rows: list[dict[str, Any]], fuzzy: bool, universe_size: int | None, field_name: str = "") -> dict:
    tp = fp = fn = 0
    tn = 0
    for r in rows:
        truth_list = _as_list(r["truth"])
        a, b, c = _match_counts(_as_list(r["predicted"]), truth_list, fuzzy, field_name)
        tp += a
        fp += b
        fn += c
        if universe_size is not None:
            # Every taxonomy value not in this record's ground truth is a
            # potential negative; `b` (this record's FP count) is how many of
            # those the model incorrectly predicted, so the rest are TNs.
            negatives = max(0, universe_size - len(set(_norm(t) for t in truth_list)))
            tn += max(0, negatives - b)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    specificity = (tn / (tn + fp)) if (universe_size is not None and (tn + fp)) else None
    return {
        "type": "list",
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "sensitivity": recall,
        "specificity": specificity,
        "f1": _fbeta(precision, recall, 1.0),
        "f2": _fbeta(precision, recall, 2.0),
        "n": len(rows),
    }


def compute_confusion(field_name: str, rows: list[dict[str, Any]]) -> dict:
    """rows: [{"predicted": <parsed value>, "truth": <ground truth value>}, ...]"""
    spec = FIELDS[field_name]
    if spec.value_type == "single_categorical":
        return _categorical_confusion(rows)
    universe_size = len(get_options(spec.taxonomy_key)) if spec.taxonomy_key else None
    return _list_confusion(rows, fuzzy=(spec.value_type == "list_text"), universe_size=universe_size,
                           field_name=field_name)


def gate_metrics(field_name: str, rows: list[dict[str, Any]]) -> dict:
    """The per-(field, model) quality metric the production gate is evaluated on,
    plus its components, computed from logged runs (no LLM judge needed).

    Field-type aware, matching the systematic-review evaluation literature:
      * list fields (authors/affiliation/country) -> element-level micro F1
        (balances precision & recall; the standard for multi-value extraction);
      * single-categorical (sector/sub_sector) -> record-level accuracy, with
        Cohen's kappa reported alongside (chance-corrected).
    `metric` is the number compared against scoring.GATE_THRESHOLD.
    """
    conf = compute_confusion(field_name, rows)
    if conf["type"] == "categorical":
        return {
            "metric_name": "accuracy",
            "metric": conf["accuracy"],
            "accuracy": conf["accuracy"],
            "kappa": conf.get("kappa"),
            "precision": None,
            # recall is None for categorical: the RECALL_FLOOR is a list-field
            # guard only. Macro-sensitivity (averaged over rare classes) is
            # exposed separately so the dashboard can still surface it, but it
            # must NOT feed the gate/optimizer floor.
            "recall": None,
            "sensitivity": conf.get("sensitivity"),
            "f1": None,
            "n": conf["n"],
        }
    return {
        "metric_name": "f1",
        "metric": conf["f1"],
        "accuracy": None,
        "kappa": None,
        "precision": conf["precision"],
        "recall": conf["recall"],
        "f1": conf["f1"],
        "n": conf["n"],
    }
