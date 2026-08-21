"""sub_sector is multi-valued, per the 3ie extraction protocol.

The protocol (row 29 of the Protocol sheet) says: "Select all sub-sectors that
apply according to the sector indicated in previous column... Multiple answers,
as necessary." We had modelled it as single-valued, which forced a one-of-many
choice onto a genuinely multi-label field: 183 of 7,617 records in the master
database carry two or more sub-sectors.

The 100-record pilot subset happens to contain none, so this was invisible in
the dashboard and would only have surfaced on scaling.
"""
from __future__ import annotations

import pytest

from backend.app import scoring
from backend.app.analytics import gate_metrics
from backend.app.fields import FIELDS
from backend.app.scoring import score_field


class TestFieldShape:
    def test_sub_sector_is_multi_valued(self):
        assert FIELDS["sub_sector"].value_type == "list_categorical"

    def test_sector_name_stays_single(self):
        # The protocol says "Select ONE sector" for the parent field.
        assert FIELDS["sector_name"].value_type == "single_categorical"

    def test_sub_sector_is_gated_on_f1_not_kappa(self):
        metric, threshold = scoring.gate_for("sub_sector")
        assert metric is None            # falls through to the list-field default
        assert threshold == scoring.GATE_THRESHOLD


class TestPipeJoinedTruth:
    """Existing reference data stores multiple sub-sectors as one pipe-joined
    string. Without splitting it is compared as a single value and can never
    match, so a fully correct answer would score zero."""

    def test_pipe_joined_truth_is_split(self):
        assert scoring.as_value_list("Primary education | Secondary education") == [
            "Primary education", "Secondary education"]

    def test_json_list_passes_through(self):
        assert scoring.as_value_list(["Crops", "Livestock"]) == ["Crops", "Livestock"]

    def test_empty_values(self):
        assert scoring.as_value_list(None) == []
        assert scoring.as_value_list("") == []

    def test_both_values_credited(self):
        r = score_field("sub_sector", ["Primary education", "Secondary education"],
                        "Primary education | Secondary education")
        assert r.score == pytest.approx(1.0)

    def test_partial_answer_gets_partial_credit(self):
        # Half the recorded sub-sectors: correct as far as it goes, but not
        # complete. Under the old single-valued model this scored a full 1.0
        # because ANY one of the alternatives counted -- which is what let
        # sub_sector look better than it was.
        r = score_field("sub_sector", ["Primary education"],
                        "Primary education | Secondary education")
        assert 0.0 < r.score < 1.0

    def test_wrong_answer_still_zero(self):
        r = score_field("sub_sector", ["Crops"], "Primary education | Secondary education")
        assert r.score == pytest.approx(0.0)


class TestGateMetric:
    def _rows(self, pairs):
        return [{"predicted": p, "truth": t} for p, t in pairs]

    def test_gate_uses_f1(self):
        gm = gate_metrics("sub_sector", self._rows([(["Crops"], ["Crops"])]))
        assert gm["metric_name"] == "f1"
        assert gm["metric"] == pytest.approx(1.0)

    def test_gate_splits_pipe_joined_truth(self):
        gm = gate_metrics("sub_sector", self._rows([
            (["Water supply", "Sanitation"], "Water supply | Sanitation"),
        ]))
        assert gm["metric"] == pytest.approx(1.0)
        assert gm["recall"] == pytest.approx(1.0)

    def test_missing_second_value_shows_as_recall_loss(self):
        gm = gate_metrics("sub_sector", self._rows([
            (["Water supply"], "Water supply | Sanitation"),
        ]))
        assert gm["precision"] == pytest.approx(1.0)
        assert gm["recall"] == pytest.approx(0.5)
