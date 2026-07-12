"""Tests for backend.app.exemplars — few-shot exemplar serialization and parsing."""
from __future__ import annotations

from backend.app.exemplars import (
    Exemplar,
    parse_exemplars,
    serialize_exemplars,
    merge_exemplars,
    SENTINEL,
)


class TestParseExemplars:
    def test_no_exemplars(self):
        base, exs = parse_exemplars("Classify the sector.")
        assert base == "Classify the sector."
        assert exs == []

    def test_empty_exemplar_block(self):
        template = f"Classify.\n\n{SENTINEL}\n"
        base, exs = parse_exemplars(template)
        assert base == "Classify."
        assert exs == []

    def test_parse_single_exemplar(self):
        template = (
            "Classify by outcome.\n\n"
            f"{SENTINEL}\n"
            'Paper: "A school deworming program"\n'
            "Answer: Health"
        )
        base, exs = parse_exemplars(template)
        assert base == "Classify by outcome."
        assert len(exs) == 1
        assert exs[0].paper == "A school deworming program"
        assert exs[0].answer == "Health"

    def test_parse_multiple_exemplars(self):
        template = (
            "Classify.\n\n"
            f"{SENTINEL}\n"
            'Paper: "Deworming in schools"\n'
            "Answer: Health\n"
            "---\n"
            'Paper: "Cash transfers"\n'
            "Answer: Social protection"
        )
        base, exs = parse_exemplars(template)
        assert base == "Classify."
        assert len(exs) == 2
        assert exs[0].answer == "Health"
        assert exs[1].answer == "Social protection"

    def test_strips_quotes_from_paper(self):
        template = f"Classify.\n{SENTINEL}\nPaper: \"Some paper\"\nAnswer: Health"
        _, exs = parse_exemplars(template)
        assert exs[0].paper == "Some paper"


class TestSerializeExemplars:
    def test_no_exemplars_returns_base(self):
        assert serialize_exemplars("Classify.", []) == "Classify."

    def test_round_trip(self):
        exemplars = [
            Exemplar(paper="Deworming in schools", answer="Health"),
            Exemplar(paper="Cash transfers", answer="Social protection"),
        ]
        template = serialize_exemplars("Classify by outcome.", exemplars)
        assert SENTINEL in template
        base, parsed = parse_exemplars(template)
        assert base == "Classify by outcome."
        assert len(parsed) == 2
        assert parsed[0].paper == "Deworming in schools"
        assert parsed[0].answer == "Health"
        assert parsed[1].paper == "Cash transfers"
        assert parsed[1].answer == "Social protection"


class TestMergeExemplars:
    def test_empty_lists(self):
        assert merge_exemplars([], []) == []

    def test_incumbent_only(self):
        inc = [Exemplar(paper="A", answer="Health")]
        assert merge_exemplars(inc, []) == inc

    def test_proposed_only(self):
        prop = [Exemplar(paper="B", answer="Education")]
        assert merge_exemplars([], prop) == prop

    def test_union_dedup(self):
        inc = [Exemplar(paper="A", answer="Health"), Exemplar(paper="B", answer="Education")]
        prop = [Exemplar(paper="B", answer="Education"), Exemplar(paper="C", answer="Energy and extractives")]
        merged = merge_exemplars(inc, prop)
        assert len(merged) == 3
        assert merged[0].paper == "A"
        assert merged[1].paper == "B"
        assert merged[2].paper == "C"

    def test_cap_at_max(self):
        inc = [Exemplar(paper=f"paper{i}", answer=f"sector{i}") for i in range(4)]
        prop = [Exemplar(paper=f"new{i}", answer=f"sector{i + 10}") for i in range(4)]
        merged = merge_exemplars(inc, prop, max_exemplars=5)
        assert len(merged) == 5
        # Incumbent comes first
        assert merged[0].paper == "paper0"
        assert merged[4].paper == "new0"
