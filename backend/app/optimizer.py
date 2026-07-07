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
from . import carbon
from .corpus import read_md
from .parsing import ParseError, parse_field_response, parse_json_object

DEFAULT_MINIBATCH_SIZE = 8
DEFAULT_VAL_SIZE = 50  # held-out set for candidate comparison on the gate metric. Kept modest (vs
                       # the 100+ production stages) because every candidate is re-scored on the
                       # whole val set each iteration; 50 halves the noise of 30 (seed 42 => stable set)
IMPROVEMENT_EPSILON = 0.01  # candidate must beat incumbent by at least this much

DEFAULT_HOLDOUT_SIZE = 30  # records held out ENTIRELY from candidate selection; used only for the
                           # cross-model generalization check, never to pick which candidate wins.
# A cheap, different-family model used (alongside the model being optimized) to check that an
# accepted rewrite generalizes rather than overfitting to a single model. Overridable per run.
DEFAULT_HOLDOUT_REFERENCE = "deepseek/deepseek-v4-flash"
_HOLDOUT_REFERENCE_ALT = "~openai/gpt-mini-latest"  # used when the optimized model IS the reference
DEFAULT_BOLD_AFTER = 2  # after this many consecutive rejections, switch the reflector to "bold"
                        # mode (structural rewrites) instead of small incremental edits.


def _default_holdout_models(model_id: str) -> list[str]:
    """[optimized model, a cheap different-family reference] for the cross-model
    generalization gate."""
    ref = DEFAULT_HOLDOUT_REFERENCE
    if ref.split("/")[-1].lower() in model_id.lower():
        ref = _HOLDOUT_REFERENCE_ALT
    return [model_id, ref]


def _gate_score(field_name: str, predictions: list[tuple[Any, Any]]) -> tuple[float, int]:
    """The production gate metric (F1 for list fields, accuracy for categorical)
    computed over (predicted, truth) pairs -- the SAME metric the dashboard and
    supervisor gate on, so "what the optimizer chases" == "what the gate checks".
    """
    rows = [{"predicted": p, "truth": t} for p, t in predictions]
    gm = analytics.gate_metrics(field_name, rows)
    return gm["metric"], gm["n"]


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


