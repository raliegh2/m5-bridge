from __future__ import annotations

import pandas as pd
import pytest

from mt5_ai_bridge.v14_7_strategy_families import (
    SYMBOLS,
    StrategySpec,
    simulate_exit,
    strategy_specs,
)


def test_every_symbol_has_multiple_swing_and_ict_families() -> None:
    for symbol in SYMBOLS:
        specs = strategy_specs(symbol)
        swing = [item for item in specs if item.mode == "SWING"]
        ict = [item for item in specs if item.mode == "ICT"]
        assert len(swing) >= 6
        assert len(ict) >= 10
        assert {item.family for item in swing} >= {
            "TREND_PULLBACK",
            "BREAKOUT",
            "VOLATILITY_BREAKOUT",
            "EMA_RECLAIM",
        }
        assert {item.family for item in ict} >= {
            "TREND_PULLBACK",
            "BREAKOUT",
            "PREVIOUS_DAY_SWEEP",
            "SESSION_BREAKOUT",
            "FVG_CONTINUATION",
        }


def _frame(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    return pd.DataFrame(
        [
            {
                "time": start + pd.Timedelta(hours=index),
                "end": start + pd.Timedelta(hours=index + 1),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
            }
            for index, (open_, high, low, close) in enumerate(rows)
        ]
    )


def test_partial_exit_preserves_full_winner_payoff() -> None:
    spec = StrategySpec(
        "TEST", "ICT", "BREAKOUT", "H1", 5, 13, 34, 4,
        0.0, 0.0, 0.0, 1.0, 1.5, 4,
        partial_fraction=0.50,
        partial_target_r=1.0,
        move_to_break_even=True,
    )
    frame = _frame(
        [
            (100.0, 100.2, 99.8, 100.0),
            (100.0, 101.1, 99.9, 101.0),
            (101.0, 101.6, 100.8, 101.4),
        ]
    )
    _, result = simulate_exit(frame, 0, 1, 99.0, spec)
    assert result == pytest.approx(1.25)


def test_stop_is_assumed_before_target_inside_ambiguous_bar() -> None:
    spec = StrategySpec(
        "TEST", "ICT", "BREAKOUT", "H1", 5, 13, 34, 4,
        0.0, 0.0, 0.0, 1.0, 1.5, 3,
        partial_fraction=0.50,
        partial_target_r=1.0,
        move_to_break_even=True,
    )
    frame = _frame(
        [
            (100.0, 100.1, 99.9, 100.0),
            (100.0, 102.0, 98.5, 101.0),
        ]
    )
    _, result = simulate_exit(frame, 0, 1, 99.0, spec)
    assert result == pytest.approx(-1.0)


def test_entry_uses_next_bar_open_for_risk_geometry() -> None:
    spec = StrategySpec(
        "TEST", "SWING", "BREAKOUT", "H4", 10, 20, 50, 12,
        0.0, 0.0, 0.0, 1.0, 2.0, 2,
    )
    frame = _frame(
        [
            (100.0, 100.5, 99.5, 100.4),
            (102.0, 104.0, 101.5, 103.5),
        ]
    )
    # The signal closes near 100, but the next bar opens at 102. With a stop at
    # 100, the risk is 2 and the 2R target is 106, so the bar does not hit it.
    _, result = simulate_exit(frame, 0, 1, 100.0, spec)
    assert result == pytest.approx(0.75)
