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

from . import db, gateway, prompt_store, prompts, scoring
from .corpus import read_md
from .parsing import ParseError, parse_field_response, parse_json_object

DEFAULT_MINIBATCH_SIZE = 8
DEFAULT_VAL_SIZE = 12
IMPROVEMENT_EPSILON = 0.01  # candidate must beat incumbent by at least this much


@dataclass
class EvalOutcome:
    mean_score: float
    n: int
    failures: list[dict[str, Any]]


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

    usable_records: list[dict] = []
    jobs: list[dict[str, Any]] = []
    for rec in records:
        try:
            md_text = read_md(rec["md_path"])
        except OSError:
            continue
        system_prompt, user_prompt = prompts.build_prompt(field_name, rec["title"] or "", md_text, instruction=instruction)
        usable_records.append(rec)
        jobs.append({"model_id": model_id, "system_prompt": system_prompt, "user_prompt": user_prompt})

    responses = gateway.call_model_batch(jobs, max_workers=max_workers)

    for rec, resp in zip(usable_records, responses):
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

        result = scoring.score_field(field_name, value, rec["ground_truth"])
        scores.append(result.score)
        if not result.is_correct:
            failures.append({
                "id": rec["id"],
                "predicted": value,
                "truth": rec["ground_truth"],
                "explanation": result.explanation,
                "excerpt": meta.get("excerpt"),
            })
        if conn is not None and prompt_version_id is not None:
            db.add_run(conn, **run_kwargs, raw_response=resp.content, parsed_value=value,
                       score=result.score, is_correct=int(result.is_correct), latency_ms=resp.latency_ms,
                       prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                       cost_usd=resp.cost_usd, error=None)

    mean_score = sum(scores) / len(scores) if scores else 0.0
    return EvalOutcome(mean_score=mean_score, n=len(scores), failures=failures)


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


def _format_failures(failures: list[dict[str, Any]], max_cases: int) -> str:
    lines = []
    for c in failures[:max_cases]:
        lines.append(
            f"- record {c['id']}: predicted={c['predicted']!r}, expected={c['truth']!r} "
            f"({c['explanation']}); excerpt cited: {c['excerpt']!r}"
        )
    return "\n".join(lines) if lines else "(no failure cases in this sample)"


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


def propose_revision(
    field_name: str,
    current_instruction: str,
    failures: list[dict[str, Any]],
    reflector_model: str,
    max_cases: int = 8,
    avoid: list[dict[str, Any]] | None = None,
) -> tuple[str, str | None]:
    """Calls the reflector model and returns (revised_instruction, diagnosis).
    `avoid` is a list of {"instruction", "diagnosis"} dicts for recently
    rejected candidates, so the reflector doesn't propose the same dead end
    twice.
    """
    cases_text = _format_failures(failures, max_cases)
    avoid_text = _format_avoid_history(avoid or [])
    user_prompt = (
        f"FIELD BEING EXTRACTED: {field_name}\n\n"
        f"CURRENT INSTRUCTION:\n{current_instruction}\n\n"
        f"FAILURE CASES (predicted vs. expected ground truth):\n{cases_text}"
        f"{avoid_text}\n\n"
        "RESPOND IN VALID JSON:\n"
        "{\n"
        '    "diagnosis": "<1-2 sentences on the main failure pattern you see>",\n'
        '    "revised_instruction": "<the new instruction text, 2-5 sentences>"\n'
        "}\n"
    )
    resp = gateway.call_model(reflector_model, _REFLECTOR_SYSTEM, user_prompt, temperature=0.7, max_tokens=500)
    obj = parse_json_object(resp.content)
    revised = obj.get("revised_instruction")
    if not revised or not str(revised).strip():
        raise ParseError(f"Reflector did not return a usable revised_instruction: {resp.content[:300]!r}")
    return str(revised).strip(), obj.get("diagnosis")


def optimize_field(
    field_name: str,
    model_id: str,
    reflector_model: str,
    project_slug: str = "dep-extraction",
    max_iterations: int = 10,
    no_improve_limit: int = 3,
    minibatch_size: int = DEFAULT_MINIBATCH_SIZE,
    val_size: int = DEFAULT_VAL_SIZE,
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
    """
    batch_id = uuid.uuid4().hex[:8]

    with db.get_conn() as conn:
        project_id = db.get_project_id(conn, project_slug)
        baseline_pv = prompt_store.get_or_create_baseline(conn, project_id, field_name)
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
            minibatch_size=minibatch_size, val_size=val_size, seed=seed, verbose=verbose,
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
    seed: int,
    verbose: bool,
    max_workers: int,
    candidates_per_iteration: int,
    history_window: int,
    job_id: int | None,
) -> OptimizeResult:
    train, val = train_val_split(all_records, val_size=val_size, seed=seed)
    rnd = random.Random(seed)

    best_instruction = baseline_pv["template"]
    best_pv_id = baseline_pv["id"]
    with db.get_conn() as conn:
        baseline_outcome = evaluate_instruction(
            field_name, best_instruction, val, model_id, conn=conn, project_id=project_id,
            prompt_version_id=best_pv_id, batch_id=batch_id, max_workers=max_workers,
        )
    best_score = baseline_outcome.mean_score
    if verbose:
        print(f"[iter 0] baseline val_score={best_score:.3f} (n={baseline_outcome.n})")

    result = OptimizeResult(field_name=field_name, baseline_score=best_score, best_instruction=best_instruction, best_score=best_score)
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

            candidates: list[dict[str, Any]] = []
            for k in range(candidates_per_iteration):
                try:
                    candidate_instruction, diagnosis = propose_revision(
                        field_name, best_instruction, train_outcome.failures, reflector_model, avoid=avoid
                    )
                except (ParseError, gateway.GatewayError) as exc:
                    if verbose:
                        print(f"[iter {it}] reflector call {k + 1}/{candidates_per_iteration} failed: {exc}")
                    continue

                pv = prompt_store.add_version(
                    conn, project_id, field_name, candidate_instruction, parent_id=best_pv_id,
                    notes=f"iter {it} (candidate {k + 1}/{candidates_per_iteration}): {diagnosis or ''}"[:500],
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
            accepted = candidate_outcome.mean_score > best_score + IMPROVEMENT_EPSILON

            for c in candidates:
                is_winner_and_accepted = accepted and c is best_candidate
                db.set_prompt_version_accepted(conn, c["pv"]["id"], is_winner_and_accepted)
                if not is_winner_and_accepted:
                    rejected_history.append({"instruction": c["instruction"], "diagnosis": c["diagnosis"]})

            db.add_iteration(
                conn, project_id=project_id, field_name=field_name, iteration_num=it, prompt_version_id=pv["id"],
                model_id=model_id, mean_score=candidate_outcome.mean_score, n_records=candidate_outcome.n,
                feedback=diagnosis, accepted=int(accepted),
            )

            if verbose:
                tag = "ACCEPTED" if accepted else "rejected"
                n_tried = len(candidates)
                suffix = f" (best of {n_tried})" if n_tried > 1 else ""
                print(f"[iter {it}] val_score={candidate_outcome.mean_score:.3f} (best={best_score:.3f}) -> {tag}{suffix}")
                if diagnosis:
                    print(f"           diagnosis: {diagnosis}")

            result.iterations.append(IterationLog(
                iteration_num=it, candidate_instruction=candidate_instruction, diagnosis=diagnosis,
                val_score=candidate_outcome.mean_score, accepted=accepted, prompt_version_id=pv["id"],
            ))

            if accepted:
                best_instruction = candidate_instruction
                best_pv_id = pv["id"]
                best_score = candidate_outcome.mean_score
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
