"""Name-normalization helpers for scoring.

The LLM judge analysis proved the string scorer under-credits two fields:

  * author_affiliation (~9 pts lost): institution name variance — "MIT" vs
    "Massachusetts Institute of Technology", "UC Berkeley" vs "University of
    California Berkeley".
  * author_country (~3 pts lost): country name variance — "USA" vs "United
    States", "UK" vs "United Kingdom", "Russia" vs "Russian Federation",
    "South Korea" vs "Korea, Rep.".

This module canonicalizes both sides of a comparison *before* the fuzzy/exact
match runs, so the scorer credits semantically-equal names that differ only in
abbreviation, word order, or World-Bank-style qualifier suffixes.

The canonical forms target the taxonomy in data/taxonomy.json (World Bank
country names), since that is the ground-truth vocabulary.  For institutions
(free text, no taxonomy) we expand well-known abbreviations to their full forms
on both sides so a prediction "MIT" matches a truth "Massachusetts Institute of
Technology" — and vice-versa.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .taxonomy import get_options

# ── Country aliases ──────────────────────────────────────────────────────────
# Maps a normalized alias (lowercase, no punctuation) to the canonical taxonomy
# name.  Built to cover the common variance the judge flagged, plus the World
# Bank qualifier suffixes in the taxonomy itself ("Egypt, Arab Rep." etc.) so
# that a plain "Egypt" prediction matches.
_COUNTRY_ALIASES: dict[str, str] = {}

# Common short-form / ISO / colloquial aliases that a model is likely to emit.
_COUNTRY_ALIAS_SEED: dict[str, list[str]] = {
    "United States": ["usa", "us", "u s", "u s a", "united states of america",
                      "united states of america america", "america"],
    "United Kingdom": ["uk", "u k", "britain", "great britain", "england",
                       "united kingdom of great britain and northern ireland",
                       "scotland", "wales", "northern ireland"],
    "Russian Federation": ["russia", "russian fed", "rf"],
    "Korea, Rep.": ["south korea", "korea", "republic of korea", "rok"],
    "Korea, Dem. People's Rep.": ["north korea", "dprk", "north korea dprk",
                                   "democratic peoples republic of korea"],
    "Czech Republic": ["czechia", "czech"],
    "Iran, Islamic Rep.": ["iran", "islamic republic of iran", "iran ir"],
    "Egypt, Arab Rep.": ["egypt", "arab republic of egypt", "egypt ar"],
    "Yemen, Rep.": ["yemen", "republic of yemen"],
    "Venezuela, RB": ["venezuela", "bolivarian republic of venezuela"],
    "Congo, Dem. Rep.": ["democratic republic of congo", "drc", "congo dr",
                         "congo kinshasa", "zaire", "d r congo"],
    "Congo, Rep.": ["republic of congo", "congo brazzaville", "roc"],
    "Slovak Republic": ["slovakia"],
    "Kyrgyz Republic": ["kyrgyzstan"],
    "Lao PDR": ["laos", "lao", "lao peoples democratic republic"],
    "Syrian Arab Republic": ["syria"],
    "Turkiye": ["turkey", "türkiye"],
    "Cote d'Ivoire": ["ivory coast", "cote d ivoire", "côte d ivoire",
                      "republic of cote d ivoire"],
    "Cabo Verde": ["cabo verde", "cape verde"],
    "Eswatini": ["swaziland"],
    "Timor-Leste": ["east timor", "timor leste", "timor leste",
                    "democratic republic of timor leste"],
    "Bahamas, The": ["bahamas"],
    "Gambia, The": ["gambia"],
}

# Build the alias map from the seed, then add stripped-suffix aliases for every
# taxonomy country (e.g. "Congo, Dem. Rep." -> canonical, alias "congo dem rep"
# and "congo" both map there — but "congo" alone is ambiguous between the two
# Congos, so only the longer form is auto-aliased).


@lru_cache(maxsize=1)
def _country_canonical_map() -> dict[str, str]:
    """Return alias -> canonical-name dict for all taxonomy countries."""
    mapping: dict[str, str] = {}
    canonical_names = get_options("countries")

    # Seed aliases first (these take priority for ambiguous short forms).
    for canonical, aliases in _COUNTRY_ALIAS_SEED.items():
        if canonical not in canonical_names:
            continue
        norm_canonical = _norm_key(canonical)
        mapping[norm_canonical] = canonical
        for alias in aliases:
            mapping[alias] = canonical

    # Auto-alias: for each taxonomy name, add a key with the World Bank qualifier
    # suffix stripped/normalized so a plain prediction matches.  e.g.
    # "Egypt, Arab Rep." -> alias "egypt arab rep" and also "egypt" (but only if
    # "egypt" isn't already claimed by the seed — it is, so no collision).
    # For comma-suffix names that share the same prefix (e.g. "Congo, Dem. Rep."
    # and "Congo, Rep." both produce short="congo"), the short key is ambiguous
    # and must NOT be aliased to either — otherwise bare "Congo" maps to only
    # one of them, creating an asymmetric false-positive match.
    short_seen: dict[str, str] = {}  # short key -> first canonical name seen
    for name in canonical_names:
        norm = _norm_key(name)
        mapping.setdefault(norm, name)
        if "," in name:
            short = _norm_key(name.split(",", 1)[0])
            if short in short_seen and short_seen[short] != name:
                # Collision: two different canonical names share the same short
                # form.  Remove the earlier mapping so neither claims it — the
                # bare short form falls through to fuzzy/exact matching instead.
                mapping.pop(short, None)
                short_seen[short] = ""  # marker: ambiguous, skip future sets
            elif short_seen.get(short, "") != "":
                # Already marked ambiguous, skip
                pass
            else:
                short_seen[short] = name
                mapping.setdefault(short, name)

    return mapping


def _norm_key(s: str) -> str:
    """Aggressive normalization for alias lookup keys: lowercase, strip
    punctuation and diacritics, collapse whitespace."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)      # punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_country(value: str) -> str:
    """Canonicalize a country name to its taxonomy form.

    Returns the original value unchanged if no alias is known — so unknown
    countries fall through to the existing fuzzy/exact match logic.
    """
    if not value:
        return value
    key = _norm_key(value)
    return _country_canonical_map().get(key, value)


