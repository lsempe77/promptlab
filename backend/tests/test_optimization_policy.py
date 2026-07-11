"""Tests for backend.app.optimization_policy — the stop/continue decision rule.

Covers: 'optimize' (keep going), 'budget' (lifetime cap), 'plateaued'
(no gain within window), 'task_limited' (plateaued far below gate).
"""
from __future__ import annotations

import pytest

from backend.app.optimization_policy import (
    LIFETIME_CAP,
    MIN_ATTEMPTS,
    PLATEAU_WINDOW,
    STOP_STATUSES,
    TASK_LIMITED_GAP,
    decide,
)


class TestDecideOptimize:
    def test_fresh_pair(self):
        should, status, _ = decide(n_iters=0, iters_since_accept=0, best_gate=0.0,
                                   gate_threshold=0.90)
        assert should is True
        assert status == "optimize"

    def test_below_min_attempts(self):
        # Even with plateau window worth of no-accept, if below MIN_ATTEMPTS
        # we keep going.
        should, status, _ = decide(n_iters=3, iters_since_accept=3, best_gate=0.80,
                                   gate_threshold=0.90)
        assert should is True
        assert status == "optimize"

    def test_recently_accepted(self):
        # Had 10 attempts, but accepted recently (iters_since_accept < window)
        should, status, _ = decide(n_iters=10, iters_since_accept=1, best_gate=0.85,
                                   gate_threshold=0.90)
        assert should is True
        assert status == "optimize"

    def test_at_min_attempts_no_plateau(self):
        should, status, _ = decide(n_iters=5, iters_since_accept=2, best_gate=0.85,
                                   gate_threshold=0.90)
        assert should is True
        assert status == "optimize"


class TestDecideBudget:
    def test_at_lifetime_cap(self):
        should, status, reason = decide(
            n_iters=LIFETIME_CAP, iters_since_accept=5, best_gate=0.80,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "budget"
        assert "budget" in reason.lower()

    def test_above_lifetime_cap(self):
        should, status, _ = decide(
            n_iters=LIFETIME_CAP + 5, iters_since_accept=3, best_gate=0.85,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "budget"


class TestDecidePlateaued:
    def test_plateaued_within_gate(self):
        # >= MIN_ATTEMPTS, plateau window reached, but close enough to gate
        should, status, reason = decide(
            n_iters=10, iters_since_accept=PLATEAU_WINDOW, best_gate=0.88,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "plateaued"
        assert "no accepted gain" in reason.lower()

    def test_plateaued_exactly_at_window(self):
        should, status, _ = decide(
            n_iters=MIN_ATTEMPTS, iters_since_accept=PLATEAU_WINDOW, best_gate=0.88,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "plateaued"

    def test_plateaued_above_window(self):
        should, status, _ = decide(
            n_iters=15, iters_since_accept=PLATEAU_WINDOW + 2, best_gate=0.87,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "plateaued"


class TestDecideTaskLimited:
    def test_task_limited_far_below_gate(self):
        # Plateaued and gap > TASK_LIMITED_GAP (0.05)
        should, status, reason = decide(
            n_iters=10, iters_since_accept=PLATEAU_WINDOW, best_gate=0.80,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "task_limited"
        assert "human review" in reason.lower()

    def test_task_limited_boundary(self):
        # gap = 0.90 - 0.84 = 0.06 > 0.05
        should, status, _ = decide(
            n_iters=10, iters_since_accept=PLATEAU_WINDOW, best_gate=0.84,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "task_limited"

    def test_not_task_limited_at_boundary(self):
        # gap = 0.90 - 0.86 = 0.04 < TASK_LIMITED_GAP (0.05) -> plain plateaued, not task_limited
        should, status, _ = decide(
            n_iters=10, iters_since_accept=PLATEAU_WINDOW, best_gate=0.86,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "plateaued"  # close enough, just plateaued


class TestStopStatuses:
    def test_stop_statuses_contains_budget(self):
        assert "budget" in STOP_STATUSES

    def test_stop_statuses_contains_plateaued(self):
        assert "plateaued" in STOP_STATUSES

    def test_stop_statuses_contains_task_limited(self):
        assert "task_limited" in STOP_STATUSES

    def test_optimize_not_in_stop_statuses(self):
        assert "optimize" not in STOP_STATUSES


class TestDecidePriority:
    def test_budget_takes_priority_over_plateau(self):
        # At cap AND plateaued -> budget wins (checked first)
        should, status, _ = decide(
            n_iters=LIFETIME_CAP, iters_since_accept=PLATEAU_WINDOW + 10,
            best_gate=0.80, gate_threshold=0.90,
        )
        assert should is False
        assert status == "budget"

    def test_task_limited_takes_priority_over_plateau(self):
        # Plateaued AND far below gate -> task_limited (checked before plain plateau)
        should, status, _ = decide(
            n_iters=10, iters_since_accept=PLATEAU_WINDOW, best_gate=0.70,
            gate_threshold=0.90,
        )
        assert should is False
        assert status == "task_limited"
