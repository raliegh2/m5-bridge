from __future__ import annotations

import pandas as pd

import v12_plus_validated_assets_backtest as study
from v12_plus_protected_assets_backtest import (
    PROTECTED_ENGINES,
    protected_guard_decision,
)


def test_original_v12_protected_engines_are_preserved() -> None:
    expected = {
        study.PRECISION_ENGINE,
        "GBPUSD_SWING_RETEST",
        "GBPJPY_SWING_RETEST",
        "EURUSD_SWING_CORE",
        "GBPJPY_SWING_CORE",
    }
    assert expected.issubset(PROTECTED_ENGINES)


def test_independently_validated_new_assets_are_protected() -> None:
    assert "AUDUSD_TREND_PULLBACK" in PROTECTED_ENGINES
    assert "USDJPY_SAFE_HAVEN_BREAKOUT" in PROTECTED_ENGINES


def test_protected_engine_stays_at_full_base_risk() -> None:
    decision = protected_guard_decision(
        "AUDUSD_TREND_PULLBACK",
        {"AUDUSD_TREND_PULLBACK": [-1.0] * 20},
        pd.Timestamp("2022-01-01", tz="UTC"),
        {},
        {},
        study.GuardConfig(),
    )
    assert decision.multiplier == 1.0
    assert decision.reason == "validated_protected"


def test_unprotected_engine_keeps_repaired_guard() -> None:
    now = pd.Timestamp("2022-01-01", tz="UTC")
    decision = protected_guard_decision(
        "GBPUSD_SWING_CORE",
        {},
        now,
        {"GBPUSD_SWING_CORE": now - pd.Timedelta(seconds=1)},
        {},
        study.GuardConfig(),
    )
    assert decision.multiplier == 0.5
    assert decision.is_probe