# ── Institution aliases ─────────────────────────────────────────────────────
# Well-known university / organization abbreviations.  Applied to BOTH sides of
# the comparison (prediction and truth) so that whichever side uses the short
# form, it expands to the long form and matches.  This is intentionally a
# conservative list of high-confidence, globally-recognized abbreviations —
# false expansions are worse than no expansion (they'd create spurious matches),
# so we prefer precision over coverage.  The fuzzy matcher already handles close
# variants; this only needs to bridge the abbreviation gap.
_INSTITUTION_ALIASES: dict[str, str] = {
    # US universities
    "mit": "Massachusetts Institute of Technology",
    "caltech": "California Institute of Technology",
    "uc berkeley": "University of California Berkeley",
    "ucla": "University of California Los Angeles",
    "uc davis": "University of California Davis",
    "nyu": "New York University",
    "upenn": "University of Pennsylvania",
    "penn state": "Pennsylvania State University",
    "osu": "Ohio State University",
    "uiuc": "University of Illinois Urbana Champaign",
    "umich": "University of Michigan",
    "utaustin": "University of Texas at Austin",
    "gt": "Georgia Institute of Technology",
    "gatech": "Georgia Institute of Technology",
    # UK universities
    "oxford": "University of Oxford",
    "cambridge": "University of Cambridge",
    "imperial college": "Imperial College London",
    "ucl": "University College London",
    "lse": "London School of Economics",
    "edinburgh": "University of Edinburgh",
    # International organizations
    "who": "World Health Organization",
    "un": "United Nations",
    "unicef": "United Nations Children's Fund",
    "undp": "United Nations Development Programme",
    "world bank": "World Bank",
    "imf": "International Monetary Fund",
    "fao": "Food and Agriculture Organization",
    "ilo": "International Labour Organization",
    "ifpri": "International Food Policy Research Institute",
    "jpal": "Abdul Latif Jameel Poverty Action Lab",
    "jpml": "Abdul Latif Jameel Poverty Action Lab",
    "3ie": "International Initiative for Impact Evaluation",
    # Research orgs
    "icddrb": "International Centre for Diarrhoeal Disease Research Bangladesh",
    "icddr b": "International Centre for Diarrhoeal Disease Research Bangladesh",
    "cdc": "Centers for Disease Control and Prevention",
    "nih": "National Institutes of Health",
    "nsf": "National Science Foundation",
    "columbia": "Columbia University",
    "harvard": "Harvard University",
    "stanford": "Stanford University",
    "yale": "Yale University",
    "princeton": "Princeton University",
    "cornell": "Cornell University",
    "duke": "Duke University",
    "johns hopkins": "Johns Hopkins University",
    "jhu": "Johns Hopkins University",
    "kellogg": "Kellogg Foundation",
}


@lru_cache(maxsize=1)
def _institution_alias_keys() -> list[tuple[str, str]]:
    """Sorted by key-length descending so longer abbreviations match first
    (e.g. 'icddr b' before 'icddrb' before 'b')."""
    return sorted(
        ((_norm_key(k), v) for k, v in _INSTITUTION_ALIASES.items()),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )


