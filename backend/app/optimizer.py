"""GEPA-lite prompt optimizer for a single field.

Simplified, single-objective version of the GEPA (Genetic-Pareto) idea: keep
one incumbent instruction; each iteration, run it on a fresh minibatch to
collect concrete failure cases (predicted vs. expected, with the excerpt the
model cited), hand those to a "reflector" model that proposes a revised
instruction in natural language (its diagnosis is the textual-feedback
signal — the closest analogue to a gradient we have with API-only models),
then accept the candidate only if it scores better on a fixed held-out
validation set. Stops after `no_improve_limit` consecutive iterations without
a new best score (per project decision: plateau-based stopping, not a fixed
metric threshold).
"""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field as dataclass_field
from typing import Any

from . import analytics, db, gateway, prompt_store, prompts, scoring
from .scoring import RECALL_FLOOR
from . import carbon
from . import db_pg as _db_pg  # Phase 2: Postgres routing
from .exemplars import Exemplar, parse_exemplars, serialize_exemplars, merge_exemplars

_USE_PG = _db_pg.pg_enabled()
from .corpus import read_md
from .parsing import ParseError, parse_field_response, parse_json_object

DEFAULT_MINIBATCH_SIZE = 8
DEFAULT_VAL_SIZE = 50  # held-out set for candidate comparison on the gate metric. Kept modest (vs
                       # the 100+ production stages) because every candidate is re-scored on the
                       # whole val set each iteration; 50 halves the noise of 30 (seed 42 => stable set)
DEFAULT_IMPROVEMENT_EPSILON = 0.01  # candidate must beat incumbent by at least this much
IMPROVEMENT_EPSILON = DEFAULT_IMPROVEMENT_EPSILON  # backwards-compat alias

# Per-field minimum improvement thresholds.  List fields (authors,
# author_affiliation) have high F1 variance on ~35 val records (100 GT records
# limits the split), so a small epsilon causes false-positive acceptances.
# Categorical fields (sector_name, sub_sector) can stay at the default.
FIELD_IMPROVEMENT_EPSILON: dict[str, float] = {
    "authors": 0.03,
    "author_affiliation": 0.03,
}

DEFAULT_HOLDOUT_SIZE = 30  # records held out ENTIRELY from candidate selection; used only for the
                           # cross-model generalization check, never to pick which candidate wins.
# A cheap, different-family model used (alongside the model being optimized) to check that an
# accepted rewrite generalizes rather than overfitting to a single model. Overridable per run.
DEFAULT_HOLDOUT_REFERENCE = "deepseek/deepseek-v4-flash"
_HOLDOUT_REFERENCE_ALT = "~openai/gpt-mini-latest"  # used when the optimized model IS the reference
# A second different-family holdout reference — two families makes the
# generalization gate stricter (blocks overfits that pass one family but
# not the other).  Only used when the pool is large enough to support it.
_HOLDOUT_REFERENCE_2 = "~google/gemini-flash-latest"

# Reflector fallback chain: if the primary reflector fails (API error, DNS,
# rate limit, unparseable response), try the next one.  Different families
# also catch different blind spots in the diagnosis.  The supervisor passes
# the first entry; the optimizer falls back to the rest automatically.
DEFAULT_REFLECTOR_FALLBACKS = ["~openai/gpt-4o", "~google/gemini-pro-latest"]
DEFAULT_BOLD_AFTER = 2  # after this many consecutive rejections, switch the reflector to "bold"
                        # mode (structural rewrites) instead of small incremental edits.


def _default_holdout_models(model_id: str) -> list[str]:
    """[optimized model, a cheap different-family reference, optionally a second
    different-family reference] for the cross-model generalization gate.

    Two reference families makes the gate stricter: a candidate must generalize
    across BOTH, not just one.  The second reference is skipped if it's the
    same family as the optimized model or the first reference.
    """
    ref1 = DEFAULT_HOLDOUT_REFERENCE
    if ref1.split("/")[-1].lower() in model_id.lower():
        ref1 = _HOLDOUT_REFERENCE_ALT
    models = [model_id, ref1]
    # Add a second reference from a different family if it's not already in the list
    ref2 = _HOLDOUT_REFERENCE_2
    ref2_family = ref2.split("/")[0].lower() if "/" in ref2 else ""
    existing_families = {m.split("/")[0].lower() for m in models if "/" in m}
    if ref2 not in models and ref2_family not in existing_families:
        models.append(ref2)
    return models


def _gate_score(field_name: str, predictions: list[tuple[Any, Any]]) -> tuple[float, int, float | None]:
    """The production gate metric (F1 for list fields, accuracy for categorical)
    computed over (predicted, truth) pairs -- the SAME metric the dashboard and
    supervisor gate on, so "what the optimizer chases" == "what the gate checks".
    Returns (metric, n, recall_or_None). recall is None for categorical fields.
    """
    rows = [{"predicted": p, "truth": t} for p, t in predictions]
    gm = analytics.gate_metrics(field_name, rows)
    return gm["metric"], gm["n"], gm.get("recall")


