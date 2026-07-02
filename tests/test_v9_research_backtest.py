import pandas as pd

from mt5_ai_bridge.v9_research_backtest import (
    calculate_metrics,
    replay,
    v8_filter,
    v9_filter,
)


def _candidate(order, engine, hour, pnl_r, risk=0.25, symbol="GBPUSD"):
    entry = pd.Timestamp(f"2026-01-0{order + 1} {hour:02d}:00:00", tz="UTC")
    return {
        "candidate_order": order,
        "symbol": symbol,
        "engine": engine,
        "setup": "TEST",
        "side": 1,
        "entry_time": entry,
        "exit_time": entry + pd.Timedelta(minutes=15),
        "risk_percent": risk,
        "r_multiple": pnl_r,
        "source": "test",
    }


def test_v9_blocks_only_configured_gbpusd_satellite_hours():
    blocked = pd.Series(_candidate(0, "GBPUSD_SATELLITE_V2", 9, -1.0))
    allowed = pd.Series(_candidate(1, "GBPUSD_SATELLITE_V2", 10, 1.0))
    other_engine = pd.Series(_candidate(2, "EURUSD_SATELLITE_V7", 9, 1.0, symbol="EURUSD"))
    assert not v9_filter(blocked)
    assert v9_filter(allowed)
    assert v9_filter(other_engine)


def test_hour_gate_improves_the_synthetic_portfolio():
    candidates = pd.DataFrame([
        _candidate(0, "GBPUSD_SATELLITE_V2", 9, -1.0),
        _candidate(1, "GBPUSD_SATELLITE_V2", 10, 2.0),
        _candidate(2, "EURUSD_SATELLITE_V7", 9, 1.0, symbol="EURUSD"),
    ])
    v8_trades, _, v8_realized, v8_stress = replay(candidates, v8_filter)
    v9_trades, rejected, v9_realized, v9_stress = replay(candidates, v9_filter)
    v8 = calculate_metrics(v8_trades, v8_realized, v8_stress)
    v9 = calculate_metrics(v9_trades, v9_realized, v9_stress)
    assert v9.net_profit > v8.net_profit
    assert v9.profit_factor > v8.profit_factor
    assert (rejected["reason"] == "strategy_hour_filter").sum() == 1
