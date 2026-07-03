from __future__ import annotations

import pandas as pd

from v12_targeted_weak_engine_optimization import (
    FULL_PROTECTED,
    FULL_SAMPLE_LOSERS,
    THIN_EDGE_ENGINES,
    filter_losers,
    targeted_guard_decision,
)
import v12_plus_validated_assets_backtest as study


def test_audusd_remains_fully_protected() -> None:
    assert "AUDUSD_TREND_PULLBACK" in FULL_PROTECTED
    decision = targeted_guard_decision(
        "AUDUSD_TREND_PULLBACK",
        {"AUDUSD_TREND_PULLBACK": [-1.0] * 20},
        pd.Timestamp("2022-01-01", tz="UTC"),
        {},
        {},
        study.GuardConfig(),
    )
    assert decision.multiplier == 1.0


def test_only_two_full_sample_losers_are_removed() -> None:
    assert FULL_SAMPLE_LOSERS == {
        "GBPUSD_SWING_CORE",
        "GBPJPY_SWING_RETEST",
    }
    frame = pd.DataFrame([
        {"engine": "GBPUSD_SWING_CORE"},
        {"engine": "GBPJPY_SWING_RETEST"},
        {"engine": "AUDUSD_TREND_PULLBACK"},
    ])
    result = filter_losers(frame)
    assert list(result["engine"]) == ["AUDUSD_TREND_PULLBACK"]


def test_thin_edge_engines_use_adaptive_guard() -> None:
    assert THIN_EDGE_ENGINES == {
        "USDJPY_SAFE_HAVEN_BREAKOUT",
        "EURUSD_SWING_RETEST",
    }
    now = pd.Timestamp("2022-01-01", tz="UTC")
    decision = targeted_guard_decision(
        "USDJPY_SAFE_HAVEN_BREAKOUT",
        {},
        now,
        {"USDJPY_SAFE_HAVEN_BREAKOUT": now - pd.Timedelta(seconds=1)},
        {},
        study.GuardConfig(),
    )
    assert decision.is_probe
    assert decision.multiplier == 0.35
