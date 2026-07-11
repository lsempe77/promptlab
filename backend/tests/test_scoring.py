"""Tests for backend.app.scoring — the field-type-aware comparison engine.

Covers: normalisation (mojibake, accents, whitespace), fuzzy matching,
single-categorical scoring, list scoring (F1/precision/recall), honesty
scoring, outcome classification, split_alternatives, verify_excerpt, and
the excerpt-penalty path in score_field.
"""
from __future__ import annotations

import pytest

from backend.app.scoring import (
    ABSTENTION_CREDIT,
    CORRECT_THRESHOLD,
    EXCERPT_PENALTY,
    OUTCOME_ABSTAIN_MISS,
    OUTCOME_CORRECT_ABSTAIN,
    OUTCOME_HALLUCINATION,
    OUTCOME_HIT,
    OUTCOME_WRONG,
    _demojibake,
    _norm,
    _strip_accents,
    _fuzzy_equal,
    fold_display,
    fold_value,
    score_field,
    split_alternatives,
    verify_excerpt,
)


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #

class TestNorm:
    def test_ascii_passthrough(self):
        assert _norm("Hello World") == "hello world"

    def test_whitespace_collapse(self):
        assert _norm("  extra   spaces  ") == "extra spaces"

    def test_case_fold(self):
        assert _norm("MiXeDCase") == "mixedcase"

    def test_accent_strip(self):
        assert _norm("Baños") == "banos"
        assert _norm("São Paulo") == "sao paulo"

    def test_empty(self):
        assert _norm("") == ""
        assert _norm("   ") == ""


class TestStripAccents:
    def test_combining_diacritics(self):
        assert _strip_accents("é ü ñ ç") == "e u n c"

    def test_translit_letters(self):
        assert _strip_accents("ø æ œ ß ł ð") == "o ae oe ss l d"

    def test_ascii_unchanged(self):
        assert _strip_accents("plain ASCII 123") == "plain ASCII 123"


class TestDemojibake:
    def test_repairs_cp1252_mojibake(self):
        # "Baños" mis-decoded as cp1252 -> "BaÃ±os"
        assert _demojibake("BaÃ±os") == "Baños"

    def test_ascii_passthrough(self):
        assert _demojibake("hello") == "hello"

    def test_already_correct_unicode(self):
        # A clean Latin-1 accent re-encoded to cp1252 is invalid UTF-8,
        # so the round-trip raises and we keep the original.
        assert _demojibake("Baños") == "Baños"


# --------------------------------------------------------------------------- #
# Fuzzy matching
# --------------------------------------------------------------------------- #

class TestFuzzyEqual:
    def test_exact_match(self):
        assert _fuzzy_equal("hello", "hello") is True

    def test_case_insensitive_after_norm(self):
        assert _fuzzy_equal("Hello", "hello") is True

    def test_accent_insensitive(self):
        assert _fuzzy_equal("Baños", "Banos") is True

    def test_different_strings(self):
        assert _fuzzy_equal("apple", "orange") is False

    def test_minor_typo(self):
        # token_set_ratio handles word-order differences well
        assert _fuzzy_equal("John Smith", "Smith, John") is True

    def test_below_threshold(self):
        assert _fuzzy_equal("completely", "different") is False


# --------------------------------------------------------------------------- #
# fold_display / fold_value
# --------------------------------------------------------------------------- #

class TestFoldDisplay:
    def test_preserves_case(self):
        assert fold_display("HELLO") == "HELLO"

    def test_strips_accents_keeps_case(self):
        assert fold_display("Baños") == "Banos"

    def test_collapses_whitespace(self):
        assert fold_display("  extra   spaces  ") == "extra spaces"

    def test_repair_mojibake_then_strip(self):
        # fold_display repairs mojibake THEN strips accents: "BaÃ±os" -> "Baños" -> "Banos"
        assert fold_display("BaÃ±os") == "Banos"


class TestFoldValue:
    def test_scalar(self):
        assert fold_value("Baños") == "Banos"

    def test_list(self):
        assert fold_value(["Baños", "São Paulo"]) == ["Banos", "Sao Paulo"]

    def test_none(self):
        assert fold_value(None) is None

    def test_number(self):
        assert fold_value(42) == 42

    def test_nested_list(self):
        assert fold_value([["Héllo"], "World"]) == [["Hello"], "World"]


# --------------------------------------------------------------------------- #
# split_alternatives
# --------------------------------------------------------------------------- #