_UNIVERSITY_MARKERS = ("university", "college", "institute of technology")


def _is_university_alias(canonical: str) -> bool:
    """Whether a canonical institution name is a university/college (as opposed
    to an international organization or research org).  Used to gate the
    stem-rewriting path so 'University of WHO' doesn't match the 'who' alias."""
    lower = canonical.lower()
    return any(marker in lower for marker in _UNIVERSITY_MARKERS)


def normalize_institution(value: str) -> str:
    """Expand a known institution abbreviation to its full form.

    Returns the original value if no known abbreviation is detected.  The match
    is on the whole token sequence (not a substring) to avoid false positives
    like expanding 'us' inside 'campus'.  We also try the value with common
    suffixes stripped ('the', 'university of', 'inst of') so 'University of
    Michigan' and 'Michigan' can both resolve.
    """
    if not value:
        return value
    key = _norm_key(value)
    if not key:
        return value

    # Direct whole-string alias match.
    for akey, canonical in _institution_alias_keys():
        if key == akey:
            return canonical

    # If the value is 'University of X' / 'X University', also try just 'X'
    # against the alias table — but ONLY for aliases whose canonical form is
    # itself a university/college.  Without this restriction, 'University of
    # WHO' would match the 'who' -> 'World Health Organization' alias, creating
    # a spurious cross-type match.  Org abbreviations (WHO, CDC, UN, etc.) are
    # excluded from the stem path.
    for prefix in ("university of ", "the university of "):
        if key.startswith(prefix):
            stem = key[len(prefix):]
            for akey, canonical in _institution_alias_keys():
                if stem == akey and _is_university_alias(canonical):
                    return canonical

    if key.endswith(" university"):
        stem = key[: -len(" university")]
        for akey, canonical in _institution_alias_keys():
            if stem == akey and _is_university_alias(canonical):
                return canonical

    return value


# ── Author names ─────────────────────────────────────────────────────────────
# Author lists under-credit on FORMAT, not substance: ground truth uses initials
# ("Black, R. E.") while models expand to full given names ("Black, Robert E.") —
# the token scorer counts these as different people (~9 F1 pts on the production
# set). The matcher below is format-robust but PRECISION-PRESERVING and is gated
# so it does NOT re-introduce the over-crediting that the stricter fuzzy threshold
# (see scoring._score_list) was added to remove:
#   * surnames must match;
#   * given names must be pairwise compatible (equal, or one is a single-letter
#     initial of the other);
#   * a match on a LONE bare first initial vs a full name (the ambiguous
#     "Smith, J." vs "Smith, John" case) is NOT credited — corroboration is
#     required: either >=2 agreeing given components, or an exact given match.
# So "Black, R. E." == "Black, Robert E." (the E corroborates) but
# "Smith, J." != "Smith, John" and "Smith, John" != "Smith, Jane".

def _parse_author(name: str) -> tuple[str, list[str]]:
    """(surname, [given-name tokens]) from 'Last, First M.' or 'First M. Last'.
    Tokens are normalized; initials become single-letter tokens ('R. E.'->['r','e'])."""
    s = (name or "").strip()
    if "," in s:
        last, _, given = s.partition(",")
    else:
        parts = s.split()
        last, given = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (s, "")
    return _norm_key(last), [t for t in _norm_key(given).split() if t]


def _given_token_compatible(x: str, y: str) -> bool:
    return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))


def authors_equal(a: str, b: str) -> bool:
    """Whether two author strings denote the same person, tolerating
    initials-vs-full-given-name differences — but only with corroboration (see
    module note), so a lone bare initial never matches a full first name."""
    la, ga = _parse_author(a)
    lb, gb = _parse_author(b)
    if not la or la != lb:
        return False
    if not ga or not gb:
        return True  # surname-only on one side: can't distinguish, accept
    exact = 0
    for x, y in zip(ga, gb):  # positional: first, middle, ...
        if not _given_token_compatible(x, y):
            return False
        if x == y:
            exact += 1
    # All compatible. Require corroboration to avoid crediting a bare initial:
    # >=2 shared given components, or at least one exact (identical) agreement.
    return min(len(ga), len(gb)) >= 2 or exact >= 1


# ── Field-aware dispatch ────────────────────────────────────────────────────

def normalize_value(field_name: str, value: str) -> str:
    """Apply field-specific name normalization to a scalar string value.

    This is called by scoring._score_list for each element BEFORE the
    norm/fuzzy comparison, so both prediction and truth are canonicalized
    symmetrically.  Unknown fields pass through unchanged.
    """
    if field_name == "author_country":
        return normalize_country(value)
    if field_name == "author_affiliation":
        return normalize_institution(value)
    return value
