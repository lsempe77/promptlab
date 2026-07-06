"""Estimate the environmental footprint (CO2e, energy, water) of a single LLM
call from its output-token count, using EcoLogits' methodology with the
world-average electricity mix.

Estimates are order-of-magnitude: parameter counts for closed models are
best-effort, and EcoLogits itself flags reduced precision for unreleased
architectures. Good enough to make the relative footprint visible and
accountable; not a certified LCA.
"""
from __future__ import annotations

from ecologits.impacts.llm import compute_llm_impacts
from ecologits.tracers.utils import electricity_mixes

_MIX = electricity_mixes.find_electricity_mix(zone="WOR")  # world-average grid
_PUE = 1.2   # datacenter power usage effectiveness (typical hyperscaler)
_WUE = 1.8   # datacenter water usage effectiveness (L/kWh)

# Best-effort (active_billions, total_billions) parameter counts. MoE models
# have active < total. Rough by necessity (closed models don't disclose sizes);
# used only for a relative footprint estimate, matched by substring of the id.
_PARAMS: dict[str, tuple[float, float]] = {
    "gpt-mini": (8, 8),
    "gpt-latest": (100, 440),
    "claude-haiku": (8, 8),
    "claude-sonnet": (70, 70),
    "claude-opus": (300, 300),
    "gemini-flash": (8, 32),
    "gemini-pro": (100, 440),
    "deepseek-v4-flash": (3, 30),
    "deepseek-v4-pro": (37, 671),
    "deepseek": (37, 671),
    "qwen3-30b-a3b": (3, 30),
    "qwen3-235b-a22b": (22, 235),
    "llama-4-scout": (17, 109),
    "llama-4-maverick": (17, 400),
    "llama-3.3-70b": (70, 70),
    "mistral-small": (24, 24),
    "mistral-medium": (40, 40),
    "glm-4.7-flash": (9, 9),
    "glm-5": (32, 355),
    "glm": (32, 355),
    "kimi": (32, 1000),
}
_DEFAULT = (8.0, 70.0)


def _params(model_id: str) -> tuple[float, float]:
    m = model_id.lower()
    for key, val in _PARAMS.items():
        if key in m:
            return val
    return _DEFAULT


def _midpoint(v) -> float:
    lo = getattr(v, "min", None)
    if lo is not None:
        return (v.min + v.max) / 2.0
    return float(v)


def estimate_co2e_grams(
    model_id: str, completion_tokens: int | None, latency_ms: int | None = None
) -> float | None:
    """Grams of CO2-equivalent for one generation, or None if not estimable."""
    if not completion_tokens:
        return None
    active, total = _params(model_id)
    try:
        imp = compute_llm_impacts(
            model_active_parameter_count=float(active),
            model_total_parameter_count=float(total),
            output_token_count=int(completion_tokens),
            if_electricity_mix_adpe=_MIX.adpe,
            if_electricity_mix_pe=_MIX.pe,
            if_electricity_mix_gwp=_MIX.gwp,
            if_electricity_mix_wue=_MIX.wue,
            datacenter_pue=_PUE,
            datacenter_wue=_WUE,
            request_latency=((latency_ms or 0) / 1000.0) or None,
        )
        return _midpoint(imp.gwp.value) * 1000.0  # kgCO2eq -> grams
    except Exception:
        return None