class TestSplitAlternatives:
    def test_no_pipe(self):
        assert split_alternatives("Health") == ["Health"]

    def test_two_alternatives(self):
        assert split_alternatives("Health|Social protection") == ["Health", "Social protection"]

    def test_three_alternatives(self):
        result = split_alternatives("A | B | C")
        assert result == ["A", "B", "C"]

    def test_strips_whitespace(self):
        assert split_alternatives("  A  |  B  ") == ["A", "B"]

    def test_empty_parts_dropped(self):
        assert split_alternatives("A||B") == ["A", "B"]


# --------------------------------------------------------------------------- #
# verify_excerpt
# --------------------------------------------------------------------------- #

class TestVerifyExcerpt:
    def test_none_excerpt(self):
        assert verify_excerpt(None, "source text") is None

    def test_empty_excerpt(self):
        assert verify_excerpt("", "source text") is None

    def test_whitespace_only_excerpt(self):
        assert verify_excerpt("   ", "source text") is None

    def test_none_source(self):
        assert verify_excerpt("some excerpt", None) is None

    def test_empty_source(self):
        assert verify_excerpt("some excerpt", "") is None

    def test_exact_substring(self):
        src = "The quick brown fox jumps over the lazy dog"
        assert verify_excerpt("quick brown fox", src) is True

    def test_not_found(self):
        src = "The quick brown fox jumps over the lazy dog"
        assert verify_excerpt("completely unrelated text", src) is False

    def test_minor_noise_fuzzy_match(self):
        src = "The quick brown fox jumps over the lazy dog"
        # Small whitespace difference should still fuzzy-match
        assert verify_excerpt("quick  brown  fox", src) is True


# --------------------------------------------------------------------------- #
# score_field — single_categorical
# --------------------------------------------------------------------------- #

class TestScoreSingleCategorical:
    def test_exact_match(self):
        r = score_field("sector_name", "Health", "Health")
        assert r.score == 1.0
        assert r.is_correct is True
        assert r.outcome == OUTCOME_HIT
        assert r.honesty_score == 1.0

    def test_fuzzy_match(self):
        # Accent / case differences -> fuzzy match (0.9 score)
        r = score_field("sector_name", "Health", "health")
        assert r.score == 1.0  # _norm folds case, so it's exact after normalisation

    def test_accent_match(self):
        r = score_field("sector_name", "Educación", "Educacion")
        assert r.score == 1.0
        assert r.is_correct is True

    def test_mismatch(self):
        r = score_field("sector_name", "Health", "Education")
        assert r.score == 0.0
        assert r.is_correct is False
        assert r.outcome == OUTCOME_WRONG
        assert r.honesty_score == 0.0

    def test_abstention_with_truth(self):
        r = score_field("sector_name", None, "Health")
        assert r.score == 0.0
        assert r.is_correct is False
        assert r.outcome == OUTCOME_ABSTAIN_MISS
        assert r.honesty_score == ABSTENTION_CREDIT

    def test_abstention_empty_string(self):
        r = score_field("sector_name", "", "Health")
        assert r.score == 0.0
        assert r.outcome == OUTCOME_ABSTAIN_MISS

    def test_correct_abstain_both_empty(self):
        r = score_field("sector_name", None, None)
        assert r.score == 1.0
        assert r.is_correct is True
        assert r.outcome == OUTCOME_CORRECT_ABSTAIN
        assert r.honesty_score == 1.0

    def test_correct_abstain_both_empty_string(self):
        r = score_field("sector_name", "", "")
        assert r.score == 1.0
        assert r.outcome == OUTCOME_CORRECT_ABSTAIN

    def test_hallucination(self):
        r = score_field("sector_name", "Health", None)
        assert r.score == 0.0
        assert r.is_correct is False
        assert r.outcome == OUTCOME_HALLUCINATION
        assert r.honesty_score == 0.0

    def test_hallucination_empty_truth(self):
        r = score_field("sector_name", "Health", "")
        assert r.score == 0.0
        assert r.outcome == OUTCOME_HALLUCINATION

    def test_alternatives_match(self):
        # Ground truth with pipe-delimited alternatives
        r = score_field("sub_sector", "Health", "Health|Social protection")
        assert r.score == 1.0
        assert r.outcome == OUTCOME_HIT

    def test_alternatives_second_match(self):
        r = score_field("sub_sector", "Social protection", "Health|Social protection")
        assert r.score == 1.0
        assert r.outcome == OUTCOME_HIT

    def test_alternatives_no_match(self):
        r = score_field("sub_sector", "Education", "Health|Social protection")
        assert r.score == 0.0
        assert r.outcome == OUTCOME_WRONG


# --------------------------------------------------------------------------- #
# score_field — list_text (authors, affiliations)
# --------------------------------------------------------------------------- #

