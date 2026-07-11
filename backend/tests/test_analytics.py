"""Tests for backend.app.analytics — aggregate metrics over logged runs.

Covers: _fbeta, _cohens_kappa, categorical confusion matrix, list confusion
(with and without universe_size), compute_confusion dispatch, gate_metrics
for both field types.
"""
from __future__ import annotations

import pytest

from backend.app.analytics import (
    MAX_CATEGORIES,
    _as_list,
    _cohens_kappa,
    _fbeta,
    _list_confusion,
    _match_counts,
    _categorical_confusion,
    compute_confusion,
    gate_metrics,
)


# --------------------------------------------------------------------------- #
# _fbeta
# --------------------------------------------------------------------------- #

class TestFbeta:
    def test_f1_perfect(self):
        assert _fbeta(1.0, 1.0, 1.0) == 1.0

    def test_f1_half(self):
        # precision=0.5, recall=1.0 -> f1 = 2*0.5*1/(0.5+1) = 0.667
        assert _fbeta(0.5, 1.0, 1.0) == pytest.approx(2 / 3, abs=0.01)

    def test_f1_zero_precision(self):
        assert _fbeta(0.0, 1.0, 1.0) == 0.0

    def test_f1_zero_recall(self):
        assert _fbeta(1.0, 0.0, 1.0) == 0.0

    def test_f1_both_zero(self):
        assert _fbeta(0.0, 0.0, 1.0) == 0.0

    def test_f2_recall_weighted(self):
        # F2 weights recall more than precision
        p, r = 0.5, 1.0
        f1 = _fbeta(p, r, 1.0)
        f2 = _fbeta(p, r, 2.0)
        assert f2 > f1  # higher recall benefits F2 more

    def test_f1_denominator_zero(self):
        # precision + recall both zero -> denom = 0 -> returns 0.0
        assert _fbeta(0.0, 0.0, 1.0) == 0.0


# --------------------------------------------------------------------------- #
# _cohens_kappa
# --------------------------------------------------------------------------- #

class TestCohensKappa:
    def test_perfect_agreement(self):
        rows = [
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Education", "truth": "Education"},
        ]
        kappa = _cohens_kappa(rows)
        assert kappa == 1.0

    def test_complete_disagreement(self):
        # Every prediction is wrong -> kappa = -1 (complete disagreement)
        rows = [
            {"predicted": "Health", "truth": "Education"},
            {"predicted": "Education", "truth": "Health"},
        ]
        kappa = _cohens_kappa(rows)
        assert kappa is not None
        assert kappa == pytest.approx(-1.0, abs=0.01)

    def test_random_agreement_near_zero(self):
        # With enough rows and balanced independent predictions, kappa ~ 0
        rows = [
            {"predicted": "Health", "truth": "Education"},
            {"predicted": "Education", "truth": "Health"},
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Education", "truth": "Education"},
        ]
        kappa = _cohens_kappa(rows)
        assert kappa is not None
        assert kappa == pytest.approx(0.0, abs=0.05)

    def test_no_data_returns_none(self):
        assert _cohens_kappa([]) is None

    def test_all_same_truth_skips_none(self):
        # Rows with empty truth are skipped. With only 1 valid row where
        # both pred and truth are "Health", chance agreement = 1 -> kappa = None
        rows = [
            {"predicted": "Health", "truth": ""},
            {"predicted": "Health", "truth": "Health"},
        ]
        kappa = _cohens_kappa(rows)
        assert kappa is None  # single class -> chance agreement = 1 -> undefined

    def test_all_same_class(self):
        # If everyone predicts and is in the same class, kappa is undefined
        # (chance agreement = 1) -> returns None
        rows = [
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Health", "truth": "Health"},
        ]
        kappa = _cohens_kappa(rows)
        assert kappa is None  # pe = 1, 1-pe = 0 -> None


# --------------------------------------------------------------------------- #
# _as_list
# --------------------------------------------------------------------------- #

