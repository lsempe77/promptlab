"""Definitions for the 5 fields being prototyped in v1 (author, institution,
country of institution, sector, sub-sector). Each spec captures: where the
ground truth comes from in the raw ier-records export, what shape the value
takes (single value vs. list), and how it should be scored/typed in prompts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    value_type: str  # "single_categorical" | "list_categorical" | "list_text"
    taxonomy_key: str | None  # key into taxonomy.json, or None for free text
    description: str  # shown to the model in the extraction prompt


FIELDS: dict[str, FieldSpec] = {
    "authors": FieldSpec(
        name="authors",
        label="Author names",
        value_type="list_text",
        taxonomy_key=None,
        description=(
            "List EVERY author of the paper, one entry per author, in the order they appear in the "
            "title/author block \u2014 check for co-authors named after the first author and in footnotes, "
            "do not stop at the first name you find. Format each as 'Last name, First name Middle name' "
            "(e.g. 'Sabet, Shayda Mae'). If the paper gives only initials for the first/middle name, keep "
            "the initials exactly as printed (e.g. 'Miranda, J. M.') \u2014 do not guess or invent a full name "
            "you are not certain of from the text."
        ),
    ),
    "author_affiliation": FieldSpec(
        name="author_affiliation",
        label="Author institution(s)",
        value_type="list_text",
        taxonomy_key=None,
        description=(
            "All institution(s)/organization(s) that ANY author is affiliated with (e.g. university, "
            "research center, government agency, NGO) \u2014 check every co-author's affiliation, not just the "
            "first author's. List each distinct institution once, using its full name and abbreviation in "
            "brackets if the paper gives one (e.g. 'International Initiative for Impact Evaluation (3ie)')."
        ),
    ),
    "author_country": FieldSpec(
        name="author_country",
        label="Author institution country",
        value_type="list_categorical",
        taxonomy_key="countries",
        description=(
            "The country/countries where EACH author's institutional affiliation is located \u2014 check every "
            "co-author's affiliation (title page, footnotes, acknowledgments), not just the first author, and "
            "report one country per distinct institution (there may be several if co-authors are affiliated "
            "with institutions in different countries). If an affiliation names an organization that has "
            "country offices worldwide (e.g. 'World Bank', 'JPAL') without specifying a particular office, use "
            "the country of that organization's headquarters (e.g. 'JPAL' alone -> United States; 'JPAL Africa' "
            "-> South Africa; 'World Bank' -> United States). Use standard country names (e.g. 'United States', "
            "not 'USA' or 'US'). If a country cannot be determined from the paper, omit it rather than guessing."
        ),
    ),
    "sector_name": FieldSpec(
        name="sector_name",
        label="Sector",
        value_type="single_categorical",
        taxonomy_key="sectors",
        description="The single World Bank sector that best matches the paper's subject.",
    ),
    "sub_sector": FieldSpec(
        name="sub_sector",
        label="Sub-sector",
        value_type="single_categorical",
        taxonomy_key="sub_sectors_flat",
        description="The single sub-sector (within the chosen sector) that best matches the paper's subject.",
    ),
}
