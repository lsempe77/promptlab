"""Tests for backend.app.parsing — robust JSON extraction from model responses.

Covers: fenced code blocks, trailing prose, bare arrays, null content,
confidence parsing (0-1, 0-100, garbage), and field-response parsing for
both single_categorical and list value types.
"""
from __future__ import annotations

import pytest

from backend.app.parsing import (
    ParseError,
    _extract_json_object,
    _parse_confidence,
    parse_field_response,
    parse_json_object,
)


# --------------------------------------------------------------------------- #
# _extract_json_object / parse_json_object
# --------------------------------------------------------------------------- #

class TestExtractJsonObject:
    def test_plain_json(self):
        obj = _extract_json_object('{"value": "Health"}')
        assert obj == {"value": "Health"}

    def test_fenced_json(self):
        raw = '```json\n{"value": "Health"}\n```'
        obj = _extract_json_object(raw)
        assert obj == {"value": "Health"}

    def test_fenced_without_language(self):
        raw = '```\n{"value": "Health"}\n```'
        obj = _extract_json_object(raw)
        assert obj == {"value": "Health"}

    def test_trailing_prose(self):
        raw = '{"value": "Health"}\n\nHere is some explanation.'
        obj = _extract_json_object(raw)
        assert obj == {"value": "Health"}

    def test_two_json_blobs_separated_by_prose(self):
        # Two JSON objects with text between them: the fallback grabs first {
        # to last } which would span both. Only works when the second is
        # trailing prose (no braces).
        raw = '{"value": "Health"}\nHere is some explanation.'
        obj = _extract_json_object(raw)
        assert obj == {"value": "Health"}

    def test_two_json_blobs_raises(self):
        # Two separate JSON objects (both have braces) cannot be parsed as
        # a single object — the fallback spans from first { to last } which
        # produces invalid JSON. This is a known limitation.
        raw = '{"value": "Health"} {"other": "ignored"}'
        with pytest.raises(ParseError):
            _extract_json_object(raw)

    def test_whitespace_stripped(self):
        obj = _extract_json_object('  {"value": "Health"}  ')
        assert obj == {"value": "Health"}

    def test_null_content_raises(self):
        with pytest.raises(ParseError, match="no content"):
            _extract_json_object(None)

    def test_bare_array_raises(self):
        with pytest.raises(ParseError, match="Expected a JSON object"):
            _extract_json_object('["a", "b"]')

    def test_bare_string_raises(self):
        with pytest.raises(ParseError, match="Expected a JSON object"):
            _extract_json_object('"just a string"')

    def test_bare_number_raises(self):
        with pytest.raises(ParseError, match="Expected a JSON object"):
            _extract_json_object("42")

    def test_invalid_json_raises(self):
        with pytest.raises(ParseError, match="Could not find"):
            _extract_json_object("not json at all")

    def test_invalid_json_with_braces_raises(self):
        with pytest.raises(ParseError, match="Could not parse"):
            _extract_json_object("{invalid json content}")

    def test_no_braces_raises(self):
        with pytest.raises(ParseError, match="Could not find a JSON object"):
            _extract_json_object("just plain text no braces")

    def test_nested_object(self):
        raw = '{"value": "Health", "meta": {"confidence": 0.9}}'
        obj = _extract_json_object(raw)
        assert obj["value"] == "Health"
        assert obj["meta"]["confidence"] == 0.9


class TestParseJsonObject:
    def test_public_alias(self):
        assert parse_json_object('{"value": "Health"}') == {"value": "Health"}


# --------------------------------------------------------------------------- #
# _parse_confidence
# --------------------------------------------------------------------------- #

