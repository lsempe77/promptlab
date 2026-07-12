"""Few-shot exemplar support for the prompt optimizer (Phase 4).

The optimizer's reflector can only rewrite the instruction text — it cannot
add few-shot examples to the prompt.  But few-shot examples are the single most
effective lever for lifting categorical fields off their plateau (the literature
is clear: in-context examples teach the decision boundary in a way prose
instructions cannot).  This module lets the reflector propose 2-3 hard cases
that get appended to the instruction as a structured block.

DESIGN: exemplars are stored INSIDE the prompt_versions.template column (the
same mutable "gene" the optimizer already evolves), delimited by a sentinel
marker so they can be parsed back out.  This means:
  * no schema migration — template is already free text
  * build_prompt() needs no change — exemplars are part of the instruction
  * the acceptance test works as-is — examples are tested via val/holdout
  * the lineage table shows which examples were tried and rejected/accepted

FORMAT (appended to the end of the instruction):

    <base instruction text>

    ---FEW-SHOT EXAMPLES---
    Paper: "A school-based deworming program measuring child health outcomes"
    Answer: Health
    ---
    Paper: "Conditional cash transfers requiring health checkups"
    Answer: Social protection
    ---
    Paper: "Land titling reform studying property rights institutions"
    Answer: Public administration

The sentinel is "---FEW-SHOT EXAMPLES---" on its own line.  Everything after it
is the exemplar block; everything before is the base instruction.  Each example
is separated by a line containing only "---".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The sentinel that separates the instruction from the exemplar block.
# Chosen to be unlikely to appear in natural instruction text and to render
# as a clean section break if shown to the model.
SENTINEL = "---FEW-SHOT EXAMPLES---"
EXAMPLE_SEP = "---"


@dataclass(frozen=True)
class Exemplar:
    """A single few-shot example: a short paper description and the correct answer."""
    paper: str       # short description of the paper (title + key detail)
    answer: str      # the correct label/value


def parse_exemplars(instruction: str) -> tuple[str, list[Exemplar]]:
    """Split an instruction template into (base_instruction, exemplars).

    If the instruction has no exemplar block, returns (instruction, []).
    """
    if SENTINEL not in instruction:
        return instruction, []
    base, _, block = instruction.partition(SENTINEL)
    base = base.rstrip()
    block = block.strip()
    if not block:
        return base, []
    examples: list[Exemplar] = []
    # Each example is separated by a line of only "---"
    raw_examples = re.split(r"^" + re.escape(EXAMPLE_SEP) + r"\s*$", block, flags=re.MULTILINE)
    for raw in raw_examples:
        raw = raw.strip()
        if not raw:
            continue
        # Parse "Paper: ..." and "Answer: ..." lines
        paper = _extract_field(raw, "Paper:")
        answer = _extract_field(raw, "Answer:")
        if paper and answer:
            # Strip surrounding quotes if present (serializer adds them for readability)
            paper = paper.strip().strip('"').strip()
            examples.append(Exemplar(paper=paper, answer=answer.strip()))
    return base, examples


def _extract_field(text: str, prefix: str) -> str | None:
    """Extract the value after a 'Paper:' or 'Answer:' prefix."""
    match = re.search(rf"^{re.escape(prefix)}\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1) if match else None


def serialize_exemplars(base_instruction: str, exemplars: list[Exemplar]) -> str:
    """Combine a base instruction and exemplars into a single template string."""
    if not exemplars:
        return base_instruction
    lines = [base_instruction.rstrip(), "", SENTINEL]
    for ex in exemplars:
        lines.append(f'Paper: "{ex.paper}"')
        lines.append(f"Answer: {ex.answer}")
        lines.append(EXAMPLE_SEP)
    # Remove the trailing separator
    if lines and lines[-1] == EXAMPLE_SEP:
        lines.pop()
    return "\n".join(lines)


# Maximum number of exemplars to keep in the prompt.  More than this and the
# prompt gets long (token cost) while the marginal signal of each additional
# example drops fast.  6 covers the main confusion patterns of an 11-class
# taxonomy without bloating the prompt.
MAX_EXEMPLARS = 6


def _exemplar_key(ex: Exemplar) -> str:
    """Normalised key for deduplication — same paper+answer = same example
    regardless of quoting/whitespace differences."""
    return f"{ex.paper.strip().lower()}|{ex.answer.strip().lower()}"


def merge_exemplars(
    incumbent: list[Exemplar],
    proposed: list[Exemplar],
    max_exemplars: int = MAX_EXEMPLARS,
) -> list[Exemplar]:
    """Merge two exemplar lists, keeping the union deduplicated.

    Incumbent exemplars come first (they were already accepted as helpful), then
    new proposed ones are appended — up to `max_exemplars` total.  Duplicates
    (same paper text + answer) are dropped so we don't show the model the same
    example twice.
    """
    seen: set[str] = set()
    merged: list[Exemplar] = []
    for ex in incumbent + proposed:
        key = _exemplar_key(ex)
        if key in seen:
            continue
        seen.add(key)
        merged.append(ex)
        if len(merged) >= max_exemplars:
            break
    return merged
