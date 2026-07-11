"""Tests for backend.app.normalize — name canonicalization before scoring.

Covers: country alias canonicalization (USA, UK, etc.), institution
abbreviation expansion (MIT, WHO, etc.), and the bug fixes for:
  * "Cabo Verde" typo (was "Cabono" — disabled Cape Verde alias)
  * Bare "Congo" ambiguity (was asymmetric false positive)
  * "University of {org}" false matches (stem-rewriting was unscoped)
"""
from __future__ import annotations

import pytest

from backend.app.normalize import (
    _country_canonical_map,
    _is_university_alias,
    _norm_key,
    normalize_country,
    normalize_institution,
    normalize_value,
)


# --------------------------------------------------------------------------- #
# _norm_key
# --------------------------------------------------------------------------- #

class TestNormKey:
    def test_lowercase(self):
        assert _norm_key("HELLO") == "hello"

    def test_punctuation_to_space(self):
        assert _norm_key("Cote d'Ivoire") == "cote d ivoire"

    def test_collapse_whitespace(self):
        assert _norm_key("  extra   spaces  ") == "extra spaces"

    def test_empty(self):
        assert _norm_key("") == ""


# --------------------------------------------------------------------------- #
# normalize_country
# --------------------------------------------------------------------------- #

class TestNormalizeCountry:
    def test_exact_taxonomy_name(self):
        assert normalize_country("United States") == "United States"

    def test_usa_alias(self):
        assert normalize_country("USA") == "United States"

    def test_us_alias(self):
        assert normalize_country("US") == "United States"

    def test_uk_alias(self):
        assert normalize_country("UK") == "United Kingdom"

    def test_britain_alias(self):
        assert normalize_country("Britain") == "United Kingdom"

    def test_russia_alias(self):
        assert normalize_country("Russia") == "Russian Federation"

    def test_south_korea_alias(self):
        assert normalize_country("South Korea") == "Korea, Rep."

    def test_czechia_alias(self):
        assert normalize_country("Czechia") == "Czech Republic"

    def test_slovakia_alias(self):
        assert normalize_country("Slovakia") == "Slovak Republic"

    def test_swaziland_alias(self):
        assert normalize_country("Swaziland") == "Eswatini"

    def test_cape_verde_alias(self):
        # Bug fix: "Cabono" typo was blocking this — now "Cabo Verde" is the key
        assert normalize_country("Cape Verde") == "Cabo Verde"

    def test_cabo_verde_passthrough(self):
        assert normalize_country("Cabo Verde") == "Cabo Verde"

    def test_unknown_country_passthrough(self):
        assert normalize_country("Atlantis") == "Atlantis"

    def test_empty_string(self):
        assert normalize_country("") == ""

    def test_strips_comma_suffix(self):
        # "Egypt, Arab Rep." -> short alias "egypt"
        assert normalize_country("Egypt") == "Egypt, Arab Rep."

    def test_yemen_strips_suffix(self):
        assert normalize_country("Yemen") == "Yemen, Rep."


# --------------------------------------------------------------------------- #
# Bug fix: bare "Congo" ambiguity
# --------------------------------------------------------------------------- #

class TestCongoAmbiguity:
    def test_bare_congo_does_not_false_match_drc(self):
        # Bug: bare "Congo" was mapping to "Congo, Dem. Rep." via setdefault,
        # creating an asymmetric false positive. After fix, "Congo" should
        # NOT canonicalize to either Congo — it falls through to fuzzy matching.
        result = normalize_country("Congo")
        # Should NOT be "Congo, Dem. Rep." (the bug) — should pass through
        assert result != "Congo, Dem. Rep."
        assert result != "Congo, Rep."
        # Should pass through unchanged (ambiguous, no canonical resolution)
        assert result == "Congo"

    def test_drc_full_aliases_still_work(self):
        assert normalize_country("DRC") == "Congo, Dem. Rep."
        assert normalize_country("Zaire") == "Congo, Dem. Rep."
        assert normalize_country("Democratic Republic of Congo") == "Congo, Dem. Rep."

    def test_republic_of_congo_alias_still_works(self):
        assert normalize_country("Republic of Congo") == "Congo, Rep."

    def test_full_taxonomy_names_pass_through(self):
        assert normalize_country("Congo, Dem. Rep.") == "Congo, Dem. Rep."
        assert normalize_country("Congo, Rep.") == "Congo, Rep."