class TestAsList:
    def test_list_passthrough(self):
        assert _as_list(["a", "b"]) == ["a", "b"]

    def test_scalar(self):
        assert _as_list("hello") == ["hello"]

    def test_none(self):
        assert _as_list(None) == []

    def test_empty_string(self):
        assert _as_list("") == []

    def test_number(self):
        assert _as_list(42) == ["42"]


# --------------------------------------------------------------------------- #
# _match_counts
# --------------------------------------------------------------------------- #

class TestMatchCounts:
    def test_perfect_match(self):
        tp, fp, fn = _match_counts(["a", "b"], ["a", "b"], fuzzy=False)
        assert tp == 2
        assert fp == 0
        assert fn == 0

    def test_partial_match_under_reporting(self):
        # 1 correct of 2 truth, no extras -> tp=1, fp=0, fn=1
        tp, fp, fn = _match_counts(["a"], ["a", "b"], fuzzy=False)
        assert tp == 1
        assert fp == 0
        assert fn == 1

    def test_overprediction(self):
        tp, fp, fn = _match_counts(["a", "c"], ["a", "b"], fuzzy=False)
        assert tp == 1
        assert fp == 1
        assert fn == 1

    def test_no_match(self):
        tp, fp, fn = _match_counts(["x"], ["a"], fuzzy=False)
        assert tp == 0
        assert fp == 1
        assert fn == 1

    def test_empty_pred(self):
        tp, fp, fn = _match_counts([], ["a", "b"], fuzzy=False)
        assert tp == 0
        assert fp == 0
        assert fn == 2

    def test_empty_truth(self):
        tp, fp, fn = _match_counts(["a"], [], fuzzy=False)
        assert tp == 0
        assert fp == 1
        assert fn == 0


# --------------------------------------------------------------------------- #
# _categorical_confusion
# --------------------------------------------------------------------------- #

class TestCategoricalConfusion:
    def test_perfect(self):
        rows = [
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Education", "truth": "Education"},
        ]
        conf = _categorical_confusion(rows)
        assert conf["type"] == "categorical"
        assert conf["accuracy"] == 1.0
        assert conf["n"] == 2

    def test_mixed(self):
        rows = [
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Health", "truth": "Education"},  # wrong
        ]
        conf = _categorical_confusion(rows)
        assert conf["accuracy"] == 0.5
        assert conf["n"] == 2

    def test_skips_empty_truth(self):
        rows = [
            {"predicted": "Health", "truth": ""},
            {"predicted": "Health", "truth": "Health"},
        ]
        conf = _categorical_confusion(rows)
        assert conf["n"] == 1
        assert conf["accuracy"] == 1.0

    def test_none_predicted(self):
        rows = [
            {"predicted": None, "truth": "Health"},
            {"predicted": "Health", "truth": "Health"},
        ]
        conf = _categorical_confusion(rows)
        assert conf["n"] == 2
        assert conf["accuracy"] == 0.5  # one correct, one (none) wrong

    def test_matrix_labels(self):
        rows = [
            {"predicted": "Health", "truth": "Health"},
        ]
        conf = _categorical_confusion(rows)
        # Labels are normalised to lowercase by _norm
        assert "health" in conf["truth_labels"]
        assert "health" in conf["pred_labels"]
        assert "(none)" in conf["pred_labels"]

    def test_max_categories_overflow(self):
        # More categories than MAX_CATEGORIES -> "(other)" bucket
        rows = []
        for i in range(MAX_CATEGORIES + 3):
            rows.append({"predicted": f"Cat{i}", "truth": f"Cat{i}"})
        conf = _categorical_confusion(rows)
        assert "(other)" in conf["truth_labels"]
        assert conf["n"] == len(rows)


# --------------------------------------------------------------------------- #
# _list_confusion
# --------------------------------------------------------------------------- #

