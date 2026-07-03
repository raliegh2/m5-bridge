from __future__ import annotations

import pandas as pd

from v12_weak_engine_optimization import (
    CANDIDATE_LOSER_ENGINES,
    MARGINAL_ENGINES,
    STRONG_PROTECTED_ENGINES,
    filter_disabled,
    loser_policy,
    optimized_guard_decision,
)
import v12_plus_validated_assets_backtest as study


def test_known_weak_engines_are_targeted() -> None:
    assert "GBPUSD_SWING_CORE" in CANDIDATE_LOSER_ENGINES
    assert "GBPJPY_SWING_RETEST" in CANDIDATE_LOSER_ENGINES
    assert "AUDUSD_TREND_PULLBACK" in MARGINAL_ENGINES
    assert "USDJPY_SAFE_HAVEN_BREAKOUT" in MARGINAL_ENGINES


def test_strong_engines_remain_protected() -> None:
    assert study.PRECISION_ENGINE in STRONG_PROTECTED_ENGINES
    assert "EURUSD_SWING_CORE" in STRONG_PROTECTED_ENGINES
    assert "GBPJPY_SWING_CORE" in STRONG_PROTECTED_ENGINES


def test_loser_policy_disables_non_robust_engine() -> None:
    report = {
        "engines": {
            "GBPUSD_SWING_CORE": {
                "development": {"trades": 30, "net_r": -2.0, "profit_factor": 0.9},
                "confirmation": {"trades": 10, "net_r": 1.0, "profit_factor": 1.1},
            },
            "GBPJPY_SWING_RETEST": {
                "development": {"trades": 30, "net_r": 2.0, "profit_factor": 1.1},
                "confirmation": {"trades": 10, "net_r": 1.0, "profit_factor": 1.1},
            },
        }
    }
    policy = loser_policy(report)
    assert policy["GBPUSD_SWING_CORE"] == "disable"
    assert policy["GBPJPY_SWING_RETEST"] == "keep"


def test_filter_disabled_removes_only_selected_engine() -> None:
    frame = pd.DataFrame([
        {"engine": "GBPUSD_SWING_CORE", "value": 1},
        {"engine": "EURUSD_SWING_CORE", "value": 2},
    ])
    result = filter_disabled(frame, {"GBPUSD_SWING_CORE": "disable"})
    assert list(result["engine"]) == ["EURUSD_SWING_CORE"]


def test_marginal_engine_uses_repaired_guard() -> None:
    now = pd.Timestamp("2022-01-01", tz="UTC")
    decision = optimized_guard_decision(
        "USDJPY_SAFE_HAVEN_BREAKOUT",
        {},
        now,
        {"USDJPY_SAFE_HAVEN_BREAKOUT": now - pd.Timedelta(seconds=1)},
        {},
        study.GuardConfig(),
    )
    assert decision.is_probe
    assert decision.multiplier == 0.35
