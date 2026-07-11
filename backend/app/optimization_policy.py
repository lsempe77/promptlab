"""Cost-benefit policy for when to STOP auto-optimizing a (field, model) pair.

Prompt-instruction optimization has steep diminishing returns: once a pair has
tried several candidates with no accepted gain, further LLM-reflection is almost
always spending money on noise (empirically ~3% of candidates are accepted once
a field matures, and the reflector can only reword a 2-5 sentence instruction —
it cannot add examples, change the pipeline, or fix the ground truth). This
module centralises the stop rule so the supervisor (which decides what to
enqueue) and the API (which surfaces status to humans) agree.

Balanced stance:
  * hard lifetime cap of 25 candidates per (field, model);
  * declare a plateau after 3 consecutive candidates with no accepted gain,
    once the pair has had at least 5 attempts (so a fresh pair gets a real shot);
  * if plateaued while still >5 pts below the gate, treat it as TASK-LIMITED
    (a ground-truth / taxonomy problem, not prompt-fixable) and flag for a human.
"""
from __future__ import annotations

LIFETIME_CAP = 25       # total optimizer candidates per (field, model) before we stop
PLATEAU_WINDOW = 3      # consecutive candidates with no accepted gain => plateau
MIN_ATTEMPTS = 5        # don't declare a plateau before this many attempts
TASK_LIMITED_GAP = 0.05  # plateaued and >5 pts below gate => task-limited (human review)

# Statuses returned by decide(). 'optimize' means keep going; the rest are stops.
STOP_STATUSES = ("budget", "plateaued", "task_limited")


def decide(n_iters: int, iters_since_accept: int, best_gate: float,
           gate_threshold: float) -> tuple[bool, str, str]:
    """Return (should_optimize, status, reason).

    status is one of: 'optimize', 'budget', 'plateaued', 'task_limited'.
      n_iters:            total optimizer iterations logged for this (field, model).
      iters_since_accept: iterations since the last accepted candidate
                          (== n_iters if none was ever accepted).
      best_gate/threshold: current best gate metric and the bar (both 0-1).
    """
    gap = gate_threshold - best_gate
    if n_iters >= LIFETIME_CAP:
        return (False, "budget",
                f"reached the {LIFETIME_CAP}-candidate budget without clearing the gate "
                f"({gap * 100:.0f} pts short) — stopping; needs human review")
    if n_iters >= MIN_ATTEMPTS and iters_since_accept >= PLATEAU_WINDOW:
        if gap > TASK_LIMITED_GAP:
            return (False, "task_limited",
                    f"plateaued {gap * 100:.0f} pts below the gate after {n_iters} candidates — "
                    f"likely ground-truth/taxonomy limited, not prompt-fixable; needs human review")
        return (False, "plateaued",
                f"no accepted gain in the last {iters_since_accept} candidates, {gap * 100:.0f} pts "
                f"below the gate — diminishing returns; stopping")
    return (True, "optimize", "")
