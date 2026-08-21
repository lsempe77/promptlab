"""Alignment with the 3ie extraction protocol (docs/DEP extraction protocol...xlsx).

The reference data was produced by curators following that protocol. Where our
scoring assumed a convention the protocol does not impose, models were being
marked wrong for conventions the protocol explicitly allows.
"""
from __future__ import annotations

from backend.app.normalize import authors_equal, normalize_value


class TestMissingValueSentinels:
    """The protocol says record a missing value as "Not reported" (country) or
    "999" (institution). The accumulated ground truth actually uses whichever
    synonym the curator reached for, so these must fold together."""

    def test_country_synonyms_are_one_value(self):
        forms = ["Not reported", "Not specified", "Not applicable", "Unspecified", "999"]
        normed = {normalize_value("author_country", f) for f in forms}
        assert len(normed) == 1

    def test_affiliation_synonyms_are_one_value(self):
        forms = ["Not specified", "999", "Not reported", "n/a"]
        normed = {normalize_value("author_affiliation", f) for f in forms}
        assert len(normed) == 1

    def test_parenthetical_commentary_still_counts_as_missing(self):
        # Real ground-truth value: the parenthetical explains an absent value.
        a = normalize_value("author_affiliation", "Not specifies (Independent Consultant/Researcher)")
        b = normalize_value("author_affiliation", "Not reported")
        assert a == b

    def test_real_values_are_untouched(self):
        assert normalize_value("author_country", "Kenya") != normalize_value("author_country", "Not reported")
        assert "kenya" in normalize_value("author_country", "Kenya").lower()


class TestRunOnInitials:
    """Papers print "Wahed, MA"; the reference data has "Wahed, M. A."."""

    def test_runon_initials_match_spaced_initials(self):
        assert authors_equal("Wahed, MA", "Wahed, M. A.")

    def test_order_does_not_matter(self):
        assert authors_equal("Wahed, M. A.", "Wahed, MA")

    def test_short_given_name_is_not_shredded_into_initials(self):
        # "Ana" must not become A.N.A. and match an unrelated A. N. A.
        assert not authors_equal("Silva, Ana", "Silva, A. N. A.")

    def test_runon_that_does_not_line_up_is_rejected(self):
        assert not authors_equal("Wahed, MB", "Wahed, M. A.")

    def test_existing_behaviour_preserved(self):
        # Guards from the original matcher must survive the change.
        assert authors_equal("Black, R. E.", "Black, Robert E.")
        assert not authors_equal("Smith, J.", "Smith, John")   # lone bare initial
        assert not authors_equal("Smith, John", "Smith, Jane")
