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
        # sub_sector is deliberately absent: the protocol makes it multi-valued,
        # so it is gated on F1 like the other list fields.
        metric, threshold = scoring.gate_for("sector_name")
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
        gm = gate_metrics("sector_name", self._rows([
            ("Health", "Health"), ("Education", "Education"),
            ("Health", "Health"), ("Education", "Education"),
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


class TestSafetyFloor:
    """The miss-rate floor: passing the headline gate must not be enough when a
    model buys that score by dropping values (lists) or abandoning rare classes."""

    def test_list_field_below_recall_floor_fails(self):
        assert scoring.safety_floor_ok({"recall": scoring.RECALL_FLOOR - 0.01}) is False

    def test_list_field_at_recall_floor_passes(self):
        assert scoring.safety_floor_ok({"recall": scoring.RECALL_FLOOR}) is True

    def test_categorical_below_sensitivity_floor_fails(self):
        gm = {"recall": None, "sensitivity_gateable": True,
              "sensitivity": scoring.SENSITIVITY_FLOOR - 0.01}
        assert scoring.safety_floor_ok(gm) is False

    def test_categorical_at_sensitivity_floor_passes(self):
        gm = {"recall": None, "sensitivity_gateable": True,
              "sensitivity": scoring.SENSITIVITY_FLOOR}
        assert scoring.safety_floor_ok(gm) is True

    def test_insufficient_data_never_fails_a_model(self):
        # Not gateable means "can't judge yet", which must not read as "failed".
        gm = {"recall": None, "sensitivity_gateable": False, "sensitivity": 0.1}
        assert scoring.safety_floor_ok(gm) is True
        assert scoring.safety_floor_ok({}) is True


class TestSupportedSensitivity:
    def _rows(self, pairs):
        return [{"predicted": p, "truth": t} for p, t in pairs]

    def test_undersampled_classes_withhold_the_floor(self):
        # Three classes with a single example each: per-class sensitivity can only
        # be 0 or 1, so the floor must not be enforced on that noise.
        gm = gate_metrics("sector_name", self._rows([
            ("Health", "Health"), ("Education", "Education"), ("Energy", "Energy"),
        ]))
        assert gm["sensitivity_gateable"] is False
        assert gm["n_classes_undersampled"] == 3
        assert gm["sensitivity"] is not None  # still reported for the dashboard
        assert scoring.safety_floor_ok(gm) is True

    def test_well_sampled_classes_are_gateable(self):
        pairs = [("Health", "Health")] * 5 + [("Education", "Education")] * 5 + \
                [("Energy", "Energy")] * 5
        gm = gate_metrics("sector_name", self._rows(pairs))
        assert gm["sensitivity_gateable"] is True
        assert gm["n_classes_undersampled"] == 0
        assert gm["sensitivity"] == pytest.approx(1.0)

    def test_collapsing_onto_the_common_class_is_caught(self):
        # 20 Health + 5 Education, model always answers Health: accuracy 0.80
        # looks acceptable, but it never once finds Education.
        pairs = [("Health", "Health")] * 20 + [("Health", "Education")] * 5
        gm = gate_metrics("sector_name", self._rows(pairs))
        assert gm["accuracy"] == pytest.approx(0.80)
        assert gm["sensitivity_gateable"] is True
        assert gm["sensitivity"] == pytest.approx(0.5)  # 1.0 on Health, 0.0 on Education
        assert scoring.safety_floor_ok(gm) is False

    def test_rare_class_is_not_dropped_from_the_average(self):
        # The guard must not be implemented by averaging over well-sampled classes
        # only -- that would exclude the rare class and hide the collapse.
        pairs = [("Health", "Health")] * 20 + [("Health", "Education")] * 5
        gm = gate_metrics("sector_name", self._rows(pairs))
        assert gm["sensitivity"] < gm["accuracy"]
