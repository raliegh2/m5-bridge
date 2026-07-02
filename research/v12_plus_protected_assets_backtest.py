"""Rerun the V12 validated-asset study with the original protection policy.

The first regeneration intentionally applied the adaptive guard broadly so its
impact could be measured. The profitable V12 configuration, however, declared
several independently validated engines as protected. V13 also admitted
AUDUSD/USDJPY only after untouched validation. This runner preserves those
validated engines at their frozen base risk while leaving the weaker/unprotected
families under the repaired cooldown and recovery-probe policy.
"""
from __future__ import annotations

from pathlib import Path

import v12_plus_validated_assets_backtest as study

PROTECTED_ENGINES = frozenset(
    {
        study.PRECISION_ENGINE,
        "GBPUSD_SWING_RETEST",
        "GBPJPY_SWING_RETEST",
        "EURUSD_SWING_CORE",
        "GBPJPY_SWING_CORE",
        "AUDUSD_TREND_PULLBACK",
        "USDJPY_SAFE_HAVEN_BREAKOUT",
    }
)

_ORIGINAL_GUARD_DECISION = study._guard_decision


def protected_guard_decision(
    engine,
    history,
    now,
    disabled_until,
    probe_active_until,
    config,
):
    if engine in PROTECTED_ENGINES:
        return study.GuardDecision(1.0, "validated_protected")
    return _ORIGINAL_GUARD_DECISION(
        engine,
        history,
        now,
        disabled_until,
        probe_active_until,
        config,
    )


def main() -> None:
    study.OUT = (
        Path(__file__).resolve().parents[1]
        / "research"
        / "v12_plus_protected_assets_output"
    )
    study.OUT.mkdir(parents=True, exist_ok=True)
    study._guard_decision = protected_guard_decision
    study.main()


if __name__ == "__main__":
    main()
