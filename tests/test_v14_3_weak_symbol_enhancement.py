from __future__ import annotations

import pandas as pd

from mt5_ai_bridge.v12_weak_symbol_profile import adjusted_v12_risk_percent, apply_weak_symbol_profile


def row(symbol: str, engine: str, hour: int, risk: float = 0.25) -> dict:
    return {
        "entry_time": pd.Timestamp(f"2020-01-02 {hour:02d}:00:00", tz="UTC"),
        "exit_time": pd.Timestamp(f"2020-01-02 {(hour + 1) % 24:02d}:00:00", tz="UTC"),
        "symbol": symbol,
        "engine": engine,
        "setup": "TEST",
        "side": "BUY",
        "risk_percent": risk,
        "r_multiple": 1.0,
    }


def test_eurusd_core_receives_bounded_full_risk() -> None:
    risk, tier = adjusted_v12_risk_percent(row("EURUSD", "EURUSD_SWING_CORE", 12))
    assert risk == 0.35
    assert tier == "EURUSD_CORE_FULL"


def test_eurusd_retest_stays_enabled_at_micro_risk() -> None:
    risk, tier = adjusted_v12_risk_percent(row("EURUSD", "EURUSD_SWING_RETEST", 12, 0.10))
    assert risk == 0.05
    assert tier == "EURUSD_RETEST_MICRO"


def test_audusd_08utc_is_full_and_04utc_is_micro() -> None:
    full, full_tier = adjusted_v12_risk_percent(row("AUDUSD", "AUDUSD_TREND_PULLBACK", 8))
    micro, micro_tier = adjusted_v12_risk_percent(row("AUDUSD", "AUDUSD_TREND_PULLBACK", 4))
    assert full == 0.35
    assert micro == 0.05
    assert full_tier == "AUDUSD_08UTC_FULL"
    assert micro_tier == "AUDUSD_04UTC_MICRO"


def test_usdjpy_quality_hours_are_full_and_other_hours_are_micro() -> None:
    midnight, midnight_tier = adjusted_v12_risk_percent(row("USDJPY", "USDJPY_SAFE_HAVEN_BREAKOUT", 0, 0.20))
    sixteen, _ = adjusted_v12_risk_percent(row("USDJPY", "USDJPY_SAFE_HAVEN_BREAKOUT", 16, 0.20))
    twenty, _ = adjusted_v12_risk_percent(row("USDJPY", "USDJPY_SAFE_HAVEN_BREAKOUT", 20, 0.20))
    other, tier = adjusted_v12_risk_percent(row("USDJPY", "USDJPY_SAFE_HAVEN_BREAKOUT", 12, 0.20))
    assert midnight == 0.25
    assert sixteen == 0.25
    assert twenty == 0.25
    assert other == 0.05
    assert midnight_tier == "USDJPY_00_16_20UTC_FULL"
    assert tier == "USDJPY_OTHER_HOUR_MICRO"


def test_unrelated_symbols_are_unchanged() -> None:
    source = pd.DataFrame([
        row("GBPUSD", "GBPUSD_V10_PRECISION", 12, 0.40),
        row("GBPJPY", "GBPJPY_SWING_CORE", 12, 0.15),
    ])
    enhanced = apply_weak_symbol_profile(source)
    assert enhanced["risk_percent"].tolist() == [0.40, 0.15]
    assert enhanced["v12_quality_tier"].tolist() == ["UNCHANGED", "UNCHANGED"]
