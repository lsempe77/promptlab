"""Tiny shared helper for reading a record's markdown full text.

The corpus was extracted from PDFs via Apache Tika, which prepends a large
block of PDF/XMP metadata (dozens of `pdf:*`/`dc:*`/`xmp:*` lines, sometimes
a `pdf:charsPerPage` array with one entry per page) followed by an
`X-TIKA:content:` marker, then a full `<html><head>...<meta ...></head>`
block that duplicates the same metadata as HTML <meta> tags, and only then
the real `<body>` with the paper's actual title/authors/abstract/text.

Measured across a 150-record sample: the real content (first
University/Institute/Department/... mention) fell beyond a 6000-char prefix
in 61% of papers before stripping this boilerplate, vs. only 13% after \u2014
this was silently starving the author/affiliation/country fields of the very
text they needed (see backend/README.md). `read_md` now skips straight to
the `<body>` tag so prompt truncation (`prompts.MAX_CHARS`) budgets against
real content instead of metadata noise.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config

_TIKA_CONTENT_MARKER = "X-TIKA:content:"
_BODY_TAG_RE = re.compile(r"<body[^>]*>", re.IGNORECASE)


def _strip_tika_boilerplate(text: str) -> str:
    marker_idx = text.find(_TIKA_CONTENT_MARKER)
    if marker_idx == -1:
        return text
    rest = text[marker_idx + len(_TIKA_CONTENT_MARKER):]
    body_match = _BODY_TAG_RE.search(rest)
    if body_match is None:
        return rest.lstrip()
    return rest[body_match.end():].lstrip()


def read_md(md_path: str) -> str:
    path = Path(md_path)
    if not path.exists():
        # Fall back to config.MD_DIR/DEP_MD_DIR + the filename, in case
        # md_path was baked in for a different machine (e.g. a deploy
        # target's absolute path tested locally, or vice versa).
        fallback = config.MD_DIR / path.name
        if fallback.exists():
            path = fallback
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return _strip_tika_boilerplate(raw)
