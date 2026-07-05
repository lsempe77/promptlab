"""Per-project registry: which synthesis "project" (DEP, HSF, Girl Effect,
StrongMinds, ...) exists and which fields it extracts/screens.

Each project has its own corpus, ground truth, and prompt/run history but
shares the same backend code, DB schema, and API (see `app/db.py`'s
`project_id`-scoped tables). Adding a new project means adding a new
`FieldSpec` dict (in its own module, mirroring `app/fields.py`) and
registering a `ProjectSpec` for it below \u2014 no schema changes needed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fields import FIELDS as _DEP_EXTRACTION_FIELDS, FieldSpec


@dataclass(frozen=True)
class ProjectSpec:
    slug: str
    name: str
    description: str
    fields: dict[str, FieldSpec]


PROJECTS: dict[str, ProjectSpec] = {
    "dep-extraction": ProjectSpec(
        slug="dep-extraction",
        name="DEP \u2014 Data Extraction",
        description=(
            "3ie Development Evidence Portal: extracting structured metadata (authors, "
            "affiliations, countries, sector) from evaluation study PDFs."
        ),
        fields=_DEP_EXTRACTION_FIELDS,
    ),
}


def get_project(slug: str) -> ProjectSpec:
    if slug not in PROJECTS:
        raise KeyError(f"Unknown project: {slug!r} (known: {sorted(PROJECTS)})")
    return PROJECTS[slug]
