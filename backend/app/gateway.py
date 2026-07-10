"""Single unified function for calling any model via OpenRouter's
OpenAI-compatible chat-completions endpoint. Deliberately provider-agnostic:
swapping models is just a string (`model_id`), no per-provider SDKs.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import config

DEFAULT_MAX_CONCURRENCY = 6
MAX_RETRIES = 3  # on HTTP 429 (rate limited) only
RETRY_BASE_DELAY_S = 1.5


class GatewayError(RuntimeError):
    """Raised when OpenRouter returns an error or an unusable response."""


@dataclass
class ModelResponse:
    model_id: str
    content: str
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: float | None
    latency_ms: int
    raw: dict[str, Any]
    # Mean per-token probability of the completion (exp of the mean token
    # logprob), 0-1, when the model/provider returned logprobs -- else None.
    # A model-intrinsic confidence signal (higher = the model was less
    # "surprised" by its own output). Not all OpenRouter providers expose it.
    logprob_confidence: float | None = None


def call_model(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 2048,
    json_mode: bool = True,
    logprobs: bool = False,
    timeout: float = 60.0,
) -> ModelResponse:
    """Call `model_id` via OpenRouter and return its text response plus
    token/cost/latency metadata. Raises GatewayError on failure. Retries a
    few times with backoff on HTTP 429 (rate limited) only.

    If `logprobs=True`, requests per-token logprobs and derives a
    `logprob_confidence` (mean per-token probability). Providers that don't
    support logprobs simply return none, leaving `logprob_confidence=None`.
    """
    if not config.OPENROUTER_API_KEY:
        raise GatewayError(
            "OPENROUTER_API_KEY is not set. Add it to backend/.env (see .env.example)."
        )

    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if logprobs:
        payload["logprobs"] = True

    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/3ie-dep-prompt-lab",
        "X-Title": "3ie DEP Prompt Lab",
    }

    start = time.perf_counter()
    attempt = 0
    logprobs_retry_done = False
    while True:
        try:
            resp = httpx.post(
                f"{config.OPENROUTER_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise GatewayError(f"Request to OpenRouter failed: {exc}") from exc

        if resp.status_code == 429 and attempt < MAX_RETRIES:
            time.sleep(RETRY_BASE_DELAY_S * (2 ** attempt))
            attempt += 1
            continue
        # Reasoning models (e.g. ~openai/gpt-latest) reject `logprobs` with a 400.
        # Rather than lose that model's data entirely, retry once without it
        # (the run just won't have a logprob_confidence).
        if (
            resp.status_code == 400
            and payload.get("logprobs")
            and not logprobs_retry_done
            and "logprob" in resp.text.lower()
        ):
            payload.pop("logprobs", None)
            logprobs_retry_done = True
            continue
        break
    latency_ms = int((time.perf_counter() - start) * 1000)

    if resp.status_code != 200:
        raise GatewayError(f"OpenRouter {resp.status_code} for model={model_id}: {resp.text[:500]}")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise GatewayError(f"OpenRouter returned no choices for model={model_id}: {data}")

    choice = choices[0]
    content = choice["message"]["content"]
    usage = data.get("usage") or {}

    logprob_confidence = _mean_token_probability(choice.get("logprobs"))

    return ModelResponse(
        model_id=model_id,
        content=content,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        cost_usd=usage.get("cost"),
        latency_ms=latency_ms,
        raw=data,
        logprob_confidence=logprob_confidence,
    )


def _mean_token_probability(logprobs_obj: Any) -> float | None:
    """exp(mean token logprob) over the completion's tokens, i.e. the geometric
    mean per-token probability (0-1). Returns None if the provider didn't
    return usable logprobs."""
    if not logprobs_obj or not isinstance(logprobs_obj, dict):
        return None
    tokens = logprobs_obj.get("content") or []
    lps = [t["logprob"] for t in tokens if isinstance(t, dict) and t.get("logprob") is not None]
    if not lps:
        return None
    return math.exp(sum(lps) / len(lps))


def call_model_batch(
    jobs: list[dict[str, Any]],
    max_workers: int = DEFAULT_MAX_CONCURRENCY,
    on_complete: Callable[[int, "ModelResponse | GatewayError"], None] | None = None,
) -> list[ModelResponse | GatewayError]:
    """Runs `call_model(**job)` for every job in `jobs` concurrently (threads
    \u2014 this is I/O-bound HTTP work, no need for asyncio) and returns results
    in the SAME ORDER as `jobs`. Failures are returned as `GatewayError`
    instances rather than raised, so callers can zip results back onto their
    record/model metadata even when some calls fail.

    If `on_complete` is given, it's called (from the main thread, one at a
    time, never concurrently) as soon as each job finishes -- in COMPLETION
    order, not job order -- so callers can persist each result immediately
    (e.g. write it to SQLite) instead of waiting for the whole batch, which
    would otherwise lose every result if the process crashes/is killed
    partway through a large batch.
    """
    results: list[ModelResponse | GatewayError | None] = [None] * len(jobs)

    def _run(job: dict[str, Any]) -> ModelResponse | GatewayError:
        try:
            return call_model(**job)
        except GatewayError as exc:
            return exc
        except Exception as exc:  # noqa: BLE001
            # A malformed-but-200 response (KeyError/JSONDecodeError/etc.) must not
            # escape the worker thread and abort the whole batch -- surface it as a
            # per-job error like any other failure so the rest still persist.
            return GatewayError(f"Unexpected error for model={job.get('model_id')!r}: {exc!r}")

    if not jobs:
        return []

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
        future_to_index = {ex.submit(_run, job): i for i, job in enumerate(jobs)}
        for fut in as_completed(future_to_index):
            i = future_to_index[fut]
            result = fut.result()
            results[i] = result
            if on_complete is not None:
                on_complete(i, result)

    return results  # type: ignore[return-value]
