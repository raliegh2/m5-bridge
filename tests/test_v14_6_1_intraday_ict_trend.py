from __future__ import annotations

import pandas as pd
import pytest

from mt5_ai_bridge.v14_6_1_intraday_ict_trend import (
    PROFILES,
    simulate_partial_exit,
)


def frame(rows):
    output = pd.DataFrame(rows)
    output["end"] = pd.to_datetime(output["end"], utc=True)
    return output


def test_profiles_explicitly_allow_multiple_daily_entries() -> None:
    assert set(PROFILES) == {"GBPUSD", "GBPJPY", "AUDUSD"}
    for profiles in PROFILES.values():
        assert len(profiles) >= 3
        assert all(profile.max_trades_per_day >= 4 for profile in profiles)
        assert all(profile.cooldown_hours >= 1 for profile in profiles)


def test_stop_before_partial_is_full_loss() -> None:
    candles = frame(
        [
            {"end": "2026-01-01T10:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"end": "2026-01-01T11:00:00Z", "open": 100, "high": 100.4, "low": 98.9, "close": 99.2},
        ]
    )
    _, result = simulate_partial_exit(candles, 0, 1, 99.0, 2.0, 4, 0.5, 1.0, True)
    assert result == pytest.approx(-1.0)


def test_partial_then_break_even_locks_realized_profit() -> None:
    candles = frame(
        [
            {"end": "2026-01-01T10:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"end": "2026-01-01T11:00:00Z", "open": 100, "high": 101.2, "low": 99.5, "close": 100.8},
            {"end": "2026-01-01T12:00:00Z", "open": 100.8, "high": 100.9, "low": 99.9, "close": 100.1},
        ]
    )
    _, result = simulate_partial_exit(candles, 0, 1, 99.0, 2.0, 4, 0.5, 1.0, True)
    assert result == pytest.approx(0.5)


def test_partial_then_final_target_combines_both_legs() -> None:
    candles = frame(
        [
            {"end": "2026-01-01T10:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"end": "2026-01-01T11:00:00Z", "open": 100, "high": 101.2, "low": 99.5, "close": 101.0},
            {"end": "2026-01-01T12:00:00Z", "open": 101, "high": 102.1, "low": 100.5, "close": 102.0},
        ]
    )
    _, result = simulate_partial_exit(candles, 0, 1, 99.0, 2.0, 4, 0.5, 1.0, True)
    assert result == pytest.approx(1.5)


def test_ambiguous_bar_uses_conservative_stop_first_ordering() -> None:
    candles = frame(
        [
            {"end": "2026-01-01T10:00:00Z", "open": 100, "high": 101, "low": 99, "close": 100},
            {"end": "2026-01-01T11:00:00Z", "open": 100, "high": 102.2, "low": 98.8, "close": 101},
        ]
    )
    _, result = simulate_partial_exit(candles, 0, 1, 99.0, 2.0, 4, 0.5, 1.0, True)
    assert result == pytest.approx(-1.0)