class TestListConfusion:
    def test_perfect_no_universe(self):
        rows = [{"predicted": ["a", "b"], "truth": ["a", "b"]}]
        conf = _list_confusion(rows, fuzzy=True, universe_size=None)
        assert conf["type"] == "list"
        assert conf["tp"] == 2
        assert conf["fp"] == 0
        assert conf["fn"] == 0
        assert conf["precision"] == 1.0
        assert conf["recall"] == 1.0
        assert conf["f1"] == 1.0
        assert conf["specificity"] is None  # no universe

    def test_with_extras(self):
        rows = [{"predicted": ["a", "c"], "truth": ["a", "b"]}]
        conf = _list_confusion(rows, fuzzy=True, universe_size=None)
        assert conf["tp"] == 1
        assert conf["fp"] == 1
        assert conf["fn"] == 1
        assert conf["precision"] == pytest.approx(0.5)
        assert conf["recall"] == pytest.approx(0.5)

    def test_with_universe_specificity(self):
        # universe_size=10, truth has 2 items -> 8 negatives
        # FP=1 -> TN = 8 - 1 = 7
        rows = [{"predicted": ["a", "c"], "truth": ["a", "b"]}]
        conf = _list_confusion(rows, fuzzy=False, universe_size=10)
        assert conf["specificity"] is not None
        # specificity = TN / (TN + FP) = 7 / (7 + 1) = 0.875
        assert conf["specificity"] == pytest.approx(0.875, abs=0.01)

    def test_empty_rows(self):
        conf = _list_confusion([], fuzzy=True, universe_size=None)
        assert conf["tp"] == 0
        assert conf["fp"] == 0
        assert conf["fn"] == 0
        assert conf["n"] == 0


# --------------------------------------------------------------------------- #
# compute_confusion
# --------------------------------------------------------------------------- #

class TestComputeConfusion:
    def test_dispatches_categorical(self):
        rows = [{"predicted": "Health", "truth": "Health"}]
        conf = compute_confusion("sector_name", rows)
        assert conf["type"] == "categorical"

    def test_dispatches_list_text(self):
        rows = [{"predicted": ["Smith, J"], "truth": ["Smith, J"]}]
        conf = compute_confusion("authors", rows)
        assert conf["type"] == "list"

    def test_dispatches_list_categorical(self):
        rows = [{"predicted": ["United States"], "truth": ["United States"]}]
        conf = compute_confusion("author_country", rows)
        assert conf["type"] == "list"
        # list_categorical has a taxonomy_key, so universe_size is set
        assert conf["specificity"] is not None


# --------------------------------------------------------------------------- #
# gate_metrics
# --------------------------------------------------------------------------- #

class TestGateMetrics:
    def test_categorical_returns_accuracy(self):
        rows = [
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Education", "truth": "Education"},
        ]
        gm = gate_metrics("sector_name", rows)
        assert gm["metric_name"] == "accuracy"
        assert gm["metric"] == 1.0
        assert gm["accuracy"] == 1.0
        assert gm["f1"] is None
        assert gm["recall"] is None
        assert gm["kappa"] is not None

    def test_categorical_imperfect(self):
        rows = [
            {"predicted": "Health", "truth": "Health"},
            {"predicted": "Health", "truth": "Education"},
        ]
        gm = gate_metrics("sector_name", rows)
        assert gm["metric"] == 0.5

    def test_list_returns_f1(self):
        rows = [{"predicted": ["a", "b"], "truth": ["a", "b"]}]
        gm = gate_metrics("authors", rows)
        assert gm["metric_name"] == "f1"
        assert gm["metric"] == 1.0
        assert gm["f1"] == 1.0
        assert gm["precision"] == 1.0
        assert gm["recall"] == 1.0
        assert gm["accuracy"] is None
        assert gm["kappa"] is None

    def test_list_imperfect(self):
        # 1 correct of 2 truth, no extras -> precision=1.0, recall=0.5, f1=0.667
        rows = [{"predicted": ["a"], "truth": ["a", "b"]}]
        gm = gate_metrics("authors", rows)
        assert gm["metric"] == pytest.approx(2 / 3, abs=0.01)

    def test_list_categorical_returns_f1(self):
        rows = [{"predicted": ["United States"], "truth": ["United States"]}]
        gm = gate_metrics("author_country", rows)
        assert gm["metric_name"] == "f1"
        assert gm["metric"] == 1.0