@dataclass
class EvalOutcome:
    mean_score: float
    n: int
    failures: list[dict[str, Any]]
    # (predicted, truth) for every successfully-parsed record, so the caller can
    # run an independent LLM-judge accuracy pass without re-calling the model.
    predictions: list[tuple[Any, Any]] = dataclass_field(default_factory=list)
    # A few cases the instruction currently gets RIGHT, shown to the reflector in
    # bold mode so a structural rewrite doesn't break what already works.
    successes: list[dict[str, Any]] = dataclass_field(default_factory=list)


@dataclass
class IterationLog:
    iteration_num: int
    candidate_instruction: str
    diagnosis: str | None
    val_score: float
    accepted: bool
    prompt_version_id: int


@dataclass
class OptimizeResult:
    field_name: str
    baseline_score: float
    best_instruction: str
    best_score: float
    iterations: list[IterationLog] = dataclass_field(default_factory=list)


def train_val_holdout_split(
    records: list[dict], val_size: int = DEFAULT_VAL_SIZE, holdout_size: int = DEFAULT_HOLDOUT_SIZE,
    seed: int = 42,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Three-way split: train (minibatch source) / val (drives which candidate
    wins) / holdout (NEVER seen during selection, used only for the cross-model
    generalization gate). All three are disjoint and fixed for a given seed.
    Sizes are clamped so a train remainder always survives on small pools.
    """
    shuffled = records[:]
    random.Random(seed).shuffle(shuffled)
    n = len(shuffled)
    holdout_size = max(0, min(holdout_size, n // 3))
    val_size = min(val_size, max(1, (n - holdout_size) // 2))
    holdout = shuffled[:holdout_size]
    val = shuffled[holdout_size:holdout_size + val_size]
    train = shuffled[holdout_size + val_size:]
    return train, val, holdout


def evaluate_instruction(
    field_name: str,
    instruction: str,
    records: list[dict],
    model_id: str,
    conn: Any = None,
    pg_conn: Any = None,        # Phase 2: Postgres connection for writing runs
    project_id: int | None = None,
    prompt_version_id: int | None = None,
    batch_id: str | None = None,
    max_workers: int = gateway.DEFAULT_MAX_CONCURRENCY,
) -> EvalOutcome:
    """Runs `instruction` against `records` and scores each response. Calls
    are made concurrently (I/O-bound). If `conn`/`prompt_version_id`/
    `batch_id` are given, every call is also logged to the `runs` table (same
    as the plain extraction harness), so optimizer evaluations show up in the
    same audit trail.
    """
    scores: list[float] = []
    failures: list[dict[str, Any]] = []
    successes: list[dict[str, Any]] = []
    predictions: list[tuple[Any, Any]] = []

    usable_records: list[dict] = []
    jobs: list[dict[str, Any]] = []
    sources: list[str] = []  # truncated md text the model saw, for excerpt verification
    for rec in records:
        try:
            md_text = read_md(rec["md_path"])
        except OSError:
            continue
        system_prompt, user_prompt = prompts.build_prompt(field_name, rec["title"] or "", md_text, instruction=instruction)
        usable_records.append(rec)
        sources.append(md_text[: prompts.MAX_CHARS])
        jobs.append({"model_id": model_id, "system_prompt": system_prompt, "user_prompt": user_prompt})

    responses = gateway.call_model_batch(jobs, max_workers=max_workers)

    for rec, source, resp in zip(usable_records, sources, responses):
        run_kwargs = dict(
            project_id=project_id, prompt_version_id=prompt_version_id, model_id=model_id, record_id=rec["id"],
            field_name=field_name, batch_id=batch_id,
        )
        if isinstance(resp, gateway.GatewayError):
            scores.append(0.0)
            failures.append({"id": rec["id"], "predicted": None, "truth": rec["ground_truth"],
                              "explanation": f"call/parse failed: {resp}", "excerpt": None})
            if prompt_version_id is not None:
                _kwargs = dict(**run_kwargs, raw_response=None, parsed_value=None, score=0.0,
                               is_correct=0, latency_ms=None, prompt_tokens=None, completion_tokens=None,
                               cost_usd=None, error=str(resp))
                if _USE_PG and pg_conn: _db_pg.add_run_pg(pg_conn, **_kwargs)
                elif conn is not None: db.add_run(conn, **_kwargs)
            continue

        try:
            value, meta = parse_field_response(field_name, resp.content)
        except ParseError as exc:
            scores.append(0.0)
            failures.append({"id": rec["id"], "predicted": None, "truth": rec["ground_truth"],
                              "explanation": f"call/parse failed: {exc}", "excerpt": None})
            if prompt_version_id is not None:
                _kwargs = dict(**run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                               is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                               completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd, error=str(exc))
                if _USE_PG and pg_conn: _db_pg.add_run_pg(pg_conn, **_kwargs)
                elif conn is not None: db.add_run(conn, **_kwargs)
            continue

        verified = scoring.verify_excerpt(meta.get("excerpt"), source)
        result = scoring.score_field(field_name, value, rec["ground_truth"], excerpt_verified=verified)
        predictions.append((value, rec["ground_truth"]))
        scores.append(result.honesty_score)
        if not result.is_correct:
            failures.append({
                "id": rec["id"],
                "predicted": value,
                "truth": rec["ground_truth"],
                "explanation": result.explanation,
                "excerpt": meta.get("excerpt"),
            })
        else:
            successes.append({"id": rec["id"], "predicted": value, "truth": rec["ground_truth"]})
        if prompt_version_id is not None:
            _kwargs = dict(**run_kwargs, raw_response=resp.content, parsed_value=value,
                           excerpt=meta.get("excerpt"), notes=meta.get("notes"),
                           excerpt_verified=(None if verified is None else int(verified)),
                           confidence=meta.get("confidence"),
                           outcome=result.outcome, honesty_score=result.honesty_score,
                           score=result.score, is_correct=int(result.is_correct), latency_ms=resp.latency_ms,
                           prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                           cost_usd=resp.cost_usd,
                           co2e_grams=carbon.estimate_co2e_grams(model_id, resp.completion_tokens, resp.latency_ms),
                           error=None)
            if _USE_PG and pg_conn: _db_pg.add_run_pg(pg_conn, **_kwargs)
            elif conn is not None: db.add_run(conn, **_kwargs)

    mean_score = sum(scores) / len(scores) if scores else 0.0
    return EvalOutcome(mean_score=mean_score, n=len(scores), failures=failures,
                       predictions=predictions, successes=successes)


_REFLECTOR_SYSTEM = (
    "You are improving the instruction given to another LLM that extracts a specific metadata "
    "field from academic papers. You will see the field, the current instruction, and concrete "
    "cases where that instruction led to a wrong extraction (predicted vs. expected value, and "
    "the excerpt the model cited as evidence). Diagnose the most likely cause of the failures, "
    "then propose a revised instruction that fixes it. Keep the instruction concise (2-5 "
    "sentences) and do not change the required output format — you are only rewriting the "
    "guidance about what to look for and how to decide. If you are shown instructions that were "
    "already tried and did NOT improve results, propose something meaningfully different rather "
    "than a small rewording of those."
)

_REFLECTOR_SYSTEM_BOLD = (
    "You are improving the instruction given to another LLM that extracts a specific metadata "
    "field from academic papers. Previous small, incremental edits have FAILED to improve results "
    "several times in a row, so a timid reword will not help. Take a BOLD, different approach: you "
    "MAY restructure the instruction, change the decision procedure step by step, add a short "
    "worked example, or rethink from scratch what to look for. You may exceed the usual 2-5 "
    "sentence length if the task genuinely needs it, but do NOT change the required output format. "
    "You will also be shown cases the CURRENT instruction already gets RIGHT — your rewrite must not "
    "break those. Diagnose why the incremental fixes stalled, then propose a substantially "
    "different instruction."
)


def _format_failures(failures: list[dict[str, Any]], max_cases: int) -> str:
    lines = []
    for c in failures[:max_cases]:
        # Fold accents/mojibake/whitespace on the shown values so the reflector
        # doesn't mis-diagnose a spurious 'José' vs 'Jose' difference as the
        # failure cause and chase it (the scorer already ignores these).
        pred = scoring.fold_value(c['predicted'])
        truth = scoring.fold_value(c['truth'])
        lines.append(
            f"- record {c['id']}: predicted={pred!r}, expected={truth!r} "
            f"({c['explanation']}); excerpt cited: {c['excerpt']!r}"
        )
    return "\n".join(lines) if lines else "(no failure cases in this sample)"


def _format_successes(successes: list[dict[str, Any]], max_cases: int) -> str:
    lines = []
    for c in successes[:max_cases]:
        pred = scoring.fold_value(c["predicted"])
        truth = scoring.fold_value(c["truth"])
        lines.append(f"- record {c['id']}: correctly produced {pred!r} (expected {truth!r})")
    return "\n".join(lines) if lines else "(no correct cases in this sample)"


def _format_avoid_history(avoid: list[dict[str, Any]]) -> str:
    if not avoid:
        return ""
    lines = [
        f'{i}. "{a["instruction"]}" \u2014 diagnosis was: {a.get("diagnosis") or "(none)"}'
        for i, a in enumerate(avoid, start=1)
    ]
    return (
        "\n\nINSTRUCTIONS ALREADY TRIED THAT DID NOT IMPROVE RESULTS "
        "(avoid repeating these or close rewordings of them):\n" + "\n".join(lines)
    )


def _format_categorical_confusion(failures: list[dict[str, Any]], max_pairs: int = 10) -> str:
    """For classification fields: summarise which (truth, predicted) pairs appear
    most often so the reflector can spot systematic label confusion at a glance."""
    from collections import Counter
    pairs: Counter = Counter()
    for f in failures:
        t = f.get("truth")
        p = f.get("predicted")
        if t is not None and p is not None and str(t) != str(p):
            pairs[(str(t), str(p))] += 1
    if not pairs:
        return ""
    lines = [
        f"  - expected {t!r} \u2192 predicted {p!r}: {n} case{'s' if n > 1 else ''}"
        for (t, p), n in pairs.most_common(max_pairs)
    ]
    return "\nPER-CATEGORY CONFUSION PATTERN (most common mislabellings):\n" + "\n".join(lines)


def propose_revision(
    field_name: str,
    current_instruction: str,
    failures: list[dict[str, Any]],
    reflector_model: str,
    max_cases: int = 8,
    avoid: list[dict[str, Any]] | None = None,
    max_attempts: int = 3,
    bold: bool = False,
    successes: list[dict[str, Any]] | None = None,
    field_value_type: str | None = None,
    allow_exemplars: bool = False,
) -> tuple[str, str | None]:
    """Calls the reflector model and returns (revised_instruction, diagnosis).
    `avoid` is a list of {"instruction", "diagnosis"} dicts for recently
    rejected candidates, so the reflector doesn't propose the same dead end
    twice. When `bold` is set (after repeated rejections) the reflector is asked
    for a substantially different rewrite and is shown `successes` (cases the
    current instruction gets right) so the bolder rewrite doesn't break them.

    When `allow_exemplars` is True (categorical fields where few-shot examples
    are most effective), the reflector may also propose 2-3 hard-case examples
    alongside the revised instruction. These are serialized into the instruction
    template (see exemplars.py) so they appear in the prompt the extraction model
    sees — the one automated lever that can lift a plateaued categorical field
    that text rewording alone cannot.
    """
    # Parse any existing exemplars from the current instruction so the reflector
    # can see and modify them rather than starting from scratch each iteration.
    base_instruction, existing_exemplars = parse_exemplars(current_instruction)
    cases_text = _format_failures(failures, max_cases)
    avoid_text = _format_avoid_history(avoid or [])
    keep_text = ""
    if bold:
        keep_text = (
            "\n\nCASES THE CURRENT INSTRUCTION ALREADY GETS RIGHT (your rewrite must keep these correct):\n"
            + _format_successes(successes or [], max_cases)
        )
    # For categorical fields, include a per-category confusion breakdown so the
    # reflector can spot systematic mislabellings (e.g. Health vs Social protection)
    # and propose targeted disambiguation rules without having to infer the pattern
    # from individual cases alone.
    confusion_text = ""
    if field_value_type == "single_categorical" and failures:
        confusion_text = _format_categorical_confusion(failures)
    # For sub_sector: give the reflector the full sector→sub-sector hierarchy so it
    # can propose instructions that reference the grouping structure rather than
    # treating all 66 options as equally likely alternatives.
    taxonomy_text = ""
    if field_name == "sub_sector":
        from .taxonomy import load_taxonomy
        sbs = load_taxonomy().get("sub_sectors_by_sector", {})
        lines = ["\nTAXONOMY HIERARCHY (sector → sub-sectors the model must choose from):"]
        for sector, subs in sbs.items():
            lines.append(f"  {sector}: {', '.join(subs)}")
        taxonomy_text = "\n".join(lines) + "\n"
    # Show existing exemplars (if any) so the reflector can build on them
    exemplar_context = ""
    if allow_exemplars:
        if existing_exemplars:
            ex_lines = [
                "\n\nCURRENT FEW-SHOT EXAMPLES already in the prompt (these will be KEPT — "
                "propose NEW examples that cover DIFFERENT confusion patterns):\n"
            ]
            for ex in existing_exemplars:
                ex_lines.append(f'  Paper: "{ex.paper}" -> Answer: {ex.answer}')
            ex_lines.append(
                "\nDo NOT duplicate the patterns above. Look at the FAILURE CASES and the "
                "PER-CATEGORY CONFUSION PATTERN, then propose examples for confusion pairs "
                "NOT already covered (e.g. if the existing examples cover Health-vs-Education "
                "and you see Social-protection->Health failures, propose an example for that).\n"
            )
            exemplar_context = "\n".join(ex_lines) + "\n"
        else:
            exemplar_context = (
                "\n\nNo few-shot examples yet. You SHOULD propose 2-3 hard cases as examples "
                "(see the exemplars field in the JSON format below). Pick cases that illustrate "
                "the exact confusion pattern in the failure cases — papers where the obvious "
                "keyword points to one sector but the correct answer is a different one.\n"
            )
    exemplar_json_field = ""
    exemplar_instructions = ""
    if allow_exemplars:
        exemplar_json_field = (
            '    "exemplars": [{"paper": "<short paper description>", "answer": "<correct label>"}, ...]\n'
        )
        if existing_exemplars:
            exemplar_instructions = (
                "The instruction already has few-shot examples that help. Your main job now is to "
                "propose NEW exemplars for confusion patterns NOT already covered. Keep the "
                "revised_instruction as close to the current one as possible — a small tweak is fine, "
                "but do NOT restructure it. The exemplars are the improvement, not the instruction.\n\n"
            )
        else:
            exemplar_instructions = (
                "You SHOULD also include 2-3 'exemplars' — hard cases where the obvious keyword misleads "
                "but the correct answer is different. These will be shown to the extraction model as "
                "worked examples. Pick cases from the FAILURE CASES above that best illustrate the "
                "confusion pattern. Use the paper's actual content (title + key detail), not the record ID. "
                "Keep the revised_instruction close to the current one — your main improvement "
                "should be the exemplars, not a wholesale rewrite of the instruction.\n\n"
            )
    base_prompt = (
        f"FIELD BEING EXTRACTED: {field_name}\n\n"
        f"CURRENT INSTRUCTION:\n{base_instruction}\n\n"
        f"FAILURE CASES (predicted vs. expected ground truth):\n{cases_text}"
        f"{confusion_text}"
        f"{taxonomy_text}"
        f"{exemplar_context}"
        f"{keep_text}"
        f"{avoid_text}\n\n"
        f"{exemplar_instructions}"
        "RESPOND IN VALID JSON:\n"
        "{\n"
        '    "diagnosis": "<1-2 sentences on the main failure pattern you see>",\n'
        f'    "revised_instruction": "<the new instruction text>"{("," if allow_exemplars else "")}\n'
        f"{exemplar_json_field}"
        "}\n"
    )
    # Reasoning-capable reflectors (e.g. Claude Sonnet) can spend their whole
    # token budget "thinking" and then return null/truncated content, so give a
    # generous budget and retry a couple of times, nudging harder for JSON-only
    # output on retries. Without this a transient empty/non-JSON reply wastes a
    # whole optimizer iteration.
    last_err: Exception | None = None
    system_prompt = _REFLECTOR_SYSTEM_BOLD if bold else _REFLECTOR_SYSTEM
    # Thinking models (e.g. ~anthropic/claude-sonnet-latest) return content=null
    # when response_format=json_object is combined with extended thinking — all
    # output lands in an internal thinking block the gateway can't see.  Disabling
    # json_mode lets the model output prose + JSON freely; _extract_json_object
    # already finds the {...} via regex fallback.  Bold mode also gets a larger
    # token budget so thinking tokens don't crowd out the actual JSON reply.
    reflector_max_tokens = 4000 if bold else 2000
    # Build the reflector chain: the primary model followed by fallbacks.
    # If the primary fails (API error, DNS, rate limit, unparseable response),
    # we try each fallback in order so a single provider outage doesn't waste
    # an entire optimizer iteration.
    reflector_chain = [reflector_model] + [
        m for m in DEFAULT_REFLECTOR_FALLBACKS if m != reflector_model
    ]
    for ref_model in reflector_chain:
        for attempt in range(max_attempts):
            user_prompt = base_prompt
            if attempt > 0:
                user_prompt += (
                    "\n\nIMPORTANT: Output ONLY the raw JSON object shown above — no preamble, no "
                    "commentary, no markdown code fences, and no step-by-step reasoning before it."
                )
            resp = gateway.call_model(
                ref_model,
                system_prompt,
                user_prompt,
                temperature=(0.9 if bold else 0.7) if attempt == 0 else 0.3,
                max_tokens=reflector_max_tokens,
                json_mode=False,
            )
            try:
                obj = parse_json_object(resp.content)
            except ParseError as exc:
                last_err = exc
                continue
            revised = obj.get("revised_instruction")
            if revised and str(revised).strip():
                revised_str = str(revised).strip()
                # If the reflector proposed exemplars, serialize them into the
                # instruction template so they appear in the extraction prompt.
                if allow_exemplars:
                    raw_exemplars = obj.get("exemplars")
                    if isinstance(raw_exemplars, list) and raw_exemplars:
                        # Validate exemplars against the field's taxonomy so the
                        # reflector can't inject arbitrary labels or prompt-control
                        # text.  Cap paper length and strip newlines from both
                        # fields so they can't introduce new prompt structure.
                        from .taxonomy import get_options
                        valid_answers = set(
                            a.lower().strip() for a in get_options(
                                prompts.FIELDS[field_name].taxonomy_key or ""
                            )
                        ) if prompts.FIELDS[field_name].taxonomy_key else None
                        exemplars = []
                        for ex in raw_exemplars:
                            if not isinstance(ex, dict):
                                continue
                            paper = str(ex.get("paper", "")).strip().replace("\n", " ")[:200]
                            answer = str(ex.get("answer", "")).strip().replace("\n", " ")
                            if not paper or not answer:
                                continue
                            if valid_answers is not None and answer.lower() not in valid_answers:
                                continue
                            exemplars.append(Exemplar(paper=paper, answer=answer))
                        if exemplars:
                            revised_str = serialize_exemplars(revised_str, exemplars)
                return revised_str, obj.get("diagnosis")
        # All retries on this reflector failed — try the next fallback
    raise last_err if last_err else ParseError("Reflector produced no usable revision")


def optimize_field(
    field_name: str,
    model_id: str,
    reflector_model: str,
    project_slug: str = "dep-extraction",
    max_iterations: int = 10,
    no_improve_limit: int = 4,
    minibatch_size: int = DEFAULT_MINIBATCH_SIZE,
    val_size: int = DEFAULT_VAL_SIZE,
    holdout_size: int = DEFAULT_HOLDOUT_SIZE,
    holdout_models: list[str] | None = None,
    bold_after: int = DEFAULT_BOLD_AFTER,
    improvement_epsilon: float | None = None,
    seed: int = 42,
    verbose: bool = True,
    max_workers: int = gateway.DEFAULT_MAX_CONCURRENCY,
    candidates_per_iteration: int = 1,
    history_window: int = 3,
) -> OptimizeResult:
    """`candidates_per_iteration` > 1 asks the reflector for that many
    independent revisions each iteration (best-of-N: only the highest-scoring
    one on the val set is compared against the incumbent). `history_window`
    controls how many of the most-recently-rejected instructions are shown to
    the reflector so it doesn't keep proposing the same dead end.

    A candidate is accepted only if it (a) improves LLM-judged accuracy on the
    selection `val` set for the optimized model AND (b) does not regress the
    mean judged accuracy on a separate `holdout` set across `holdout_models`
    (the optimized model plus a cheap different-family reference) — a
    cross-model generalization gate that blocks single-model overfits. After
    `bold_after` consecutive rejections the reflector switches to bold
    (structural) rewrites.
    """
    batch_id = uuid.uuid4().hex[:8]
    if improvement_epsilon is None:
        improvement_epsilon = FIELD_IMPROVEMENT_EPSILON.get(field_name, DEFAULT_IMPROVEMENT_EPSILON)
    if holdout_models is None:
        holdout_models = _default_holdout_models(model_id)

    with db.get_conn(autocommit=True) as conn:
        project_id = db.get_project_id(conn, project_slug)
        baseline_pv = prompt_store.get_or_create_baseline(conn, project_id, field_name, model_id=model_id)
        all_records = db.get_records_with_field(conn, project_id, field_name)

    if baseline_pv is None:
        raise RuntimeError(f"Failed to create/load a baseline prompt version for field={field_name}.")
    if len(all_records) < 4:
        raise ValueError(f"Not enough ground-truthed records for field={field_name} to optimize (need >= 4).")

    with db.get_conn(autocommit=True) as conn:
        job_id = db.start_job(conn, project_id, field_name, model_id, kind="optimization", total=max_iterations)

    try:
        result = _run_optimization(
            field_name=field_name, model_id=model_id, reflector_model=reflector_model,
            project_id=project_id, baseline_pv=baseline_pv, all_records=all_records, batch_id=batch_id,
            max_iterations=max_iterations, no_improve_limit=no_improve_limit,
            minibatch_size=minibatch_size, val_size=val_size, holdout_size=holdout_size,
            holdout_models=holdout_models, bold_after=bold_after, improvement_epsilon=improvement_epsilon,
            seed=seed, verbose=verbose, max_workers=max_workers,
            candidates_per_iteration=candidates_per_iteration,
            history_window=history_window, job_id=job_id,
        )
    except Exception as exc:
        with db.get_conn(autocommit=True) as conn:
            db.finish_job(conn, job_id, status="failed", error=str(exc))
        raise
    with db.get_conn(autocommit=True) as conn:
        db.finish_job(conn, job_id, status="completed")
    return result


def _holdout_generalization(
    field_name: str,
    instruction: str,
    holdout: list[dict],
    models: list[str],
    conn: Any,
    project_id: int,
    prompt_version_id: int,
    batch_id: str,
    max_workers: int,
) -> tuple[float, dict[str, float]]:
    """Run `instruction` on the held-out set with each model in `models`, score
    each against the ground truth with the production gate metric (F1 for list
    fields, accuracy for categorical), and return (mean across models, per-model
    metric). Runs are logged (they are real, costed extractions) so their
    carbon/cost show up in the audit trail too.
    """
    per_model: dict[str, float] = {}
    for m in models:
        outcome = evaluate_instruction(
            field_name, instruction, holdout, m, conn=conn, project_id=project_id,
            prompt_version_id=prompt_version_id, batch_id=batch_id, max_workers=max_workers,
        )
        metric, _n, _recall = _gate_score(field_name, outcome.predictions)
        per_model[m] = metric
    avg = sum(per_model.values()) / len(per_model) if per_model else 0.0
    return avg, per_model


def _run_optimization(
    field_name: str,
    model_id: str,
    reflector_model: str,
    project_id: int,
    baseline_pv: Any,
    all_records: list[dict],
    batch_id: str,
    max_iterations: int,
    no_improve_limit: int,
    minibatch_size: int,
    val_size: int,
    holdout_size: int,
    holdout_models: list[str],
    bold_after: int,
    improvement_epsilon: float,
    seed: int,
    verbose: bool,
    max_workers: int,
    candidates_per_iteration: int,
    history_window: int,
    job_id: int | None,
) -> OptimizeResult:
    train, val, holdout = train_val_holdout_split(
        all_records, val_size=val_size, holdout_size=holdout_size, seed=seed
    )
    # The cross-model generalization gate needs a non-trivial holdout; on very
    # small pools it is disabled and acceptance falls back to the val gate only.
    gen_gate = len(holdout) >= 4 and len(holdout_models) >= 1
    rnd = random.Random(seed)

    best_instruction = baseline_pv["template"]
    best_pv_id = baseline_pv["id"]
    # Phase 2: one PG connection for the entire optimization run.
    # The whole run is wrapped in try/finally (below) so this connection is
    # always released back to the pool — otherwise any exception in the
    # iteration loop leaks it and, after maxconn failures, blocks all PG work.
    _pg_ctx = _db_pg.get_pg_conn() if _USE_PG else None
    _pg_conn = _pg_ctx.__enter__() if _pg_ctx else None
    try:
        with db.get_conn(autocommit=True) as conn:
            baseline_outcome = evaluate_instruction(
                field_name, best_instruction, val, model_id, conn=conn, pg_conn=_pg_conn,
                project_id=project_id, prompt_version_id=best_pv_id, batch_id=batch_id, max_workers=max_workers,
            )
        best_score = baseline_outcome.mean_score
        best_gate, best_gate_n, best_gate_recall = _gate_score(field_name, baseline_outcome.predictions)
        best_holdout_avg = 0.0
        if gen_gate:
            with db.get_conn(autocommit=True) as conn:
                best_holdout_avg, base_per = _holdout_generalization(
                    field_name, best_instruction, holdout, holdout_models, conn=conn,
                    project_id=project_id, prompt_version_id=best_pv_id, batch_id=batch_id,
                    max_workers=max_workers,
                )
        if verbose:
            print(
                f"[iter 0] baseline gate-metric={best_gate:.3f} "
                f"(n={best_gate_n}, honesty={best_score:.3f})"
            )
            if gen_gate:
                per = ", ".join(f"{m.split('/')[-1]}={v:.2f}" for m, v in base_per.items())
                print(f"          holdout generalization avg={best_holdout_avg:.3f} over [{per}] (n={len(holdout)})")
            else:
                print(f"          (cross-model holdout gate disabled: only {len(holdout)} holdout records)")

        result = OptimizeResult(field_name=field_name, baseline_score=best_gate, best_instruction=best_instruction, best_score=best_gate)
        no_improve_count = 0
        rejected_history: list[dict[str, Any]] = []  # {"instruction", "diagnosis"}, most recent last

        with db.get_conn(autocommit=True) as conn:
            for it in range(1, max_iterations + 1):
                if job_id is not None:
                    db.update_job_progress(conn, job_id, it - 1)
                minibatch = rnd.sample(train, k=min(minibatch_size, len(train)))
                train_outcome = evaluate_instruction(
                    field_name, best_instruction, minibatch, model_id, conn=conn, pg_conn=_pg_conn,
                    project_id=project_id, prompt_version_id=best_pv_id, batch_id=batch_id, max_workers=max_workers,
                )

                if not train_outcome.failures:
                    if verbose:
                        print(f"[iter {it}] no failures in minibatch (train_score={train_outcome.mean_score:.3f}); stopping early.")
                    break

                avoid = rejected_history[-history_window:] if history_window > 0 else None
                bold = no_improve_count >= bold_after
                if bold and verbose:
                    print(f"[iter {it}] bold mode ON ({no_improve_count} consecutive rejections >= bold_after={bold_after})")

                field_value_type = prompts.FIELDS[field_name].value_type if field_name in prompts.FIELDS else None
                allow_exemplars = field_value_type == "single_categorical"
                candidates: list[dict[str, Any]] = []
                for k in range(candidates_per_iteration):
                    try:
                        candidate_instruction, diagnosis = propose_revision(
                            field_name, best_instruction, train_outcome.failures, reflector_model,
                            avoid=avoid, bold=bold, successes=train_outcome.successes,
                            field_value_type=field_value_type,
                            allow_exemplars=allow_exemplars,
                        )
                    except (ParseError, gateway.GatewayError) as exc:
                        if verbose:
                            print(f"[iter {it}] reflector call {k + 1}/{candidates_per_iteration} failed: {exc}")
                        continue

                    pv = prompt_store.add_version(
                        conn, project_id, field_name, candidate_instruction, parent_id=best_pv_id,
                        notes=f"iter {it} (candidate {k + 1}/{candidates_per_iteration}): {diagnosis or ''}"[:500],
                        model_id=model_id,
                    )
                    if pv is None:
                        raise RuntimeError(f"Failed to persist candidate prompt version for field={field_name}.")
                    candidate_outcome = evaluate_instruction(
                        field_name, candidate_instruction, val, model_id, conn=conn, pg_conn=_pg_conn,
                        project_id=project_id, prompt_version_id=pv["id"], batch_id=batch_id, max_workers=max_workers,
                    )
                    candidates.append({
                        "instruction": candidate_instruction, "diagnosis": diagnosis, "pv": pv, "outcome": candidate_outcome,
                    })

                if not candidates:
                    no_improve_count += 1
                    if no_improve_count >= no_improve_limit:
                        if verbose:
                            print(f"[stop] {no_improve_count} iterations with no improvement (limit={no_improve_limit}).")
                        break
                    continue

                best_candidate = max(candidates, key=lambda c: c["outcome"].mean_score)
                candidate_instruction = best_candidate["instruction"]
                diagnosis = best_candidate["diagnosis"]
                pv = best_candidate["pv"]
                candidate_outcome = best_candidate["outcome"]
                # Accept on the production gate metric (F1 for lists / accuracy for
                # categorical) -- the SAME metric the dashboard/supervisor gate on --
                # computed on the winning candidate's val predictions.
                cand_gate, cand_gate_n, cand_gate_recall = _gate_score(field_name, candidate_outcome.predictions)
                # Candidate must beat incumbent on the gate metric AND not regress recall
                # below the RECALL_FLOOR (list fields only; categorical recall=None).
                passes_val = (
                    cand_gate > best_gate + improvement_epsilon
                    and (cand_gate_recall is None or cand_gate_recall >= RECALL_FLOOR)
                )
                # Cross-model generalization gate: a candidate that beats val must also
                # not regress the mean gate metric on the untouched holdout set across
                # the optimized model + a different-family reference. This is what
                # blocks single-model overfits (e.g. the diacritics flip).
                cand_holdout_avg: float | None = None
                cand_holdout_per: dict[str, float] = {}
                if passes_val and gen_gate:
                    cand_holdout_avg, cand_holdout_per = _holdout_generalization(
                        field_name, candidate_instruction, holdout, holdout_models, conn=conn,
                        project_id=project_id, prompt_version_id=pv["id"], batch_id=batch_id,
                        max_workers=max_workers,
                    )
                    accepted = cand_holdout_avg >= best_holdout_avg - improvement_epsilon
                else:
                    accepted = passes_val

                for c in candidates:
                    is_winner_and_accepted = accepted and c is best_candidate
                    db.set_prompt_version_accepted(conn, c["pv"]["id"], is_winner_and_accepted)
                    if not is_winner_and_accepted:
                        rejected_history.append({"instruction": c["instruction"], "diagnosis": c["diagnosis"]})

                db.add_iteration(
                    conn, project_id=project_id, field_name=field_name, iteration_num=it, prompt_version_id=pv["id"],
                    model_id=model_id, mean_score=cand_gate, n_records=cand_gate_n,
                    feedback=diagnosis, accepted=int(accepted),
                )

                if verbose:
                    tag = "ACCEPTED" if accepted else "rejected"
                    n_tried = len(candidates)
                    suffix = f" (best of {n_tried})" if n_tried > 1 else ""
                    print(
                        f"[iter {it}] gate-metric={cand_gate:.3f} (best={best_gate:.3f}, "
                        f"honesty={candidate_outcome.mean_score:.3f}) -> {tag}{suffix}"
                    )
                    if cand_holdout_avg is not None:
                        per = ", ".join(f"{m.split('/')[-1]}={v:.2f}" for m, v in cand_holdout_per.items())
                        print(f"           holdout avg={cand_holdout_avg:.3f} (best={best_holdout_avg:.3f}) over [{per}]")
                    elif passes_val and not gen_gate:
                        print("           (val gate passed; holdout gate disabled)")
                    if diagnosis:
                        print(f"           diagnosis: {diagnosis}")

                result.iterations.append(IterationLog(
                    iteration_num=it, candidate_instruction=candidate_instruction, diagnosis=diagnosis,
                    val_score=cand_gate, accepted=accepted, prompt_version_id=pv["id"],
                ))

                if accepted:
                    # Phase 4+: Accumulate exemplars across accepted iterations.
                    # The reflector proposes fresh examples each iteration, but
                    # earlier accepted examples targeted different confusion
                    # patterns — we don't want to lose them.  Merge the
                    # candidate's exemplars with the incumbent's (deduped,
                    # capped at MAX_EXEMPLARS) so the prompt builds a growing
                    # library of hard cases instead of replacing them each time.
                    if allow_exemplars:
                        _inc_base, _inc_exs = parse_exemplars(best_instruction)
                        _cand_base, _cand_exs = parse_exemplars(candidate_instruction)
                        merged = merge_exemplars(_inc_exs, _cand_exs)
                        if merged != _cand_exs:
                            # Re-serialize with the merged exemplar set and
                            # update the stored prompt version so the lineage
                            # reflects what the model actually sees.
                            candidate_instruction = serialize_exemplars(_cand_base, merged)
                            db.set_prompt_version_accepted(conn, pv["id"], True)
                            # Update the template in-place with merged exemplars
                            conn.execute(
                                "UPDATE prompt_versions SET template=? WHERE id=?",
                                (candidate_instruction, pv["id"]),
                            )
                            if verbose:
                                print(f"           merged exemplars: {len(_inc_exs)} incumbent + {len(_cand_exs)} proposed -> {len(merged)} total")
                    best_instruction = candidate_instruction
                    best_pv_id = pv["id"]
                    best_score = candidate_outcome.mean_score  # honesty, for ranking the next minibatch
                    best_gate = cand_gate
                    if cand_holdout_avg is not None:
                        best_holdout_avg = cand_holdout_avg
                    result.best_instruction = candidate_instruction
                    result.best_score = cand_gate
                    no_improve_count = 0
                else:
                    no_improve_count += 1
                    if no_improve_count >= no_improve_limit:
                        if verbose:
                            print(f"[stop] {no_improve_count} iterations with no improvement (limit={no_improve_limit}).")
                        break

        result.best_instruction = best_instruction
        result.best_score = best_score
        return result
    finally:
        if _pg_ctx and _pg_conn:
            _pg_ctx.__exit__(None, None, None)
