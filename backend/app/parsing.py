"""Robust-ish parsing of a model's JSON response into the value shape a field
expects (str for single_categorical, list[str] for list_* types)."""
from __future__ import annotations

import json
import re
from typing import Any

from .fields import FIELDS

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class ParseError(ValueError):
    pass


def _extract_json_object(raw: str) -> dict:
    if raw is None:
        # Some models occasionally return content: null (empty/refused
        # response) instead of a string -- treat as a parse failure rather
        # than crashing on raw.strip().
        raise ParseError("Model returned no content (content was null)")
    raw = raw.strip()
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        # Fall back to grabbing the first {...} span (handles models that add
        # trailing prose/a second JSON blob after the real object).
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(raw[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ParseError(f"Could not parse JSON object in response: {raw[:300]!r}") from exc
        else:
            raise ParseError(f"Could not find a JSON object in response: {raw[:300]!r}")
    if not isinstance(obj, dict):
        # A model occasionally returns a bare JSON array/string/number instead
        # of the expected {"value": ...} / {"values": [...]} object -- treat
        # that as a parse failure (caught by callers) rather than crashing
        # with an AttributeError the next time something calls .get() on it.
        raise ParseError(f"Expected a JSON object, got {type(obj).__name__}: {raw[:300]!r}")
    return obj


def parse_json_object(raw: str) -> dict:
    """Generic version of `_extract_json_object` for callers outside this
    module (e.g. the optimizer parsing a reflector model's response)."""
    return _extract_json_object(raw)


def _parse_confidence(raw: Any) -> float | None:
    """A model's self-reported 0-1 confidence. Tolerates 0-100 scales and
    stray strings; clamps to [0, 1]. None if absent/unparseable."""
    if raw is None:
        return None
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return None
    if c > 1.0:  # model gave a 0-100 (or percentage) scale
        c = c / 100.0
    return max(0.0, min(1.0, c))


def parse_field_response(field_name: str, raw_content: str) -> tuple[Any, dict]:
    """Returns (value, meta) where value matches the field's expected shape
    and meta holds {excerpt, notes, confidence} for observability/debugging."""
    spec = FIELDS[field_name]
    obj = _extract_json_object(raw_content)
    meta = {
        "excerpt": obj.get("excerpt"),
        "notes": obj.get("notes"),
        "confidence": _parse_confidence(obj.get("confidence")),
    }

    if spec.value_type == "single_categorical":
        value = obj.get("value")
        return (value if value else None), meta

    values = obj.get("values")
    if not isinstance(values, list):
        raise ParseError(f"Expected 'values' to be a list, got: {values!r}")
    return [str(v) for v in values if str(v).strip()], meta
