from __future__ import annotations

import pandas as pd

from mt5_ai_bridge.v14_3_profit_preserving_profile import (
    PORTFOLIO_GUARD,
    SETUP_RISK_PERCENT,
    SYMBOL_GUARDS,
    scaled_risk_percent,
)
from research.v14_3_profit_preserving_backtest import ResearchReplay


def ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC")


def v12_rows() -> pd.DataFrame:
    engines = {
        "GBPUSD": "GBPUSD_V10_PRECISION",
        "EURUSD": "EURUSD_SWING_CORE",
        "GBPJPY": "GBPJPY_SWING_CORE",
        "AUDUSD": "AUDUSD_TREND_PULLBACK",
        "USDJPY": "USDJPY_SAFE_HAVEN_BREAKOUT",
    }
    rows = []
    for index, (symbol, engine) in enumerate(engines.items(), start=1):
        rows.append({
            "entry_time": ts(f"2020-01-{index:02d} 08:00:00"),
            "exit_time": ts(f"2020-01-{index:02d} 12:00:00"),
            "symbol": symbol,
            "engine": engine,
            "setup": "V12_FINAL",
            "side": "BUY",
            "risk_percent": 0.10,
            "r_multiple": 1.0,
        })
    return pd.DataFrame(rows)


def ict_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "entry_time": ts("2023-01-02 09:00:00"),
            "exit_time": ts("2023-01-02 10:00:00"),
            "symbol": "GBPJPY",
            "engine": "ICT_V14_3",
            "setup": "sweep_reclaim_15",
            "side": "BUY",
            "r_multiple": -1.0,
        },
        {
            "entry_time": ts("2023-01-02 09:15:00"),
            "exit_time": ts("2023-01-02 10:15:00"),
            "symbol": "GBPJPY",
            "engine": "ICT_V14_3",
            "setup": "sweep_reclaim_15",
            "side": "BUY",
            "r_multiple": 1.0,
        },
        {
            "entry_time": ts("2023-01-03 10:00:00"),
            "exit_time": ts("2023-01-03 11:00:00"),
            "symbol": "GBPUSD",
            "engine": "ICT_V14_3",
            "setup": "breakout_60_fade",
            "side": "SELL",
            "r_multiple": 1.0,
        },
    ])


def test_profile_keeps_hard_drawdown_below_ten_percent() -> None:
    assert PORTFOLIO_GUARD.hard_drawdown_stop_percent == 9.90
    assert PORTFOLIO_GUARD.max_ict_open_risk_percent == 1.75
    assert PORTFOLIO_GUARD.max_combined_open_risk_percent == 3.25


def test_gbpjpy_retains_single_position_and_post_loss_reduction() -> None:
    guard = SYMBOL_GUARDS["GBPJPY"]
    assert guard.max_open_positions == 1
    assert guard.post_loss_multiplier == 0.70
    assert guard.stop_after_daily_losses == 3
    normal = scaled_risk_percent("GBPJPY", "sweep_reclaim_15", 0.0, False)
    reduced = scaled_risk_percent("GBPJPY", "sweep_reclaim_15", 0.0, True)
    assert normal == SETUP_RISK_PERCENT[("GBPJPY", "sweep_reclaim_15")]
    assert reduced < normal


def test_continuous_drawdown_scaling_reduces_risk() -> None:
    full = scaled_risk_percent("GBPUSD", "breakout_60_fade", 0.0, False)
    middle = scaled_risk_percent("GBPUSD", "breakout_60_fade", 8.0, False)
    floor = scaled_risk_percent("GBPUSD", "breakout_60_fade", 9.47, False)
    assert full > middle > floor
    assert floor == PORTFOLIO_GUARD.drawdown_risk_floor_percent


def test_replay_keeps_all_v12_symbols_and_blocks_overlapping_gbpjpy() -> None:
    summary, trades, skipped = ResearchReplay(v12_rows(), ict_rows()).run()
    v12 = trades[trades["engine_group"] == "V12"]
    assert set(v12["symbol"]) == {"GBPUSD", "EURUSD", "GBPJPY", "AUDUSD", "USDJPY"}
    assert "SYMBOL_OPEN_POSITION_LIMIT" in set(skipped["skip_reason"])
    assert summary["by_symbol"]["GBPUSD"]["trades"] >= 1
    assert summary["by_symbol"]["GBPJPY"]["trades"] >= 1
