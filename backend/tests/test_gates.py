"""Tests for the per-field quality gate (scoring.FIELD_GATE + analytics.gate_metrics).

Categorical fields are gated on Cohen's kappa rather than raw accuracy, because
accuracy is not comparable across fields with different numbers of classes.
List fields keep element-level F1 at the global threshold.
"""
from __future__ import annotations

import pytest

from backend.app import scoring
from backend.app.analytics import gate_metrics


class TestGateLookup:
    def test_list_field_uses_global_default(self):
        metric, threshold = scoring.gate_for("authors")
        assert metric is None  # no override -> field-type default (F1)
        assert threshold == scoring.GATE_THRESHOLD

    def test_categorical_fields_are_gated_on_kappa(self):
        for field in ("sector_name", "sub_sector"):
            metric, threshold = scoring.gate_for(field)
            assert metric == "kappa"
            assert threshold == 0.80

    def test_unknown_field_falls_back_to_default(self):
        assert scoring.gate_threshold_for("not_a_field") == scoring.GATE_THRESHOLD


class TestGateMetricSelection:
    def _rows(self, pairs):
        return [{"predicted": p, "truth": t} for p, t in pairs]

    def test_categorical_metric_is_kappa_not_accuracy(self):
        # 3/4 correct -> accuracy 0.75, but kappa is lower (chance-corrected).
        gm = gate_metrics("sector_name", self._rows([
            ("Health", "Health"),
            ("Education", "Education"),
            ("Health", "Health"),
            ("Health", "Education"),
        ]))
        assert gm["metric_name"] == "kappa"
        assert gm["accuracy"] == pytest.approx(0.75)
        assert gm["metric"] == gm["kappa"]
        assert gm["metric"] != gm["accuracy"]

    def test_perfect_agreement_gives_kappa_one(self):
        gm = gate_metrics("sub_sector", self._rows([
            ("Crops", "Crops"), ("Health", "Health"), ("Crops", "Crops"), ("Health", "Health"),
        ]))
        assert gm["metric_name"] == "kappa"
        assert gm["metric"] == pytest.approx(1.0)

    def test_list_field_still_gated_on_f1(self):
        gm = gate_metrics("authors", self._rows([
            (["Smith, John"], ["Smith, John"]),
        ]))
        assert gm["metric_name"] == "f1"
        assert gm["metric"] == pytest.approx(1.0)

    def test_accuracy_still_reported_for_categorical(self):
        # Switching the gated metric must not hide the companion numbers.
        gm = gate_metrics("sector_name", self._rows([("Health", "Health"), ("Health", "Education")]))
        assert gm["accuracy"] is not None
        assert gm["kappa"] is not None
        assert gm["n"] == 2