class TestScoreListText:
    def test_perfect_match(self):
        r = score_field("authors", ["Smith, John", "Doe, Jane"], ["Smith, John", "Doe, Jane"])
        assert r.score == 1.0
        assert r.is_correct is True
        assert r.outcome == OUTCOME_HIT

    def test_partial_match(self):
        # 1 correct of 2 truth, no extras -> precision=1.0, recall=0.5, f1=0.667
        r = score_field("authors", ["Smith, John"], ["Smith, John", "Doe, Jane"])
        assert r.score == pytest.approx(2 / 3, abs=0.01)
        assert r.is_correct is False

    def test_overprediction(self):
        # 2 predicted, 1 truth, 1 correct -> precision=0.5, recall=1.0, f1=0.667
        r = score_field("authors", ["Smith, John", "Doe, Jane"], ["Smith, John"])
        assert r.score == pytest.approx(2 / 3, abs=0.01)

    def test_empty_truth_empty_pred(self):
        r = score_field("authors", [], [])
        assert r.score == 1.0
        assert r.outcome == OUTCOME_CORRECT_ABSTAIN

    def test_empty_truth_with_pred(self):
        r = score_field("authors", ["Smith, John"], [])
        assert r.score == 0.0
        assert r.outcome == OUTCOME_HALLUCINATION

    def test_empty_pred_with_truth(self):
        r = score_field("authors", [], ["Smith, John"])
        assert r.score == 0.0
        assert r.outcome == OUTCOME_ABSTAIN_MISS
        assert r.honesty_score == ABSTENTION_CREDIT

    def test_none_pred_with_truth(self):
        r = score_field("authors", None, ["Smith, John"])
        assert r.score == 0.0
        assert r.outcome == OUTCOME_ABSTAIN_MISS

    def test_fuzzy_match_in_list(self):
        # Accent differences should fuzzy-match for list_text
        r = score_field("authors", ["Señor, J"], ["Senor, J"])
        assert r.score == pytest.approx(1.0, abs=0.01)

    def test_honesty_score_under_reporting(self):
        # Under-reporting (2 truth, 1 correct, no extras) should get
        # abstention credit on the missing item, boosting honesty_score above raw f1
        r = score_field("authors", ["Smith, John"], ["Smith, John", "Doe, Jane"])
        assert r.honesty_score > r.score

    def test_honesty_score_with_extras(self):
        # Wrong extras should keep honesty_score near raw f1 (no abstention credit)
        r = score_field("authors", ["Smith, John", "Wrong, Guy"], ["Smith, John"])
        # precision=0.5, recall=1.0, f1=0.667
        # honesty: adjusted_recall = (1 + 0.5*0)/1 = 1.0, honesty_f1 = 2*0.5*1/(0.5+1) = 0.667
        assert r.honesty_score == pytest.approx(r.score, abs=0.01)

    def test_outcome_wrong_with_extras(self):
        r = score_field("authors", ["Wrong, Guy", "Also Wrong, Person"], ["Smith, John"])
        assert r.outcome == OUTCOME_WRONG

    def test_outcome_abstain_miss_under_reporting(self):
        # No extras, just under-reported -> abstain_miss
        r = score_field("authors", ["Smith, John"], ["Smith, John", "Doe, Jane"])
        assert r.outcome == OUTCOME_ABSTAIN_MISS

    def test_scalar_predicted_as_single_element(self):
        # Non-list predicted value gets wrapped as single-element list
        r = score_field("authors", "Smith, John", ["Smith, John"])
        assert r.score == 1.0


# --------------------------------------------------------------------------- #
# score_field — list_categorical (author_country)
# --------------------------------------------------------------------------- #

class TestScoreListCategorical:
    def test_perfect_match(self):
        r = score_field("author_country", ["United States", "Brazil"], ["United States", "Brazil"])
        assert r.score == 1.0
        assert r.is_correct is True

    def test_case_insensitive(self):
        r = score_field("author_country", ["united states"], ["United States"])
        assert r.score == 1.0

    def test_partial(self):
        # 1 correct of 2 truth, no extras -> precision=1.0, recall=0.5, f1=0.667
        r = score_field("author_country", ["United States"], ["United States", "Brazil"])
        assert r.score == pytest.approx(2 / 3, abs=0.01)


# --------------------------------------------------------------------------- #
# score_field — excerpt penalty
# --------------------------------------------------------------------------- #