def train_val_split(records: list[dict], val_size: int = DEFAULT_VAL_SIZE, seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Splits off a small, fixed-size validation set (NOT a fraction of the
    whole pool, which can be thousands of records) used to consistently
    compare every candidate instruction; the remainder is the train pool that
    minibatches are sampled from.
    """
    shuffled = records[:]
    random.Random(seed).shuffle(shuffled)
    val_size = min(val_size, max(1, len(shuffled) // 2))
    return shuffled[val_size:], shuffled[:val_size]


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
            if conn is not None and prompt_version_id is not None:
                db.add_run(conn, **run_kwargs, raw_response=None, parsed_value=None, score=0.0,
                           is_correct=0, latency_ms=None, prompt_tokens=None, completion_tokens=None,
                           cost_usd=None, error=str(resp))
            continue

        try:
            value, meta = parse_field_response(field_name, resp.content)
        except ParseError as exc:
            scores.append(0.0)
            failures.append({"id": rec["id"], "predicted": None, "truth": rec["ground_truth"],
                              "explanation": f"call/parse failed: {exc}", "excerpt": None})
            if conn is not None and prompt_version_id is not None:
                db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=None, score=0.0,
                           is_correct=0, latency_ms=resp.latency_ms, prompt_tokens=resp.prompt_tokens,
                           completion_tokens=resp.completion_tokens, cost_usd=resp.cost_usd, error=str(exc))
            continue

        verified = scoring.verify_excerpt(meta.get("excerpt"), source)
        result = scoring.score_field(field_name, value, rec["ground_truth"], excerpt_verified=verified)
        predictions.append((value, rec["ground_truth"]))
        # The optimizer optimizes the honesty-adjusted score (partial credit for
        # honest abstention, and a penalty for a value cited with a fabricated
        # excerpt), not the raw score, so it prefers calibrated honesty over
        # confident wrong guesses. Raw `score` is still stored for
        # display/aggregates.
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
        if conn is not None and prompt_version_id is not None:
            db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value,
                       excerpt=meta.get("excerpt"), notes=meta.get("notes"),
                       excerpt_verified=(None if verified is None else int(verified)),
                       confidence=meta.get("confidence"),
                       outcome=result.outcome, honesty_score=result.honesty_score,
                       score=result.score, is_correct=int(result.is_correct), latency_ms=resp.latency_ms,
                       prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                       cost_usd=resp.cost_usd,
                       co2e_grams=carbon.estimate_co2e_grams(model_id, resp.completion_tokens, resp.latency_ms),
                       error=None)

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
) -> tuple[str, str | None]:
    """Calls the reflector model and returns (revised_instruction, diagnosis).
    `avoid` is a list of {"instruction", "diagnosis"} dicts for recently
    rejected candidates, so the reflector doesn't propose the same dead end
    twice. When `bold` is set (after repeated rejections) the reflector is asked
    for a substantially different rewrite and is shown `successes` (cases the
    current instruction gets right) so the bolder rewrite doesn't break them.
    """
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
    base_prompt = (
        f"FIELD BEING EXTRACTED: {field_name}\n\n"
        f"CURRENT INSTRUCTION:\n{current_instruction}\n\n"
        f"FAILURE CASES (predicted vs. expected ground truth):\n{cases_text}"
        f"{confusion_text}"
        f"{keep_text}"
        f"{avoid_text}\n\n"
        "RESPOND IN VALID JSON:\n"
        "{\n"
        '    "diagnosis": "<1-2 sentences on the main failure pattern you see>",\n'
        '    "revised_instruction": "<the new instruction text>"\n'
        "}\n"
    )
    # Reasoning-capable reflectors (e.g. Claude Sonnet) can spend their whole
    # token budget "thinking" and then return null/truncated content, so give a
    # generous budget and retry a couple of times, nudging harder for JSON-only
    # output on retries. Without this a transient empty/non-JSON reply wastes a
    # whole optimizer iteration.
    last_err: Exception | None = None
    system_prompt = _REFLECTOR_SYSTEM_BOLD if bold else _REFLECTOR_SYSTEM
    for attempt in range(max_attempts):
        user_prompt = base_prompt
        if attempt > 0:
            user_prompt += (
                "\n\nIMPORTANT: Output ONLY the raw JSON object shown above — no preamble, no "
                "commentary, no markdown code fences, and no step-by-step reasoning before it."
            )
        resp = gateway.call_model(
            reflector_model,
            system_prompt,
            user_prompt,
            temperature=(0.9 if bold else 0.7) if attempt == 0 else 0.3,
            max_tokens=2000,
        )
        try:
            obj = parse_json_object(resp.content)
        except ParseError as exc:
            last_err = exc
            continue
        revised = obj.get("revised_instruction")
        if revised and str(revised).strip():
            return str(revised).strip(), obj.get("diagnosis")
        last_err = ParseError(
            f"Reflector did not return a usable revised_instruction: {resp.content[:300]!r}"
        )
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
    if holdout_models is None:
        holdout_models = _default_holdout_models(model_id)

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        baseline_pv = prompt_store.get_or_create_baseline(conn, project_id, field_name, model_id=model_id)
        all_records = db.get_records_with_field(conn, project_id, field_name)

    if baseline_pv is None:
        raise RuntimeError(f"Failed to create/load a baseline prompt version for field={field_name}.")
    if len(all_records) < 4:
        raise ValueError(f"Not enough ground-truthed records for field={field_name} to optimize (need >= 4).")

    with db.get_conn() as conn:
        job_id = db.start_job(conn, project_id, field_name, model_id, kind="optimization", total=max_iterations)

    try:
        result = _run_optimization(
            field_name=field_name, model_id=model_id, reflector_model=reflector_model,
            project_id=project_id, baseline_pv=baseline_pv, all_records=all_records, batch_id=batch_id,
            max_iterations=max_iterations, no_improve_limit=no_improve_limit,
            minibatch_size=minibatch_size, val_size=val_size, holdout_size=holdout_size,
            holdout_models=holdout_models, bold_after=bold_after, seed=seed, verbose=verbose,
            max_workers=max_workers, candidates_per_iteration=candidates_per_iteration,
            history_window=history_window, job_id=job_id,
        )
    except Exception as exc:
        with db.get_conn() as conn:
            db.finish_job(conn, job_id, status="failed", error=str(exc))
        raise
    with db.get_conn() as conn:
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
        metric, _n = _gate_score(field_name, outcome.predictions)
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
    with db.get_conn() as conn:
        baseline_outcome = evaluate_instruction(
            field_name, best_instruction, val, model_id, conn=conn, project_id=project_id,
            prompt_version_id=best_pv_id, batch_id=batch_id, max_workers=max_workers,
        )
    best_score = baseline_outcome.mean_score  # honesty score, used only to rank candidates cheaply
    best_gate, best_gate_n = _gate_score(field_name, baseline_outcome.predictions)
    best_holdout_avg = 0.0
    if gen_gate:
        with db.get_conn() as conn:
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

    with db.get_conn() as conn:
        for it in range(1, max_iterations + 1):
            if job_id is not None:
                db.update_job_progress(conn, job_id, it - 1)
            minibatch = rnd.sample(train, k=min(minibatch_size, len(train)))
            train_outcome = evaluate_instruction(
                field_name, best_instruction, minibatch, model_id, conn=conn, project_id=project_id,
                prompt_version_id=best_pv_id, batch_id=batch_id, max_workers=max_workers,
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
            candidates: list[dict[str, Any]] = []
            for k in range(candidates_per_iteration):
                try:
                    candidate_instruction, diagnosis = propose_revision(
                        field_name, best_instruction, train_outcome.failures, reflector_model,
                        avoid=avoid, bold=bold, successes=train_outcome.successes,
                        field_value_type=field_value_type,
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
                    field_name, candidate_instruction, val, model_id, conn=conn, project_id=project_id,
                    prompt_version_id=pv["id"], batch_id=batch_id, max_workers=max_workers,
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
            cand_gate, cand_gate_n = _gate_score(field_name, candidate_outcome.predictions)
            passes_val = cand_gate > best_gate + IMPROVEMENT_EPSILON
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
                accepted = cand_holdout_avg >= best_holdout_avg - IMPROVEMENT_EPSILON
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