class TestParseConfidence:
    def test_float_0_to_1(self):
        assert _parse_confidence(0.85) == 0.85

    def test_int_0_to_1(self):
        assert _parse_confidence(1) == 1.0

    def test_zero(self):
        assert _parse_confidence(0) == 0.0

    def test_scale_0_to_100(self):
        assert _parse_confidence(85) == 0.85

    def test_scale_100(self):
        assert _parse_confidence(100) == 1.0

    def test_string_numeric(self):
        assert _parse_confidence("0.9") == 0.9

    def test_string_percentage(self):
        assert _parse_confidence("85") == 0.85

    def test_none(self):
        assert _parse_confidence(None) is None

    def test_garbage_string(self):
        assert _parse_confidence("not a number") is None

    def test_value_above_1_treated_as_0_100_scale(self):
        # 1.5 > 1.0, so it's treated as a 0-100 scale: 1.5/100 = 0.015
        assert _parse_confidence(1.5) == 0.015

    def test_clamp_below_0(self):
        assert _parse_confidence(-0.5) == 0.0

    def test_clamp_150_becomes_1(self):
        # 150 > 1.0, so divided by 100 -> 1.5, clamped to 1.0
        assert _parse_confidence(150) == 1.0

    def test_type_error_returns_none(self):
        assert _parse_confidence([1, 2, 3]) is None


# --------------------------------------------------------------------------- #
# parse_field_response
# --------------------------------------------------------------------------- #

class TestParseFieldResponse:
    def test_single_categorical_with_value(self):
        value, meta = parse_field_response("sector_name", '{"value": "Health"}')
        assert value == "Health"
        assert meta["excerpt"] is None
        assert meta["notes"] is None
        assert meta["confidence"] is None

    def test_single_categorical_empty_value(self):
        value, meta = parse_field_response("sector_name", '{"value": ""}')
        assert value is None

    def test_single_categorical_null_value(self):
        value, _ = parse_field_response("sector_name", '{"value": null}')
        assert value is None

    def test_single_categorical_with_meta(self):
        raw = '{"value": "Health", "excerpt": "the health sector", "confidence": 0.9, "notes": "confident"}'
        value, meta = parse_field_response("sector_name", raw)
        assert value == "Health"
        assert meta["excerpt"] == "the health sector"
        assert meta["confidence"] == 0.9
        assert meta["notes"] == "confident"

    def test_single_categorical_confidence_0_to_100(self):
        raw = '{"value": "Health", "confidence": 85}'
        _, meta = parse_field_response("sector_name", raw)
        assert meta["confidence"] == 0.85

    def test_list_text_with_values(self):
        raw = '{"values": ["Smith, John", "Doe, Jane"]}'
        value, _ = parse_field_response("authors", raw)
        assert value == ["Smith, John", "Doe, Jane"]

    def test_list_categorical_with_values(self):
        raw = '{"values": ["United States", "Brazil"]}'
        value, _ = parse_field_response("author_country", raw)
        assert value == ["United States", "Brazil"]

    def test_list_empty_values(self):
        raw = '{"values": []}'
        value, _ = parse_field_response("authors", raw)
        assert value == []

    def test_list_values_with_empty_strings(self):
        raw = '{"values": ["Smith, John", "", "  "]}'
        value, _ = parse_field_response("authors", raw)
        assert value == ["Smith, John"]

    def test_list_values_not_a_list_raises(self):
        raw = '{"values": "not a list"}'
        with pytest.raises(ParseError, match="Expected 'values' to be a list"):
            parse_field_response("authors", raw)

    def test_list_values_missing_key_raises(self):
        raw = '{"value": "Health"}'
        with pytest.raises(ParseError, match="Expected 'values' to be a list"):
            parse_field_response("authors", raw)

    def test_fenced_response(self):
        raw = '```json\n{"value": "Health"}\n```'
        value, _ = parse_field_response("sector_name", raw)
        assert value == "Health"

    def test_invalid_json_raises(self):
        with pytest.raises(ParseError):
            parse_field_response("sector_name", "not json")

    def test_list_with_meta(self):
        raw = '{"values": ["Smith, John"], "excerpt": "Author list", "confidence": 0.95}'
        value, meta = parse_field_response("authors", raw)
        assert value == ["Smith, John"]
        assert meta["excerpt"] == "Author list"
        assert meta["confidence"] == 0.95