class TestExcerptPenalty:
    def test_excerpt_verified_true_no_penalty(self):
        r = score_field("sector_name", "Health", "Health", excerpt_verified=True)
        assert r.honesty_score == 1.0

    def test_excerpt_verified_false_with_value(self):
        r = score_field("sector_name", "Health", "Health", excerpt_verified=False)
        assert r.honesty_score == 1.0 * EXCERPT_PENALTY
        assert "cited excerpt not found" in r.explanation

    def test_excerpt_verified_false_no_value(self):
        # Abstention with unverified excerpt should NOT be penalised
        r = score_field("sector_name", None, "Health", excerpt_verified=False)
        assert r.honesty_score == ABSTENTION_CREDIT  # no penalty since predicted is falsy

    def test_excerpt_verified_none_no_penalty(self):
        r = score_field("sector_name", "Health", "Health", excerpt_verified=None)
        assert r.honesty_score == 1.0

    def test_excerpt_penalty_list_field(self):
        r = score_field("authors", ["Smith, John"], ["Smith, John"], excerpt_verified=False)
        assert r.honesty_score == 1.0 * EXCERPT_PENALTY


# --------------------------------------------------------------------------- #
# score_field — unknown field type
# --------------------------------------------------------------------------- #

class TestScoreFieldErrors:
    def test_unknown_value_type(self):
        # There's no field with an unknown value_type in FIELDS, but score_field
        # raises ValueError for one. We test the guard by monkeypatching.
        from backend.app import scoring
        original = scoring.FIELDS["sector_name"].value_type
        try:
            # FieldSpec is frozen, so we replace the whole entry
            from backend.app.fields import FieldSpec
            scoring.FIELDS["sector_name"] = FieldSpec(
                name="sector_name",
                label="Sector",
                value_type="unknown_type",
                taxonomy_key="sectors",
                description="test",
            )
            with pytest.raises(ValueError):
                scoring.score_field("sector_name", "x", "y")
        finally:
            scoring.FIELDS["sector_name"] = FieldSpec(
                name="sector_name",
                label="Sector",
                value_type=original,
                taxonomy_key="sectors",
                description="test",
            )


class TestCountryNormalization:
    """The LLM judge proved the scorer under-credits author_country by ~3 pts
    due to country-name variance. These tests lock in the alias resolution."""

    def test_usa_matches_united_states(self):
        r = score_field("author_country", ["USA"], ["United States"])
        assert r.is_correct
        assert r.score == 1.0

    def test_uk_matches_united_kingdom(self):
        r = score_field("author_country", ["UK"], ["United Kingdom"])
        assert r.is_correct

    def test_russia_matches_russian_federation(self):
        r = score_field("author_country", ["Russia"], ["Russian Federation"])
        assert r.is_correct

    def test_south_korea_matches_korea_rep(self):
        r = score_field("author_country", ["South Korea"], ["Korea, Rep."])
        assert r.is_correct

    def test_egypt_matches_world_bank_form(self):
        r = score_field("author_country", ["Egypt"], ["Egypt, Arab Rep."])
        assert r.is_correct

    def test_drc_matches_dem_rep(self):
        r = score_field("author_country", ["Democratic Republic of Congo"],
                        ["Congo, Dem. Rep."])
        assert r.is_correct

    def test_ambiguous_congo_does_not_false_match(self):
        # "Congo" alone is ambiguous — must NOT auto-match either Congo variant.
        r = score_field("author_country", ["Congo"], ["Congo, Rep."])
        assert not r.is_correct

    def test_multiple_countries_normalized(self):
        r = score_field("author_country", ["USA", "UK"],
                        ["United States", "United Kingdom"])
        assert r.is_correct
        assert r.score == 1.0

    def test_unknown_country_passes_through(self):
        r = score_field("author_country", ["Atlantis"], ["United States"])
        assert not r.is_correct


class TestInstitutionNormalization:
    """The LLM judge proved the scorer under-credits author_affiliation by ~9 pts
    due to institution-name variance. These tests lock in the alias resolution."""

    def test_mit_matches_full_name(self):
        r = score_field("author_affiliation", ["MIT"],
                        ["Massachusetts Institute of Technology"])
        assert r.is_correct

    def test_full_name_matches_abbrev(self):
        # Bidirectional: truth has the abbreviation, prediction has the full form.
        r = score_field("author_affiliation",
                        ["University of Michigan"], ["umich"])
        assert r.is_correct

    def test_who_matches_world_health_organization(self):
        r = score_field("author_affiliation", ["WHO"],
                        ["World Health Organization"])
        assert r.is_correct

    def test_3ie_matches_full_name(self):
        r = score_field("author_affiliation", ["3ie"],
                        ["International Initiative for Impact Evaluation"])
        assert r.is_correct

    def test_world_bank_exact(self):
        r = score_field("author_affiliation", ["World Bank"], ["World Bank"])
        assert r.is_correct

    def test_wrong_institution_still_fails(self):
        r = score_field("author_affiliation", ["MIT"], ["Harvard University"])
        assert not r.is_correct

    def test_unknown_institution_passes_through(self):
        r = score_field("author_affiliation",
                        ["Southwest Technical Institute"], ["Northeast Polytechnic"])
        assert not r.is_correct

