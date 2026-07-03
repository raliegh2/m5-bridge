"""Corrected execution wrapper for V12 weak-engine optimization.

Captures the original V12 guard before the optimization runner monkey-patches
``study._guard_decision``. This prevents recursive self-calls in the adaptive
scenarios and preserves an exact baseline comparison.
"""
from __future__ import annotations

from pathlib import Path

import v12_weak_engine_optimization as optimization

ORIGINAL_GUARD_DECISION = optimization.study._guard_decision


def fixed_optimized_guard_decision(
    engine,
    history,
    now,
    disabled_until,
    probe_active_until,
    config,
):
    if engine in optimization.STRONG_PROTECTED_ENGINES:
        return optimization.study.GuardDecision(1.0, "strong_engine_protected")
    return ORIGINAL_GUARD_DECISION(
        engine,
        history,
        now,
        disabled_until,
        probe_active_until,
        optimization.OPTIMIZED_GUARD
        if engine in optimization.MARGINAL_ENGINES
        else config,
    )


def main() -> None:
    optimization.OUT = (
        Path(__file__).resolve().parents[1]
        / "research"
        / "v12_weak_engine_optimization_v2_output"
    )
    optimization.OUT.mkdir(parents=True, exist_ok=True)
    optimization.optimized_guard_decision = fixed_optimized_guard_decision
    optimization.main()


if __name__ == "__main__":
    main()