# --------------------------------------------------------------------------- #
# normalize_institution
# --------------------------------------------------------------------------- #

class TestNormalizeInstitution:
    def test_mit_alias(self):
        assert normalize_institution("MIT") == "Massachusetts Institute of Technology"

    def test_who_alias(self):
        assert normalize_institution("WHO") == "World Health Organization"

    def test_world_bank_alias(self):
        assert normalize_institution("World Bank") == "World Bank"

    def test_jpal_alias(self):
        assert normalize_institution("JPAL") == "Abdul Latif Jameel Poverty Action Lab"

    def test_3ie_alias(self):
        assert normalize_institution("3ie") == "International Initiative for Impact Evaluation"

    def test_unknown_passthrough(self):
        assert normalize_institution("Some Random University") == "Some Random University"

    def test_empty_string(self):
        assert normalize_institution("") == ""

    def test_oxford_alias(self):
        assert normalize_institution("Oxford") == "University of Oxford"

    def test_harvard_alias(self):
        assert normalize_institution("Harvard") == "Harvard University"

    # ── Stem rewriting (University of X / X University) ────────────────── #

    def test_university_of_michigan_stem_match(self):
        # "University of Michigan" -> stem "michigan" -> "University of Michigan"
        assert normalize_institution("University of Michigan") == "University of Michigan"

    def test_michigan_university_stem_match(self):
        # "Michigan University" -> stem "michigan" — but the alias key is "umich",
        # not "michigan", so this does NOT match (correct: the alias table uses
        # "umich" as the key, not the bare state name).
        assert normalize_institution("Michigan University") == "Michigan University"

    def test_the_university_of_oxford_stem_match(self):
        assert normalize_institution("The University of Oxford") == "University of Oxford"


# --------------------------------------------------------------------------- #
# Bug fix: "University of {org}" false matches
# --------------------------------------------------------------------------- #

class TestUniversityOfOrgFalseMatch:
    def test_university_of_who_does_not_match_who(self):
        # Bug: "University of WHO" was matching the "who" -> "World Health
        # Organization" alias via stem rewriting. After fix, it should NOT
        # expand to "World Health Organization".
        result = normalize_institution("University of WHO")
        assert result != "World Health Organization"
        assert result == "University of WHO"  # passes through unchanged

    def test_university_of_cdc_does_not_match_cdc(self):
        result = normalize_institution("University of CDC")
        assert result != "Centers for Disease Control and Prevention"

    def test_university_of_un_does_not_match_un(self):
        result = normalize_institution("University of UN")
        assert result != "United Nations"

    def test_university_of_imf_does_not_match_imf(self):
        result = normalize_institution("University of IMF")
        assert result != "International Monetary Fund"

    def test_university_of_nyu_still_matches_nyu(self):
        # NYU IS a university, so "University of NYU" correctly matches
        # "New York University" via stem rewriting — the fix only blocks
        # non-university orgs (WHO, CDC, UN, IMF), not university abbreviations.
        assert normalize_institution("University of NYU") == "New York University"

    def test_direct_who_still_works(self):
        # Direct "WHO" should still match — only the stem path is restricted
        assert normalize_institution("WHO") == "World Health Organization"


# --------------------------------------------------------------------------- #
# _is_university_alias
# --------------------------------------------------------------------------- #

class TestIsUniversityAlias:
    def test_university(self):
        assert _is_university_alias("Harvard University") is True

    def test_college(self):
        assert _is_university_alias("Imperial College London") is True

    def test_institute_of_technology(self):
        assert _is_university_alias("Massachusetts Institute of Technology") is True

    def test_organization(self):
        assert _is_university_alias("World Health Organization") is False

    def test_foundation(self):
        assert _is_university_alias("Kellogg Foundation") is False

    def test_research_institute(self):
        assert _is_university_alias("International Food Policy Research Institute") is False


# --------------------------------------------------------------------------- #
# normalize_value (dispatch)
# --------------------------------------------------------------------------- #

class TestNormalizeValue:
    def test_author_country(self):
        assert normalize_value("author_country", "USA") == "United States"

    def test_author_affiliation(self):
        assert normalize_value("author_affiliation", "MIT") == "Massachusetts Institute of Technology"

    def test_unknown_field_passthrough(self):
        assert normalize_value("authors", "Smith, John") == "Smith, John"

    def test_empty_field_name(self):
        assert normalize_value("", "anything") == "anything"
